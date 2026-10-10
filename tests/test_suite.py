"""The `dgm` suite, the SDK's own (meridian.suites, contract v18), run unchanged
against this plugin: every case mapped to the plugin's own conversion, from
Alpaca's synthetic exchange, through the lake's half it runs in production
(lake.py), against the suite's `Recorder`.

Two cases describe what Alpaca's US equity data never presents: a currency's
rate against another currency, and a crypto asset quoted in a stablecoin.
The suite has no way to say a source does not present a case unless it
asserts one value of a closed list, and these do not; so each is mapped to
the plugin's own recording path, with the row the plugin would send were its
source to state one. That proves the plugin names a cash instrument and a
token's own instrument as the contract has them, not that Alpaca serves
either. The same holds for meridian-tradier, and is raised with the lake's
plan.
"""

from __future__ import annotations

import datetime as dt
import uuid
from collections.abc import Awaitable, Callable
from decimal import Decimal
from pathlib import Path

import meridian
from meridian import Money, ObservationMeta, Price, SourceTime
from meridian.plugin.v1 import operations_pb2 as ops
from meridian.suites import Recorder, run

from alpaca.lake import DAILY, LIVE_KINDS, PRICE, Standing

from conftest import (
    BRK,
    ISIN_ONLY,
    RECORDS,
    SPY,
    TQQQ,
    Clock,
    answer_records,
    at,
    synthetic_lake,
    want,
)

DAY = "2026-10-08"
#: Saturday morning after the synthetic week: every day of it final.
AFTER = at("2026-10-10", 9)
#: Thursday 2026-10-09, 15:00 New York: the session still open.
DURING = at("2026-10-09", 15)

Producer = Callable[[Recorder], Awaitable[None]]


def producers(tmp: Path) -> dict[str, Producer]:
    def lake(recorder: Recorder, now: int = AFTER):  # type: ignore[no-untyped-def]
        answer_records(recorder, RECORDS)
        return synthetic_lake(recorder, Clock(now), tmp / uuid.uuid4().hex)

    async def wanted(recorder: Recorder, event: ops.ObservationsWantedEvent) -> None:
        held = lake(recorder)
        recorder.answer("ObservationsWanted", lambda _: event)
        await recorder.receive(observations_wanted=held.on_want)

    async def daily_close(recorder: Recorder) -> None:
        # The plugin resolves a listing by its own symbol, then records the
        # close the lake wants for it.
        held = lake(recorder)
        await held.seed(["BRK.B"])
        (subject,) = held.products.symbols
        recorder.answer("ObservationsWanted", lambda _: want(subject, business_date=DAY))
        await recorder.receive(observations_wanted=held.on_want)

    async def forming(recorder: Recorder) -> None:
        clock = Clock(DURING)
        answer_records(recorder, RECORDS)
        held = synthetic_lake(recorder, clock, tmp / "forming")
        await held.daily([SPY], dt.date(2026, 10, 9), dt.date(2026, 10, 9))
        clock.now = at("2026-10-09", 16, 30)
        await held.tick()

    async def exact(recorder: Recorder) -> None:
        # TQQQ's close on 2026-10-09 is stated to eighteen places.
        await wanted(recorder, want(TQQQ, business_date="2026-10-09"))

    async def bar(recorder: Recorder) -> None:
        # BRK.B's bars state no VWAP.
        await wanted(recorder, want(BRK, data_type="meridian.v1.Bar", business_date=DAY))

    async def fx(recorder: Recorder) -> None:
        # Not presented by Alpaca's equity data (above): the plugin's
        # recording path, given the rate its source would state.
        held = lake(recorder)
        eur = await recorder.resolve_identifier(
            identifiers=[meridian.Identifier(scheme="iso4217", value="EUR")]
        )
        await held._record(
            DAILY, PRICE, [_row(held, eur.instrument_id, Money(Decimal("1.0921"), "USD"))], ""
        )

    async def stablecoin(recorder: Recorder) -> None:
        # Not presented by Alpaca's equity data (above).
        held = lake(recorder)
        btc = await recorder.resolve_identifier(
            identifiers=[meridian.Identifier(scheme="symbol", value="BTC", source="alpaca")]
        )
        usdc = await recorder.resolve_identifier(
            identifiers=[meridian.Identifier(scheme="symbol", value="USDC", source="alpaca")]
        )
        money = Money(Decimal("62431.27"), instrument_id=usdc.instrument_id)
        await held._record(DAILY, PRICE, [_row(held, btc.instrument_id, money)], "")

    async def ambiguous(recorder: Recorder) -> None:
        # The listing's symbol meets two of the deployment's records.
        held = lake(recorder)
        recorder.answer(
            "ResolveIdentifier",
            lambda _: ops.ResolveIdentifierResult(
                found=False, miss_reason=ops.MISS_REASON_AMBIGUOUS
            ),
        )
        await held.seed(["TQQQ"])

    async def venue(recorder: Recorder) -> None:
        # SPY's latest trade printed on IEX, Alpaca's letter V, which the
        # venue master holds as Alpaca's code.
        await wanted(recorder, want(SPY, dataset="suite-plugin:live"))

    async def venue_not_held(recorder: Recorder) -> None:
        held = lake(recorder)
        recorder.answer(
            "ResolveVenue",
            lambda _: ops.ResolveVenueResult(found=False, miss_reason=ops.MISS_REASON_NOT_FOUND),
        )
        recorder.answer("ObservationsWanted", lambda _: want(SPY, dataset="suite-plugin:live"))
        await recorder.receive(observations_wanted=held.on_want)

    async def recorded_against(recorder: Recorder) -> None:
        await wanted(recorder, want(SPY, business_date=DAY))

    async def elsewhere(recorder: Recorder) -> None:
        # TQQQ as a custodian's book holds it, by its CUSIP and the
        # custodian's symbol: the record read (W3.6), Alpaca's symbol found.
        await wanted(recorder, want(TQQQ, business_date=DAY))

    async def declined(recorder: Recorder) -> None:
        await wanted(recorder, want(ISIN_ONLY, business_date=DAY))

    async def withdrawn(recorder: Recorder) -> None:
        clock = Clock(AFTER)
        answer_records(recorder, RECORDS)
        held = synthetic_lake(recorder, clock, tmp / "withdrawn")
        held.standing["WNT-9"] = Standing("WNT-9", (SPY,), LIVE_KINDS, AFTER)
        recorder.answer(
            "WantWithdrawn",
            lambda _: [ops.WantWithdrawnEvent(want_id="WNT-9", dataset="suite-plugin:live")],
        )
        await recorder.receive(want_withdrawn=held.on_withdrawn)
        clock.now += 3600 * 10**9
        await held.tick()

    return {
        "a-daily-close": daily_close,
        "the-forming-day-restated": forming,
        "an-exact-price": exact,
        "a-daily-bar": bar,
        "an-fx-rate": fx,
        "a-stablecoin-quote": stablecoin,
        "an-asset-that-does-not-resolve": ambiguous,
        "a-venue-resolved": venue,
        "a-venue-not-held": venue_not_held,
        "a-want-recorded-against": recorded_against,
        "a-subject-another-source-resolved": elsewhere,
        "a-subject-declined": declined,
        "a-standing-want-withdrawn": withdrawn,
    }


def _row(held, subject: str, money: Money) -> Price:  # type: ignore[no-untyped-def]
    day = dt.date(2026, 10, 8)
    return Price(
        meta=ObservationMeta(
            row_key=f"{subject}:1d:{day}",
            subjects=[ops.SubjectRef(entity_id=subject)],
            source=ops.Source(dataset=held.dataset(DAILY)),
            valid_from_ns=at("2026-10-07", 16),
            valid_until_ns=at("2026-10-08", 16),
            business_date=day,
            source_times=[SourceTime(kind="published", at_ns=at("2026-10-08", 16))],
            raw=held.plugin.raw_record("not-presented"),
        ),
        kind="close",
        price=money,
        basis="per_unit",
    )


def test_the_dgm_suite_passes_unchanged(tmp_path: Path) -> None:
    report = run("dgm", producers(tmp_path))
    assert report.passed, report.failures
    assert len(report.passed_cases) == 13


def test_a_price_read_through_a_float_fails_the_suite(tmp_path: Path, restore_parse: None) -> None:
    import json

    import alpaca.vendor as vendor

    vendor.parse = lambda text: json.loads(text, parse_float=float)
    report = run("dgm", producers(tmp_path))
    assert "an-exact-price" in report.failures, report.failures
