"""What the tests share: the lake's half on the SDK's `Recorder`, Alpaca on the
built-in synthetic exchange, a clock the tests set, and the deployment's
records as the instrument store would answer them."""

from __future__ import annotations

import datetime as dt
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any

import pytest
from meridian.plugin.v1 import operations_pb2 as ops
from meridian.suites import Recorder

from alpaca.convert import NEW_YORK
from alpaca.lake import Lake, Products
from alpaca.raw import RawStore
from alpaca.source import AlpacaSource
from alpaca.synthetic import SyntheticTransport, exchange
from alpaca.vendor import NS, Alpaca, RateLimiter

SPY, BRK, TQQQ, ISIN_ONLY = "LCL-SPY", "LCL-BRK", "LCL-TQQQ", "LCL-ISIN"
OTHER = "snaptrade"

#: The deployment's records, as ResolveInstrument answers them: SPY with this
#: source's symbol, BRK.B and TQQQ with another source's (BRK.B in its
#: spelling), and one record with no symbol at all.
RECORDS: dict[str, list[ops.Identifier]] = {
    SPY: [ops.Identifier(scheme="symbol", value="SPY", source="alpaca")],
    BRK: [ops.Identifier(scheme="symbol", value="BRK/B", source=OTHER)],
    TQQQ: [ops.Identifier(scheme="symbol", value="TQQQ", source=OTHER)],
    ISIN_ONLY: [ops.Identifier(scheme="isin", value="US0000000001")],
}


def at(day: str, hour: int, minute: int = 0) -> int:
    """A New York wall-clock moment, in UTC nanoseconds."""
    moment = dt.datetime.fromisoformat(day).replace(hour=hour, minute=minute, tzinfo=NEW_YORK)
    return int(moment.timestamp()) * NS


class Clock:
    def __init__(self, now: int) -> None:
        self.now = now

    def __call__(self) -> int:
        return self.now


def no_wait() -> RateLimiter:
    return RateLimiter(per_minute=6_000_000, sleep=lambda _: None)


def answer_records(recorder: Recorder, records: Mapping[str, Sequence[ops.Identifier]]) -> None:
    """The instrument store's answer to ResolveInstrument, from `records`."""

    def reply(params: Any) -> ops.ResolveInstrumentResult:
        held = records.get(params.instrument_id)
        if held is None:
            return ops.ResolveInstrumentResult(found=False)
        return ops.ResolveInstrumentResult(
            found=True,
            instrument=ops.InstrumentRecord(
                instrument_id=params.instrument_id, identifiers=list(held), currency="USD"
            ),
        )

    recorder.answer("ResolveInstrument", reply)

    def joined(params: Any) -> ops.ResolveIdentifierResult:
        # One identifier in common with a record is that record.
        for entity, held in records.items():
            for given in params.identifiers:
                if any(
                    (h.scheme, h.value, h.source) == (given.scheme, given.value, given.source)
                    for h in held
                ):
                    return ops.ResolveIdentifierResult(found=True, instrument_id=entity)
        return ops.ResolveIdentifierResult(found=True, instrument_id="LCL-minted", minted=True)

    recorder.answer("ResolveIdentifier", joined)


def synthetic_lake(
    recorder: Any,
    clock: Clock,
    tmp: Path,
    held: Mapping[str, Any] | None = None,
    parse: Callable[[str], Any] | None = None,
) -> Lake:
    store = RawStore(tmp / "raw")
    alpaca = Alpaca(
        store,
        None,
        transport=SyntheticTransport(held if held is not None else exchange()),
        limiter=no_wait(),
        clock_ns=clock,
        keyless=True,
    )
    if parse is not None:
        import alpaca.vendor as vendor

        vendor.parse = parse  # type: ignore[assignment]
    return Lake(
        recorder, AlpacaSource(alpaca, synthetic=True), Products(tmp / "products.json"), clock
    )


def want(
    *subjects: str,
    dataset: str = "suite-plugin:daily",
    data_type: str = "meridian.v1.Price",
    kinds: Sequence[ops.PriceKind] = (),
    business_date: str = "",
    standing: bool = False,
    want_id: str = "WNT-1",
    interval_ns: int = 0,
) -> ops.ObservationsWantedEvent:
    return ops.ObservationsWantedEvent(
        want_id=want_id,
        dataset=dataset,
        data_type=data_type,
        subjects=[ops.SubjectRef(entity_id=s) for s in subjects],
        kinds=list(kinds),
        business_date=business_date,
        standing=standing,
        interval_ns=interval_ns,
    )


@pytest.fixture
def restore_parse() -> Any:
    import alpaca.vendor as vendor

    held = vendor.parse
    yield
    vendor.parse = held
