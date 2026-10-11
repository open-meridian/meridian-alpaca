"""The lake's half (lake.py) on Alpaca's synthetic exchange, what the suite
does not reach: the rows exactly as recorded, a forming day restated under
one key until final, the push of a final close, a standing want kept current
and stopped, the subject's symbol from another source joined and kept, a
venue the deployment does not hold kept as reported, and what does not
convert counted, never recorded."""

from __future__ import annotations

import datetime as dt
from decimal import Decimal
from pathlib import Path
from typing import Any

from meridian.operations import as_decimal
from meridian.plugin.v1 import operations_pb2 as ops
from meridian.suites import Recorder

from alpaca.lake import LIVE_CADENCE_SECONDS, Lake, Products

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

AFTER = at("2026-10-10", 9)
D8 = dt.date(2026, 10, 8)


def on(recorder: Recorder, row: str) -> list[Any]:
    """What the plugin sent on a row, as the wire's messages."""
    return list(recorder.on(row))


def made(tmp: Path, now: int = AFTER) -> tuple[Recorder, Lake, Clock]:
    recorder = Recorder("alpaca-1")
    answer_records(recorder, RECORDS)
    clock = Clock(now)
    return recorder, synthetic_lake(recorder, clock, tmp), clock


def prices(recorder: Recorder) -> list[ops.Price]:
    return [p for sent in on(recorder, "RecordPrices") for p in sent.prices]


async def test_a_close_is_its_bars_close_exactly_on_its_new_york_day(tmp_path: Path) -> None:
    recorder, lake, _ = made(tmp_path)
    await lake.daily([SPY], D8, D8)
    (price,) = prices(recorder)
    assert price.kind == ops.PRICE_KIND_CLOSE
    assert as_decimal(price.price.amount) == Decimal("573.78")
    assert price.price.currency_code == "USD"
    meta = price.meta
    assert meta.business_date == "2026-10-08"
    assert meta.row_key == f"SPY:1d:2026-10-08@{SPY}"
    assert meta.source.dataset == "alpaca-1:daily" and meta.source.venue_id == ""
    assert meta.valid_from_ns == at("2026-10-07", 16)
    assert meta.valid_until_ns == at("2026-10-08", 16)
    assert [(t.kind, t.at_ns) for t in meta.source_times] == [
        (ops.SOURCE_TIME_KIND_PUBLISHED, at("2026-10-08", 16))
    ]
    assert meta.raw.key.startswith("bars/sip/1Day/") and meta.raw.key.endswith("#SPY/0")


async def test_a_bar_keeps_vwap_and_trade_count_where_alpaca_states_them(tmp_path: Path) -> None:
    recorder, lake, _ = made(tmp_path)
    await lake.daily([SPY, BRK], D8, D8, data_type="meridian.v1.Bar")
    bars = {b.meta.row_key: b for sent in on(recorder, "RecordBars") for b in sent.bars}
    spy, brk = bars[f"SPY:1d:2026-10-08@{SPY}"], bars[f"BRK.B:1d:2026-10-08@{BRK}"]
    assert spy.HasField("vwap") and spy.trade_count == 512375
    assert as_decimal(spy.volume) == Decimal("61237567")
    assert not brk.HasField("vwap")


async def test_a_forming_day_is_restated_under_one_key_until_final(tmp_path: Path) -> None:
    recorder, lake, clock = made(tmp_path, at("2026-10-09", 15))
    day = dt.date(2026, 10, 9)
    await lake.daily([SPY], day, day)
    clock.now = at("2026-10-09", 16, 5)
    await lake.tick()
    clock.now = at("2026-10-10", 9)
    await lake.tick()
    rows = prices(recorder)
    # Forming at 15:00; at 16:05 its close published, read again through the
    # extended session in case it moves (the lake keeps an identical repeat
    # unchanged); past it, final and read no more.
    assert len(rows) == 3
    assert {r.meta.row_key for r in rows} == {f"SPY:1d:2026-10-09@{SPY}"}
    assert not rows[0].meta.source_times  # still forming: not published
    assert rows[1].meta.source_times[0].kind == ops.SOURCE_TIME_KIND_PUBLISHED
    for row in rows:
        row.meta.ClearField("raw")  # each reading is its own raw record
    assert rows[1] == rows[2]
    assert not lake.forming
    await lake.tick()
    assert len(prices(recorder)) == 3


async def test_a_listing_from_another_source_is_joined_spelled_once_and_kept(
    tmp_path: Path,
) -> None:
    recorder, lake, _ = made(tmp_path)
    await lake.daily([BRK], D8, D8)
    (joined,) = on(recorder, "ResolveIdentifier")
    assert [(i.scheme, i.value, i.source) for i in joined.identifiers] == [
        ("symbol", "BRK.B", "alpaca"),
        ("symbol", "BRK/B", "snaptrade"),
    ]
    assert lake.products.symbols[BRK] == "BRK.B"
    again = Lake(recorder, lake.source, Products(tmp_path / "products.json"), lake.clock_ns)
    assert again.products.symbols == {BRK: "BRK.B"}
    await again.daily([BRK], D8, D8)
    assert len(on(recorder, "ResolveInstrument")) == 1  # read back from storage


async def test_a_subject_with_no_symbol_is_declined_and_a_day_without_a_session_silent(
    tmp_path: Path,
) -> None:
    recorder, lake, _ = made(tmp_path)
    holder = Recorder("alpaca-1")
    holder.answer("ObservationsWanted", lambda _: want(ISIN_ONLY, SPY, business_date="2026-10-04"))
    answer_records(holder, RECORDS)
    lake.plugin = holder
    await holder.receive(observations_wanted=lake.on_want)
    declined = {(s.entity_id, d.reason) for d in on(holder, "DeclineWant") for s in d.subjects}
    assert declined == {
        (ISIN_ONLY, ops.UNANSWERED_REASON_NOT_COVERED),
        (SPY, ops.UNANSWERED_REASON_SOURCE_SILENT),  # a Sunday: no session
    }
    assert not on(holder, "RecordPrices")


async def test_a_want_for_a_kind_or_length_it_does_not_serve_is_declined(tmp_path: Path) -> None:
    recorder, lake, _ = made(tmp_path)
    for event in (
        want(SPY, kinds=[ops.PRICE_KIND_NAV]),
        want(SPY, data_type="meridian.v1.Bar", interval_ns=60 * 10**9),
    ):
        recorder.answer("ObservationsWanted", _given(event))
        await recorder.receive(observations_wanted=lake.on_want)
    assert len(on(recorder, "DeclineWant")) == 2
    assert all(d.reason == ops.UNANSWERED_REASON_NOT_COVERED for d in on(recorder, "DeclineWant"))


def _given(event: ops.ObservationsWantedEvent) -> Any:
    return lambda _: event


async def test_a_standing_live_want_is_kept_current_until_withdrawn(tmp_path: Path) -> None:
    recorder, lake, clock = made(tmp_path)
    live = want(SPY, BRK, dataset="alpaca-1:live", standing=True, want_id="WNT-L")
    recorder.answer("ObservationsWanted", lambda _: live)
    await recorder.receive(observations_wanted=lake.on_want)
    first = prices(recorder)
    kinds = sorted(ops.PriceKind.Name(p.kind) for p in first)
    # SPY's last, bid and ask; BRK.B's last and ask, its bid side empty.
    assert kinds == sorted(["PRICE_KIND_LAST"] * 2 + ["PRICE_KIND_BID"] + ["PRICE_KIND_ASK"] * 2)
    assert all(p.meta.business_date == "" and p.meta.valid_until_ns == 0 for p in first)
    clock.now += (LIVE_CADENCE_SECONDS - 1) * 10**9
    await lake.tick()
    assert len(prices(recorder)) == len(first)  # not due yet
    clock.now += 2 * 10**9
    await lake.tick()
    assert len(prices(recorder)) == 2 * len(first)
    recorder.answer("WantWithdrawn", lambda _: [ops.WantWithdrawnEvent(want_id="WNT-L")])
    await recorder.receive(want_withdrawn=lake.on_withdrawn)
    clock.now += 3600 * 10**9
    await lake.tick()
    assert len(prices(recorder)) == 2 * len(first)


async def test_a_live_price_names_its_venue_and_one_not_held_is_kept_as_reported(
    tmp_path: Path,
) -> None:
    recorder, lake, clock = made(tmp_path)
    recorder.answer(
        "ResolveVenue",
        lambda _: ops.ResolveVenueResult(found=False, miss_reason=ops.MISS_REASON_NOT_FOUND),
    )
    await lake.live([SPY])
    await lake.live([SPY])
    rows = prices(recorder)
    assert rows and all(p.meta.source.venue_id == "" for p in rows)
    assert all(
        [(u.scheme, u.code) for u in p.meta.unconverted] == [("alpaca:venue", "V")] for p in rows
    )
    # Reported once, not on every price, until asked again an hour later.
    assert len(on(recorder, "ReportMissingVenue")) == 1
    (missed,) = on(recorder, "ReportMissingVenue")
    assert [(i.scheme, i.value, i.source) for i in missed.identifiers] == [
        ("symbol", "V", "alpaca")
    ]
    held = Recorder("alpaca-1")
    answer_records(held, RECORDS)
    lake.plugin = held
    clock.now += 3601 * 10**9
    await lake.live([SPY])
    assert all(p.meta.source.venue_id.startswith("VEN-") for p in prices(held))


async def test_the_final_close_of_every_listing_served_is_pushed_once(tmp_path: Path) -> None:
    exchange: dict[str, Any] = {
        "bars": {
            "SPY": [
                {
                    "t": "2026-10-09T04:00:00Z",
                    "o": "1.5",
                    "h": "2",
                    "l": "1",
                    "c": "1.75",
                    "v": "10",
                }
            ]
        }
    }
    recorder = Recorder("alpaca-1")
    answer_records(recorder, RECORDS)
    clock = Clock(at("2026-10-09", 12))
    lake = synthetic_lake(recorder, clock, tmp_path, held=exchange)
    lake.products.put(SPY, "SPY")
    await lake.tick()
    assert not on(recorder, "RecordPrices")  # the session is open
    clock.now = at("2026-10-09", 20, 30)
    await lake.tick()
    await lake.tick()
    assert len(on(recorder, "RecordPrices")) == 1 and len(on(recorder, "RecordBars")) == 1
    (sent,) = on(recorder, "RecordPrices")
    assert sent.want_id == "" and sent.prices[0].meta.source_times


async def test_what_does_not_convert_is_counted_never_recorded(tmp_path: Path) -> None:
    exchange: dict[str, Any] = {
        "bars": {
            "TQQQ": [
                {
                    "t": "2026-10-08T04:00:00Z",
                    "o": "1",
                    "h": "2",
                    "l": "1",
                    "c": "1.0000000000000000001",
                    "v": "5",
                },
            ]
        }
    }
    recorder = Recorder("alpaca-1")
    answer_records(recorder, RECORDS)
    lake = synthetic_lake(recorder, Clock(AFTER), tmp_path, held=exchange)
    silent = await lake.daily([TQQQ], D8, D8)
    assert silent == [TQQQ]
    assert not on(recorder, "RecordPrices")
    assert lake.counts["daily"].unconverted == 1


async def test_a_source_that_fails_is_said_and_its_subjects_silent(tmp_path: Path) -> None:
    recorder, lake, _ = made(tmp_path)

    class Failing:
        def daily(self, *_: Any) -> Any:
            raise OSError("no route to host")

        def live(self, *_: Any) -> Any:
            raise OSError("no route to host")

        def trades(self, *_: Any) -> Any:
            raise OSError("no route to host")

        def quotes(self, *_: Any) -> Any:
            raise OSError("no route to host")

    lake.source = Failing()
    recorder.answer("ObservationsWanted", lambda _: want(SPY, business_date="2026-10-08"))
    await recorder.receive(observations_wanted=lake.on_want)
    (declined,) = on(recorder, "DeclineWant")
    assert declined.reason == ops.UNANSWERED_REASON_SOURCE_SILENT
    assert "no route to host" in lake.last_error


async def test_a_book_instrument_known_only_by_another_sources_identifiers_is_priced(
    tmp_path: Path,
) -> None:
    # Contract v18 as ruled 2026-10-10: a data plugin reads an instrument's record
    # (W3.6). TQQQ is known to the deployment only as a custodian's book
    # holds it -- its CUSIP and the custodian's symbol -- and the lake wants
    # its close: the plugin reads the record, finds the listing, joins its
    # own symbol to the record (W3.1) and records the close against the want.
    recorder, lake, _ = made(tmp_path)
    recorder.answer("ObservationsWanted", lambda _: want(TQQQ, business_date="2026-10-08"))
    await recorder.receive(observations_wanted=lake.on_want)
    (read,) = on(recorder, "ResolveInstrument")
    assert read.instrument_id == TQQQ and read.as_of_ns > 0
    (joined,) = on(recorder, "ResolveIdentifier")
    assert [(i.scheme, i.value, i.source) for i in joined.identifiers] == [
        ("symbol", "TQQQ", "alpaca"),
        ("symbol", "TQQQ", "snaptrade"),
    ]
    assert not on(recorder, "ReportMissingInstrument") and not on(recorder, "DeclineWant")
    (sent,) = on(recorder, "RecordPrices")
    assert sent.want_id == "WNT-1"
    (price,) = sent.prices
    assert [s.entity_id for s in price.meta.subjects] == [TQQQ]
    assert as_decimal(price.price.amount) == Decimal("82.37")
    assert price.meta.row_key == f"TQQQ:1d:2026-10-08@{TQQQ}"
    assert lake.products.symbols[TQQQ] == "TQQQ" and lake.reads_instruments


async def test_with_no_read_of_an_instrument_its_own_listings_alone_are_served(
    tmp_path: Path,
) -> None:
    # On a runtime from before the ruling of 2026-10-10, which grants a data plugin
    # no read of an instrument's record.
    import meridian

    recorder, lake, _ = made(tmp_path)

    def refused(_: Any) -> Any:
        raise meridian.NotGranted(
            "platform.reference.query.resolve-instrument", "this plugin holds data"
        )

    recorder.answer("ResolveInstrument", refused)
    await lake.daily([BRK, TQQQ], D8, D8)
    assert not prices(recorder) and not lake.reads_instruments
    assert len(on(recorder, "ResolveInstrument")) == 1  # asked once, not per subject
    await lake.seed(["BRK.B"])
    (minted,) = [e for e, s in lake.products.symbols.items() if s == "BRK.B"]
    await lake.daily([minted], D8, D8)
    assert [p.meta.row_key for p in prices(recorder)] == [f"BRK.B:1d:2026-10-08@{minted}"]


async def test_a_listing_that_does_not_resolve_is_reported_and_not_kept(tmp_path: Path) -> None:
    recorder, lake, _ = made(tmp_path)
    recorder.answer(
        "ResolveIdentifier",
        lambda _: ops.ResolveIdentifierResult(found=False, miss_reason=ops.MISS_REASON_AMBIGUOUS),
    )
    await lake.seed(["TQQQ"])
    (missed,) = on(recorder, "ReportMissingInstrument")
    assert missed.reason == ops.MISS_REASON_AMBIGUOUS and missed.source == "alpaca"
    assert lake.products.symbols == {}


#: A plan fund a custodian names by a symbol Alpaca has nothing for.
TCH4 = "LCL-TCH4"
WITH_TCH4 = {**RECORDS, TCH4: [ops.Identifier(scheme="symbol", value="TCH4", source="snaptrade")]}


async def test_an_unknown_symbol_is_declined_once_for_the_date(tmp_path: Path) -> None:
    """TCH4, which Alpaca has nothing for, under a standing want of live prices:
    declined once for the day, not again each minute the want is kept current
    or the lake delivers it again; the next day, once more."""
    recorder = Recorder("alpaca-1")
    answer_records(recorder, WITH_TCH4)
    clock = Clock(at("2026-10-09", 11))
    lake = synthetic_lake(recorder, clock, tmp_path)
    event = want(SPY, TCH4, dataset="suite-plugin:live", standing=True)
    recorder.answer("ObservationsWanted", lambda _: event)
    for minute in range(5):
        clock.now = at("2026-10-09", 11, minute)
        await recorder.receive(observations_wanted=lake.on_want)
        await lake.tick()
    declined = [s.entity_id for d in on(recorder, "DeclineWant") for s in d.subjects]
    assert declined == [TCH4]
    asked = lake.source.alpaca.transport.asked  # type: ignore[union-attr]
    # Asked once (its latest trade and quote), never again that day.
    assert sum("TCH4" in url for url in asked) == 2
    clock.now = at("2026-10-12", 11)
    await lake.tick()
    declined = [s.entity_id for d in on(recorder, "DeclineWant") for s in d.subjects]
    assert declined == [TCH4, TCH4]


async def test_a_source_that_fails_is_asked_again(tmp_path: Path) -> None:
    """A decline because the source failed is not settled for the date."""
    recorder, lake, clock = made(tmp_path, at("2026-10-09", 11))

    def failing(*_: object) -> list[Any]:
        raise OSError("down")

    lake.source.live = failing  # type: ignore[union-attr, method-assign]
    await lake.live([SPY], want_id="WNT-1")
    await lake.live([SPY], want_id="WNT-1")
    assert len(on(recorder, "DeclineWant")) == 2


async def test_a_restarts_gap_is_backfilled_once_and_two_days_back_at_most(
    tmp_path: Path,
) -> None:
    recorder, lake, clock = made(tmp_path, at("2026-10-09", 16, 5))
    last = ops.Trade(
        meta=ops.ObservationMeta(
            subjects=[ops.SubjectRef(entity_id=SPY)], valid_from_ns=at("2026-10-01", 10)
        )
    )
    recorder.answer("ListTrades", lambda _: ops.ListTradesResult(trades=[last]))
    lake.products.put(SPY, "SPY")
    assert await lake.backfill([SPY]) == 2
    asked = lake.source.alpaca.transport.asked  # type: ignore[union-attr]
    (history,) = [url for url in asked if "/v2/stocks/trades?" in url]
    assert "start=2026-10-07T20" in history  # two days back, not 2026-10-01
    assert await lake.backfill([SPY]) == 0  # once a process
    assert all(read.latest_recorded for read in on(recorder, "ListTrades"))
    assert lake.counts["trades"].caught_up == 2
