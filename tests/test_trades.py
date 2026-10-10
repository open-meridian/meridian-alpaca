"""Trades and quotes in the lake's half (contract v19), what the suite does not
reach: the platform's attributes exactly as converted on each tape, a
withdrawal and a correction as the trade's next versions, quotes at most one
a second per listing, trades only for listings under a standing want, the
key's symbol limit, the gap after a reconnect caught up with nothing sent
twice, a range pulled; and the carried fix, one row an observation however
many records of its listing the deployment holds."""

from __future__ import annotations

import datetime as dt
import json
from decimal import Decimal
from pathlib import Path
from typing import Any

from meridian.operations import as_decimal
from meridian.plugin.v1 import operations_pb2 as ops
from meridian.suites import Recorder

from alpaca import conditions, convert
from alpaca.lake import TRADES, Lake
from alpaca.vendor import NS

from conftest import BRK, RECORDS, SPY, TQQQ, Clock, answer_records, at, synthetic_lake, want

DURING = at("2026-10-09", 15, 30)
NUMBERS = frozenset({"i", "p", "s", "bp", "bs", "ap", "as", "oi", "op", "os", "ci", "cp", "cs"})


def message(**fields: Any) -> dict[str, Any]:
    said = {"t": "2026-10-09T19:30:00.5Z", "z": "B", "x": "V", **fields}
    text = (
        "{"
        + ",".join(
            f"{json.dumps(k)}:{v if k in NUMBERS else json.dumps(v)}" for k, v in said.items()
        )
        + "}"
    )
    return dict(json.loads(text, parse_float=Decimal))


def made(tmp: Path, now: int = DURING) -> tuple[Recorder, Lake, Clock]:
    recorder = Recorder("alpaca-1")
    answer_records(recorder, RECORDS)
    clock = Clock(now)
    return recorder, synthetic_lake(recorder, clock, tmp), clock


async def stand(recorder: Recorder, lake: Lake, *subjects: str, want_id: str = "WNT-T") -> None:
    event = want(
        *subjects,
        dataset="alpaca-1:trades",
        data_type="meridian.v1.Trade",
        standing=True,
        want_id=want_id,
    )
    recorder.answer("ObservationsWanted", lambda _: event)
    await recorder.receive(observations_wanted=lake.on_want)


async def hear(lake: Lake, *messages: dict[str, Any]) -> None:
    for n, said in enumerate(messages):
        await lake.heard(convert.streamed(said, f"stream/iex/t#{n}", "iex"))


def trades(recorder: Recorder) -> list[ops.Trade]:
    return [t for sent in recorder.on("RecordTrades") for t in sent.trades]


def quotes(recorder: Recorder) -> list[ops.Quote]:
    return [q for sent in recorder.on("RecordQuotes") for q in sent.quotes]


def eligible(e: ops.Eligibility) -> tuple[str, ...]:
    return tuple(
        ops.Eligible.Name(getattr(e, f)).removeprefix("ELIGIBLE_")
        for f in ("high_low", "open", "close", "volume")
    )


async def test_a_regular_trade_is_recorded_with_every_eligibility_and_no_aggressor(
    tmp_path: Path,
) -> None:
    recorder, lake, _ = made(tmp_path)
    await stand(recorder, lake, SPY)
    await hear(lake, message(T="t", S="SPY", i=52983525029461, p="575.44", s=100, c=["@"]))
    await lake.flush(force=True)
    (trade,) = trades(recorder)
    assert (
        as_decimal(trade.price.amount) == Decimal("575.44") and trade.price.currency_code == "USD"
    )
    assert as_decimal(trade.quantity) == Decimal("100")
    assert trade.aggressor == ops.AGGRESSOR_UNSPECIFIED and not trade.cancelled
    assert eligible(trade.attributes.consolidated) == ("ELIGIBLE",) * 4
    assert eligible(trade.attributes.market_centre) == ("ELIGIBLE",) * 4
    assert list(trade.attributes.characteristics) == []
    meta = trade.meta
    assert meta.row_key == f"SPY:t:2026-10-09:V:52983525029461@{SPY}"
    assert meta.source.dataset == "alpaca-1:trades" and meta.source.venue_id.startswith("VEN-")
    assert meta.valid_from_ns == at("2026-10-09", 15, 30) + NS // 2
    assert not meta.business_date and meta.raw.key == "stream/iex/t#0"
    assert [s.entity_id for s in meta.subjects] == [SPY]


def test_each_code_is_read_on_its_own_tape_and_codes_combine() -> None:
    # `B` is an average price trade on the CTA's tapes, a bunched trade on
    # the UTP's.
    cta = conditions.trade_attributes(["B"], "A")
    utp = conditions.trade_attributes(["B"], "C")
    assert cta.characteristics == ("average_price",) and cta.consolidated == (
        False,
        False,
        False,
        True,
    )
    assert utp.characteristics == () and utp.consolidated == (True, True, True, True)
    both = conditions.trade_attributes(["F", "I"], "C")
    assert both.characteristics == ("intermarket_sweep", "odd_lot")
    assert both.consolidated == (False, False, False, True)
    official = conditions.trade_attributes(["M"], "B")
    assert official.consolidated == (False,) * 4 and official.market_centre == (
        False,
        False,
        True,
        False,
    )
    unknown = conditions.trade_attributes(["@", "~"], "B")
    assert unknown.consolidated == conditions.NOT_KNOWN
    assert [(u.scheme, u.code, u.text) for u in unknown.unconverted] == [
        ("alpaca:trade.c", "~", "tape B")
    ]


async def test_a_withdrawal_and_a_correction_are_the_trades_next_versions(tmp_path: Path) -> None:
    recorder, lake, _ = made(tmp_path)
    await stand(recorder, lake, SPY)
    await hear(lake, message(T="t", S="SPY", i=1, p="575.40", s=100, c=["@", "I"]))
    await lake.flush(force=True)
    await hear(
        lake,
        message(T="x", S="SPY", i=1, p="575.40", s=100, a="C"),
        message(T="t", S="SPY", i=2, p="575.41", s=200, c=["@"]),
        message(
            T="c", S="SPY", oi=2, op="575.41", os=200, oc=["@"], ci=3, cp="575.42", cs=200, cc=["@"]
        ),
    )
    await lake.flush(force=True)
    rows = trades(recorder)
    by = [(t.meta.row_key.split("@")[0].rsplit(":", 1)[1], t.cancelled) for t in rows]
    assert by == [("1", False), ("1", True), ("2", False), ("2", True), ("3", False)]
    withdrawn = rows[1]
    # The trade as recorded, withdrawn: its attributes kept, its raw record the cancel's.
    assert list(withdrawn.attributes.characteristics) == [ops.TRADE_CHARACTERISTIC_ODD_LOT]
    assert withdrawn.meta.raw.key == "stream/iex/t#0" and rows[0].meta.raw.key == "stream/iex/t#0"
    assert lake.counts[TRADES].withdrawn == 2


async def test_trades_are_recorded_only_for_listings_under_a_standing_want(tmp_path: Path) -> None:
    recorder, lake, _ = made(tmp_path)
    await hear(lake, message(T="t", S="SPY", i=1, p="575.40", s=100, c=["@"]))
    await lake.flush(force=True)
    assert not trades(recorder)
    await stand(recorder, lake, SPY)
    assert lake.streamed == ["SPY"]
    recorder.answer("WantWithdrawn", lambda _: [ops.WantWithdrawnEvent(want_id="WNT-T")])
    await recorder.receive(want_withdrawn=lake.on_withdrawn)
    assert lake.streamed == []


async def test_quotes_are_recorded_at_most_once_a_second_the_latest(tmp_path: Path) -> None:
    recorder, lake, clock = made(tmp_path)
    await stand(recorder, lake, SPY, BRK)
    for n, bid in enumerate(("575.41", "575.42", "575.43")):
        await hear(
            lake,
            message(
                T="q",
                S="SPY",
                bx="V",
                bp=bid,
                bs=100,
                ax="V",
                ap="575.46",
                **{"as": 300},
                c=["R"],
                t=f"2026-10-09T19:30:0{n}Z",
            ),
        )
    await hear(
        lake,
        message(
            T="q", S="BRK.B", bx="V", bp="455.30", bs=100, ax="V", ap=0, **{"as": 0}, c=["N"], z="A"
        ),
    )
    clock.now += 2 * NS
    await lake.flush()
    spy, brk = sorted(quotes(recorder), key=lambda q: q.meta.row_key, reverse=True)
    assert as_decimal(spy.bid.amount) == Decimal("575.43") and as_decimal(spy.ask_quantity) == 300
    assert spy.meta.source.dataset == "alpaca-1:live" and spy.meta.source.venue_id.startswith(
        "VEN-"
    )
    assert brk.HasField("bid") and not brk.HasField("ask") and not brk.HasField("ask_quantity")
    assert list(brk.characteristics) == [ops.QUOTE_CHARACTERISTIC_INDICATIVE]
    await lake.flush()
    assert len(quotes(recorder)) == 2


async def test_listings_past_the_keys_limit_are_declined_not_entitled(tmp_path: Path) -> None:
    recorder, lake, _ = made(tmp_path)
    lake.stream_limit = 1
    await stand(recorder, lake, SPY, want_id="WNT-1")
    await stand(recorder, lake, BRK, want_id="WNT-2")
    assert lake.streamed == ["SPY"] and lake.past_limit == {"BRK.B"}
    (declined,) = recorder.on("DeclineWant")
    assert declined.want_id == "WNT-2" and declined.reason == ops.UNANSWERED_REASON_NOT_ENTITLED


async def test_the_gap_after_a_reconnect_is_caught_up_and_nothing_sent_twice(
    tmp_path: Path,
) -> None:
    recorder, lake, clock = made(tmp_path, at("2026-10-10", 9))
    await stand(recorder, lake, SPY)
    # The stream had the earlier trade; the history holds it and a later one.
    await hear(
        lake,
        message(
            T="t", S="SPY", i=52983525029401, p="575.40", s=100, c=["@"], t="2026-10-09T19:59:58.1Z"
        ),
    )
    await lake.flush(force=True)
    since = {"SPY": convert.parse_time("2026-10-09T19:59:58.1Z")}
    assert await lake.caught_up(since) == 1
    keys = [t.meta.row_key for t in trades(recorder)]
    assert keys == [
        f"SPY:t:2026-10-09:V:52983525029401@{SPY}",
        f"SPY:t:2026-10-09:V:52983525029461@{SPY}",
    ]
    assert lake.counts[TRADES].caught_up == 1 and lake.counts[TRADES].unchanged == 1
    assert trades(recorder)[1].meta.raw.key.startswith("trades/iex/")


async def test_a_range_of_trades_a_reader_wants_is_pulled_against_the_want(tmp_path: Path) -> None:
    recorder, lake, _ = made(tmp_path, at("2026-10-10", 9))
    event = want(SPY, dataset="alpaca-1:trades", data_type="meridian.v1.Trade")
    event.valid_from_ns = at("2026-10-09", 15, 59)
    event.valid_until_ns = at("2026-10-09", 16)
    recorder.answer("ObservationsWanted", lambda _: event)
    await recorder.receive(observations_wanted=lake.on_want)
    (sent,) = recorder.on("RecordTrades")
    assert sent.want_id == "WNT-1" and len(sent.trades) == 2


async def test_two_records_of_one_listing_each_have_their_own_rows(tmp_path: Path) -> None:
    # The carried fix: the deployment holds TQQQ as this source resolved it
    # and as a custodian's book holds it; each record's close and trade under
    # its own key, never one recorded over the other as a restatement.
    own = "LCL-TQQQ-OWN"
    recorder, lake, _ = made(tmp_path, at("2026-10-10", 9))
    lake.products.put(own, "TQQQ")
    await lake.daily([TQQQ, own], dt.date(2026, 10, 8), dt.date(2026, 10, 8))
    await lake.daily([own], dt.date(2026, 10, 8), dt.date(2026, 10, 8))
    rows = [p for sent in recorder.on("RecordPrices") for p in sent.prices]
    keys = [p.meta.row_key for p in rows]
    assert keys == [f"TQQQ:1d:2026-10-08@{TQQQ}", f"TQQQ:1d:2026-10-08@{own}"] * 1 + [
        f"TQQQ:1d:2026-10-08@{own}"
    ]
    assert [[s.entity_id for s in p.meta.subjects] for p in rows] == [[TQQQ], [own], [own]]
    # Read again unchanged: the same raw record, so the lake keeps it unchanged.
    assert rows[1] == rows[2]
    await stand(recorder, lake, TQQQ, own)
    await hear(lake, message(T="t", S="TQQQ", i=5, p="82.41", s=300, c=["@", "T"], z="C"))
    await lake.flush(force=True)
    made_rows = trades(recorder)
    assert [[s.entity_id for s in t.meta.subjects] for t in made_rows] == [[TQQQ], [own]]
    assert len({t.meta.row_key for t in made_rows}) == 2
    trade = made_rows[0]
    assert list(trade.attributes.characteristics) == [ops.TRADE_CHARACTERISTIC_EXTENDED_HOURS]
