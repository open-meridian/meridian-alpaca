"""The `dgm` conformance suite for the retail equity pair: one file, unchanged.

Copied from the crypto pair's suite (meridian-coinbase and meridian-kraken,
tests/test_conformance.py) and kept to its shape: the lake's rows stated in
contract terms alone -- the subject, the currency, the kind, the business
date, the valid times, the dataset and venue, the exact decimals -- and each
plugin asked, through its `conformance` fixture (tests/conftest.py), to
convert its own venue's synthetic exchange of the same market facts. Its
seam is the same rows.py, by the same digest.

Where the crypto pair's cases are about tokens (CAIP-19, a stablecoin, a
token on several networks), these are about a US listing instead: a symbol
namespaced to the venue and no global identifier, a share class's one
spelling, the exchange day in New York, a consolidated close, a live price
naming the exchange it printed on, sizes in shares, and condition codes
crossing as reported. The rest -- the forming day restated, a price past a
float, a value past the contract's Decimal, raw records, no float, one shape
-- are the crypto pair's cases with equity facts.

Meant to be identical, byte for byte, in meridian-tradier when it is built
(`make conformance-same` diffs it against the sibling): Alpaca's JSON
numbers, IEX and SIP feeds and dotted share classes, Tradier's sizes in
hundreds, `BRK/B` and consolidated quotes, into the same rows. When the
lake's contract and the SDK's `dgm` suite exist, these cases become that
suite's equity cases (plans/the-lake-prices-the-book, "The `dgm` suite").
"""

from __future__ import annotations

import dataclasses
import hashlib
import re
from decimal import Decimal
from pathlib import Path
from typing import Any

#: The digest of rows.py, the seam, which is the same module in every spike.
ROWS_SHA256 = "fcd87f34d313c2b406069e7f41403d6ff319c94e658bc6bb5f7c904703cb9bd0"

USD = ("iso4217", "USD", "")

S = 1_000_000_000
H = 3600 * S
#: The start of each New York day (daylight time, UTC-4), in UTC.
SEP_30 = 1_790_740_800 * S
OCT_1, OCT_2, OCT_3 = SEP_30 + 24 * H, SEP_30 + 48 * H, SEP_30 + 72 * H
#: The synthetic exchanges' now: noon in New York on 2026-10-02, that day still forming.
NOW = OCT_2 + 12 * H

#: The market facts every synthetic exchange states, each in its venue's dialect.
#: (business date, valid from, open, high, low, close, volume, vwap, trade count)
DAYS = [
    ("2026-09-30", SEP_30, "61.05", "62.5", "60.5", "62.12", "41234567", "61.8123", 245678),
    ("2026-10-01", OCT_1, "62.12", "63", "61.5", "62.75", "38765432", "62.3045", 234567),
    ("2026-10-02", OCT_2, "62.75", "62.9", "62.6", "62.8", "10500", "62.7803", 1234),
]
LATER = ("2026-10-02", OCT_2, "62.75", "62.95", "62.6", "62.85", "15075", "62.8004", 2345)
#: (source sequence, price, quantity in shares, valid from, condition codes)
TRADES = [
    (52983525029461, "62.8", "100", NOW - 2 * S + 123_400_000, ("@",)),  # 11:59:58.1234 New York
    (52983525029462, "62.795", "25", NOW - S // 2, ("@", "I")),  # 11:59:59.5 New York
]
#: (bid, bid size, ask, ask size), sizes in shares
QUOTE = ("62.79", "300", "62.81", "200")


def ident(identifier: Any) -> tuple[str, str, str]:
    return (identifier.scheme, identifier.value, identifier.source)


def subject(row: Any) -> tuple[str, str, str]:
    [only] = row.meta.subjects
    return ident(only.resolve_by)


def money(value: Any) -> tuple[Decimal, tuple[str, str, str]] | None:
    return None if value is None else (value.amount, ident(value.currency))


def usd(text: str) -> tuple[Decimal, tuple[str, str, str]]:
    return (Decimal(text), USD)


def tqqq(conformance: Any) -> tuple[str, str, str]:
    return ("symbol", "TQQQ", conformance.plugin)


def walk(value: Any) -> Any:
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        for f in dataclasses.fields(value):
            yield from walk(getattr(value, f.name))
    elif isinstance(value, tuple | list):
        for item in value:
            yield from walk(item)
    else:
        yield value


def is_mic(text: str) -> bool:
    return re.fullmatch(r"[A-Z0-9]{4}", text) is not None


# --- the seam -----------------------------------------------------------------


def test_the_seam_is_one_module_in_every_plugin(conformance: Any) -> None:
    source = Path(conformance.rows.__file__).read_bytes()
    assert hashlib.sha256(source).hexdigest() == ROWS_SHA256


# --- identifiers ----------------------------------------------------------------


def test_a_listing_is_the_venues_symbol_priced_in_the_dollars_cash_instrument(
    conformance: Any,
) -> None:
    listing = conformance.listing("TQQQ")
    # The deployment's instrument by the venue's symbol, namespaced to the
    # venue (decisions/030): no global identifier is stated, so none is made.
    assert ident(listing.subject().resolve_by) == tqqq(conformance)
    assert ident(listing.money(Decimal("1")).currency) == USD


def test_a_share_class_has_one_spelling_with_a_dot(conformance: Any) -> None:
    listing = conformance.listing("BRK.B")
    assert ident(listing.subject().resolve_by) == ("symbol", "BRK.B", conformance.plugin)


# --- the daily close, on the declared exchange day ------------------------------------


def test_each_daily_dataset_declares_new_yorks_day_ending_at_the_close(conformance: Any) -> None:
    datasets = {d.key: d for d in conformance.datasets()}
    daily = datasets[conformance.DAILY_DATASET]
    assert daily.data_types == ("price", "bar")
    live = datasets[conformance.LIVE_DATASET]
    assert set(live.data_types) == {"price", "quote", "trade"} and live.day is None
    for dataset in datasets.values():
        if "bar" in dataset.data_types:
            assert (dataset.day.calendar, dataset.day.ends_at) == ("America/New_York", "16:00")


def test_a_daily_close_is_the_declared_days_consolidated_close(conformance: Any) -> None:
    closes, _ = conformance.daily("daily")
    got = [
        (
            p.kind,
            p.basis,
            subject(p),
            money(p.price),
            p.meta.business_date,
            p.meta.valid_from_ns,
            p.meta.valid_until_ns,
            [(t.kind, t.at_ns) for t in p.meta.source_times],
            p.meta.source.dataset,
            p.meta.source.venue,
            p.meta.unconverted,
        )
        for p in closes
    ]
    assert got == [
        (
            "close",
            "per_unit",
            tqqq(conformance),
            usd(close),
            date,
            start,
            start + 24 * H,
            # A complete day was published at its end; the day still forming not yet.
            [("published", start + 24 * H)] if start + 24 * H <= NOW else [],
            conformance.DAILY_DATASET,
            # Consolidated across the exchanges: no one venue.
            "",
            (),
        )
        for date, start, _o, _h, _l, close, _v, _w, _n in DAYS
    ]


def test_daily_bars_carry_what_the_venue_gave_and_nothing_it_did_not(conformance: Any) -> None:
    _, bars = conformance.daily("daily")
    assert len(bars) == len(DAYS)
    for bar, (date, start, o, h, low, c, v, vwap, count) in zip(bars, DAYS, strict=True):
        assert (bar.meta.business_date, bar.meta.valid_from_ns, bar.meta.valid_until_ns) == (
            date,
            start,
            start + 24 * H,
        )
        assert subject(bar) == tqqq(conformance)
        assert [money(x) for x in (bar.open, bar.high, bar.low, bar.close)] == [
            usd(o),
            usd(h),
            usd(low),
            usd(c),
        ]
        assert bar.volume == Decimal(v)
        # Unset where the venue gives none, never zero.
        assert bar.vwap is None or money(bar.vwap) == usd(vwap)
        assert bar.trade_count is None or bar.trade_count == count


def test_the_forming_day_is_restated_under_its_row_key(conformance: Any) -> None:
    first, _ = conformance.daily("daily")
    later, later_bars = conformance.daily("daily-later")
    assert [p.meta.row_key for p in first] == [p.meta.row_key for p in later]
    assert len({p.meta.row_key for p in first}) == 3
    # The complete days are an identical repeat: nothing changes but the raw record.
    for a, b in zip(first[:2], later[:2], strict=True):
        assert dataclasses.replace(a, meta=dataclasses.replace(a.meta, raw=b.meta.raw)) == b
    # The day still forming: the same key, its new value.
    date, start, o, h, low, c, v, vwap, count = LATER
    assert money(later[2].price) == usd(c) and money(first[2].price) == usd("62.8")
    assert later[2].meta.source_times == ()
    assert [money(x) for x in (later_bars[2].high, later_bars[2].close)] == [usd(h), usd(c)]
    assert later_bars[2].volume == Decimal(v)


def test_a_price_past_what_a_float_holds_is_exact(conformance: Any) -> None:
    closes, _ = conformance.daily("precise")
    [close] = closes
    assert close.price.amount == Decimal("62.123456789012345678")
    assert str(close.price.amount) == "62.123456789012345678"
    assert Decimal(float(close.price.amount)) != close.price.amount


def test_a_value_past_the_contracts_decimal_crosses_as_reported(conformance: Any) -> None:
    closes, bars = conformance.daily("unconvertible")
    [close], [bar] = closes, bars
    assert close.price is None and bar.close is None
    [reported] = close.meta.unconverted
    assert reported.scheme.startswith(conformance.plugin + ":")
    assert reported.code == "1.0000000000000000001"
    assert reported in bar.meta.unconverted
    # Everything that did convert still crosses.
    assert money(bar.open) == usd("61.05") and bar.volume == Decimal("41234567")


# --- live prices, quotes and trades ---------------------------------------------------


def test_the_last_price_names_the_exchange_it_printed_on(conformance: Any) -> None:
    price = conformance.last()
    assert (price.kind, subject(price), money(price.price)) == (
        "last",
        tqqq(conformance),
        usd("62.795"),
    )
    assert price.meta.valid_from_ns == TRADES[1][3]
    assert (price.meta.business_date, price.meta.valid_until_ns) == ("", 0)
    assert price.meta.source.dataset == conformance.LIVE_DATASET
    assert is_mic(price.meta.source.venue)


def test_the_quote_in_shares(conformance: Any) -> None:
    quote = conformance.quote()
    bid, bid_size, ask, ask_size = QUOTE
    assert subject(quote) == tqqq(conformance)
    assert (money(quote.bid), quote.bid_quantity) == (usd(bid), Decimal(bid_size))
    assert (money(quote.ask), quote.ask_quantity) == (usd(ask), Decimal(ask_size))
    assert quote.characteristics == () and quote.meta.source.dataset == conformance.LIVE_DATASET


def test_a_quote_with_one_side_empty_leaves_that_side_unset(conformance: Any) -> None:
    quote = conformance.quote("one-side")
    assert (money(quote.bid), quote.bid_quantity) == (usd(QUOTE[0]), Decimal(QUOTE[1]))
    assert (quote.ask, quote.ask_quantity) == (None, None)


def test_a_trade_with_no_side_stated_and_its_conditions_as_reported(conformance: Any) -> None:
    trades = conformance.trades()
    got = [(t.source_sequence, money(t.price), t.quantity, t.meta.valid_from_ns) for t in trades]
    assert got == [(n, usd(p), Decimal(q), at) for n, p, q, at, _c in TRADES]
    for trade, (_n, _p, _q, _at, conditions) in zip(trades, TRADES, strict=True):
        assert subject(trade) == tqqq(conformance) and not trade.cancelled
        # Neither venue states the aggressor: not known, never guessed.
        assert trade.aggressor == ""
        # Codes not yet converted to the platform's attributes: each as
        # reported, and every eligibility not known.
        assert [u.code for u in trade.meta.unconverted if ".c" in u.scheme] == list(conditions)
        assert all(u.scheme.startswith(conformance.plugin + ":") for u in trade.meta.unconverted)
        for view in (trade.attributes.consolidated, trade.attributes.market_centre):
            assert (view.high_low, view.open, view.close, view.volume) == ("",) * 4
        assert trade.attributes.characteristics == ()
        assert trade.meta.source.dataset == conformance.LIVE_DATASET
        assert is_mic(trade.meta.source.venue)


# --- every row --------------------------------------------------------------------------


def _every_row(conformance: Any) -> list[Any]:
    closes, bars = conformance.daily("daily")
    return [
        *closes,
        *bars,
        conformance.last(),
        conformance.quote(),
        conformance.quote("one-side"),
        *conformance.trades(),
    ]


def test_every_row_references_a_raw_record_that_resolves(conformance: Any) -> None:
    for row in _every_row(conformance):
        assert row.meta.raw.instance_id == ""  # the sidecar's to fill
        assert conformance.raw(row.meta.raw) is not None, row.meta.raw


def test_every_row_names_a_declared_dataset(conformance: Any) -> None:
    declared = {d.key for d in conformance.datasets()}
    for row in _every_row(conformance):
        assert row.meta.source.dataset in declared, row.meta.source.dataset


def test_no_float_anywhere_in_any_row(conformance: Any) -> None:
    for row in _every_row(conformance):
        assert not any(isinstance(value, float) for value in walk(row))


#: Each row type's shape: one contract between the plugins.
SHAPES: dict[str, dict[str, Any]] = {
    "Price": {"kind": "str", "price": "Money", "basis": "str"},
    "Bar": {
        "open": "Money",
        "high": "Money",
        "low": "Money",
        "close": "Money",
        "volume": "Decimal",
        "vwap": ("Money", None),
        "trade_count": ("int", None),
    },
    "Quote": {
        "bid": ("Money", None),
        "ask": ("Money", None),
        "bid_quantity": ("Decimal", None),
        "ask_quantity": ("Decimal", None),
        "characteristics": "tuple",
    },
    "Trade": {
        "price": "Money",
        "quantity": "Decimal",
        "attributes": "TradeAttributes",
        "aggressor": "str",
        "source_sequence": "int",
        "cancelled": "bool",
    },
}
META = {
    "row_key": "str",
    "subjects": "tuple",
    "source": "Source",
    "valid_from_ns": "int",
    "raw": "RawRecordRef",
    "valid_until_ns": "int",
    "business_date": "str",
    "source_times": "tuple",
    "unconverted": "tuple",
}


def test_every_row_has_the_contracts_shape(conformance: Any) -> None:
    for row in _every_row(conformance):
        name = type(row).__name__
        fields = {f.name: getattr(row, f.name) for f in dataclasses.fields(row)}
        meta = fields.pop("meta")
        assert {
            f.name: type(getattr(meta, f.name)).__name__ for f in dataclasses.fields(meta)
        } == META
        assert set(fields) == set(SHAPES[name]), name
        for field, value in fields.items():
            wanted = SHAPES[name][field]
            allowed = wanted if isinstance(wanted, tuple) else (wanted,)
            got = None if value is None else type(value).__name__
            assert got in allowed, (name, field, got)
        if hasattr(row, "price") and row.price is not None:
            assert type(row.price.amount).__name__ == "Decimal"
