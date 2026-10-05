"""Alpaca's stock market data, converted into the lake's rows (rows.py).

The edge is the only place Alpaca's vocabulary is read. Each converter takes
what Alpaca sent, parsed with Decimals, the listing it was for (assets.py),
the raw record it was kept as and the feed it was asked of, and returns rows
naming the listing as their subject, priced in US dollars.

- **Which feed** every row came from is its dataset: `daily-iex`,
  `daily-sip`, `live-iex` and `live-sip` (keys proposed), so a deployment
  licenses and entitles each feed apart, and a reader always knows whether a
  price is IEX's or the consolidated tape's. `Source.venue` is the MIC of
  the one exchange a row is from where there is one (IEX's `IEXG` for every
  row of the IEX feed; a SIP trade's exchange), and empty for a
  consolidated row (a SIP bar, a SIP quote whose bid and offer are on two
  exchanges).
- **The daily close on the declared exchange day** (the lake plan's Q5):
  each daily dataset declares its day as New York's, ending 16:00, the
  exchanges' session close. A daily bar's time is the start of its New York
  day (`2026-10-02T04:00:00Z`): it gives kind `close` for business date D,
  valid from D 00:00 until D+1 00:00 New York time, its `published` source
  time the bar's end. The day still forming has no `published` time and
  keeps its row key, so each later reading is a restatement under the same
  key, never a new row. The bar's VWAP (`vw`) and trade count (`n`) are kept.
- **Trades:** Alpaca states no side, so the aggressor is not known (never
  guessed). Its condition codes are kept **as reported**, one `AsReported`
  each, under a scheme naming the tape whose code set it is
  (`alpaca:trades.c:C`, Nasdaq-listed), and a trade's eligibilities are not
  known until the codes are converted to the platform's trade attributes
  (the lake's 1b). Sizes are shares.
- **Quotes, level 1:** the best bid and offer and their sizes, a side with no
  price unset. Sizes are shares since 2025-11-03; before that Alpaca stated
  them in round lots, so an older quote's sizes cross as reported, not
  known, rather than multiplied by a lot size the quote does not state.
- **Last:** the latest trade's price, at its time, from its exchange.
- A value that does not convert -- not a number, or past the contract's
  Decimal -- is None with Alpaca's text in an `AsReported`, its scheme
  naming Alpaca's field (`alpaca:bars.c`).
"""

from __future__ import annotations

import datetime as _dt
from collections.abc import Mapping
from decimal import Decimal
from typing import Any
from zoneinfo import ZoneInfo

from .assets import Listing
from .rows import (
    NANOS_PER_SECOND,
    AsReported,
    Bar,
    DatasetDeclaration,
    DeclaredDay,
    Eligibility,
    Money,
    ObservationMeta,
    Price,
    Quote,
    RawRecordRef,
    Row,
    Source,
    SourceTime,
    Trade,
    TradeAttributes,
    as_reported,
    exact,
    rfc3339_ns,
)
from .settings import FEEDS, IEX

VENUE = "alpaca"
NEW_YORK = ZoneInfo("America/New_York")
#: The day Alpaca's quote sizes became shares; round lots before it.
SIZES_IN_SHARES_FROM = "2025-11-03"

#: Alpaca's exchange codes, as it lists them under /v2/stocks/meta/exchanges,
#: to ISO 10383 MICs, for the exchanges whose MIC is certain. Any other code
#: (a trade reporting facility, an exchange since closed) is no venue, its
#: code as reported.
MIC = {
    "A": "XASE",  # NYSE American
    "B": "XBOS",  # Nasdaq BX
    "C": "XCIS",  # NYSE National
    "H": "EPRL",  # MIAX Pearl
    "J": "EDGA",  # Cboe EDGA
    "K": "EDGX",  # Cboe EDGX
    "L": "LTSE",  # Long-Term Stock Exchange
    "M": "XCHI",  # NYSE Chicago
    "N": "XNYS",  # NYSE
    "P": "ARCX",  # NYSE Arca
    "Q": "XNAS",  # Nasdaq
    "U": "MEMX",  # Members Exchange
    "V": "IEXG",  # IEX
    "X": "XPHL",  # Nasdaq PSX
    "Y": "BATY",  # Cboe BYX
    "Z": "BATS",  # Cboe BZX
}

#: Licence terms Alpaca's standard terms impose on each dataset, as the
#: spec's catalogue has `licence_default` (the seam's DatasetDeclaration has
#: no such field yet, so they are kept beside it): personal, non-business
#: use by the account holder alone, nothing redistributed. Closes are kept
#: (a personal deployment may keep its own copy); live data is served, not
#: kept, by default (the lake plan's Q21: or kept two days).
LICENCE_DEFAULT = {
    "daily": "kept; the account holder's personal, non-business use; never redistributed",
    "live": "served, not kept (or kept two days); the account holder's personal use; "
    "never redistributed",
}


def _dataset(kind: str, feed: str) -> DatasetDeclaration:
    name = "IEX" if feed == IEX else "SIP"
    if kind == "daily":
        return DatasetDeclaration(
            key=f"daily-{feed}",
            vendor=f"Alpaca ({name})",
            data_types=("price", "bar"),
            modes=("pull",),
            cadence="P1D",
            day=DeclaredDay(calendar="America/New_York", ends_at="16:00"),
        )
    return DatasetDeclaration(
        key=f"live-{feed}",
        vendor=f"Alpaca ({name})",
        data_types=("price", "quote", "trade"),
        modes=("pull",),
        cadence="PT1S",
    )


#: The catalogue: a daily dataset (closes and bars, kept, its day New York's)
#: and a live one (last prices, quotes and trades) for each feed. The keys
#: are proposals.
CATALOGUE = tuple(_dataset(kind, feed) for kind in ("daily", "live") for feed in FEEDS)
DATASETS = {dataset.key: dataset for dataset in CATALOGUE}


def dataset(kind: str, feed: str) -> str:
    """The key of the dataset rows of `kind` from `feed` are recorded in."""
    if feed not in FEEDS:
        raise ValueError(f"{feed!r} is not one of Alpaca's feeds this plugin serves")
    return f"{kind}-{feed}"


def new_york_date(at_ns: int) -> str:
    """The New York calendar date a moment falls on."""
    moment = _dt.datetime.fromtimestamp(at_ns // NANOS_PER_SECOND, NEW_YORK)
    return moment.date().isoformat()


def new_york_midnight_ns(date: str) -> int:
    """The moment a New York calendar date starts, in UTC nanoseconds."""
    moment = _dt.datetime.fromisoformat(date).replace(tzinfo=NEW_YORK)
    return int(moment.timestamp()) * NANOS_PER_SECOND


def _next_date(date: str) -> str:
    return (_dt.date.fromisoformat(date) + _dt.timedelta(days=1)).isoformat()


class _Values:
    """Converts one record's values, collecting what did not convert."""

    def __init__(self, listing: Listing, endpoint: str) -> None:
        self.listing, self.endpoint = listing, endpoint
        self.unconverted: list[AsReported] = []

    def scheme(self, field: str) -> str:
        return f"{VENUE}:{self.endpoint}.{field}"

    def report(self, field: str, value: object) -> None:
        self.unconverted.append(as_reported(self.scheme(field), value))

    def number(self, field: str, value: object) -> Decimal | None:
        converted = exact(value, self.scheme(field))
        if isinstance(converted, AsReported):
            self.unconverted.append(converted)
            return None
        return converted

    def money(self, field: str, value: object) -> Money | None:
        amount = self.number(field, value)
        return None if amount is None else self.listing.money(amount)

    def positive(self, field: str, value: object) -> Decimal | None:
        """A quantity, which the contract has more than zero."""
        amount = self.number(field, value)
        if amount is not None and amount <= 0:
            self.report(field, value)
            return None
        return amount

    def conditions(self, item: Mapping[str, Any]) -> None:
        """Alpaca's condition codes, each as reported, under its tape's set."""
        tape = str(item.get("z") or "")
        for code in item.get("c") or []:
            self.report(f"c:{tape}" if tape else "c", str(code))

    def venue(self, field: str, code: object) -> str:
        """The MIC of one of Alpaca's exchange codes, or "" with the code as
        reported."""
        mic = MIC.get(str(code or ""))
        if mic is None:
            self.report(field, code if code is not None else "")
            return ""
        return mic


def daily(
    listing: Listing, bars: list[Any], raw_key: str, feed: str, now_ns: int
) -> tuple[list[Price], list[Bar]]:
    """Closes and bars from Alpaca's daily bars, oldest first."""
    closes: list[Price] = []
    rows: list[Bar] = []
    for item in sorted(bars, key=lambda bar: rfc3339_ns(str(bar["t"]))):
        start_ns = rfc3339_ns(str(item["t"]))
        date = new_york_date(start_ns)
        if start_ns != new_york_midnight_ns(date):
            raise ValueError(f"a daily bar at {item['t']} does not start at New York's midnight")
        end_ns = new_york_midnight_ns(_next_date(date))
        published = () if end_ns > now_ns else (SourceTime("published", end_ns),)
        values = _Values(listing, "bars")
        open_, high, low, close = (
            values.money(field, item.get(field)) for field in ("o", "h", "l", "c")
        )
        volume = values.number("v", item.get("v"))
        if volume is not None and volume < 0:
            values.report("v", item.get("v"))
            volume = None
        vwap = values.money("vw", item["vw"]) if item.get("vw") is not None else None
        count = item.get("n")
        trade_count = count if isinstance(count, int) and not isinstance(count, bool) else None
        if count is not None and trade_count is None:
            values.report("n", count)

        source = Source(dataset("daily", feed), "IEXG" if feed == IEX else "")
        raw = RawRecordRef(f"{raw_key}#{start_ns // NANOS_PER_SECOND}")
        close_meta = ObservationMeta(
            row_key=f"close/{listing.symbol}/{date}",
            subjects=(listing.subject(),),
            source=source,
            valid_from_ns=start_ns,
            valid_until_ns=end_ns,
            business_date=date,
            source_times=published,
            raw=raw,
            unconverted=tuple(u for u in values.unconverted if u.scheme.endswith(".c")),
        )
        closes.append(Price(close_meta, "close", close))
        bar_meta = ObservationMeta(
            row_key=f"bar/{listing.symbol}/P1D/{date}",
            subjects=(listing.subject(),),
            source=source,
            valid_from_ns=start_ns,
            valid_until_ns=end_ns,
            business_date=date,
            source_times=published,
            raw=raw,
            unconverted=tuple(values.unconverted),
        )
        rows.append(
            Bar(
                bar_meta,
                open=open_,
                high=high,
                low=low,
                close=close,
                volume=volume,
                vwap=vwap,
                trade_count=trade_count,
            )
        )
    return closes, rows


#: Alpaca's trades state no side and no converted condition: eligibilities
#: not known in either view, until the 1b conversion of its codes.
NOT_KNOWN = TradeAttributes(consolidated=Eligibility(), market_centre=Eligibility())


def trade(
    listing: Listing, item: Mapping[str, Any], raw_key: str, feed: str, endpoint: str = "trades"
) -> Trade:
    """One trade: its exchange's MIC, its conditions as reported, no side."""
    values = _Values(listing, endpoint)
    at = rfc3339_ns(str(item["t"]))
    venue = values.venue("x", item.get("x"))
    values.conditions(item)
    price = values.money("p", item.get("p"))
    quantity = values.positive("s", item.get("s"))
    trade_id = int(item["i"])
    return Trade(
        ObservationMeta(
            row_key=f"trade/{listing.symbol}/{item.get('x')}/{trade_id}",
            subjects=(listing.subject(),),
            source=Source(dataset("live", feed), venue),
            valid_from_ns=at,
            source_times=(SourceTime("event", at),),
            raw=RawRecordRef(raw_key),
            unconverted=tuple(values.unconverted),
        ),
        price=price,
        quantity=quantity,
        attributes=NOT_KNOWN,
        source_sequence=trade_id,
    )


def trades(listing: Listing, items: list[Any], raw_key: str, feed: str) -> list[Trade]:
    """Trades oldest first, each referencing its place in the record by ID."""
    ordered = sorted(items, key=lambda item: (rfc3339_ns(str(item["t"])), int(item["i"])))
    return [trade(listing, item, f"{raw_key}#{item['i']}", feed) for item in ordered]


def last(
    listing: Listing, item: Mapping[str, Any], raw_key: str, feed: str, endpoint: str = "trades"
) -> Price:
    """The latest trade's price, at its time, from its exchange."""
    values = _Values(listing, endpoint)
    at = rfc3339_ns(str(item["t"]))
    venue = values.venue("x", item.get("x"))
    price = values.money("p", item.get("p"))
    return Price(
        ObservationMeta(
            row_key=f"last/{listing.symbol}/{item.get('x')}/{item.get('i') or at}",
            subjects=(listing.subject(),),
            source=Source(dataset("live", feed), venue),
            valid_from_ns=at,
            source_times=(SourceTime("event", at),),
            raw=RawRecordRef(raw_key),
            unconverted=tuple(values.unconverted),
        ),
        "last",
        price,
    )


def quote(
    listing: Listing, item: Mapping[str, Any], raw_key: str, feed: str, endpoint: str = "quotes"
) -> Quote:
    """The best bid and offer. A side with no price (Alpaca sends 0) is
    unset; sizes are shares, or as reported before Alpaca stated shares."""
    values = _Values(listing, endpoint)
    at = rfc3339_ns(str(item["t"]))
    in_shares = new_york_date(at) >= SIZES_IN_SHARES_FROM
    values.conditions(item)
    sides: dict[str, tuple[Money | None, Decimal | None, str]] = {}
    for side, price_field, size_field, venue_field in (
        ("bid", "bp", "bs", "bx"),
        ("ask", "ap", "as", "ax"),
    ):
        price_value = item.get(price_field)
        if price_value in (None, 0):
            sides[side] = (None, None, "")
            continue
        money = values.money(price_field, price_value)
        size: Decimal | None
        if in_shares:
            size = values.positive(size_field, item.get(size_field))
        else:
            values.report(f"{size_field}.round_lots", item.get(size_field))
            size = None
        sides[side] = (money, size, str(item.get(venue_field) or ""))
    exchanges = {code for _, _, code in sides.values() if code}
    venue = ""
    if len(exchanges) == 1:
        venue = values.venue("bx" if sides["bid"][2] else "ax", exchanges.pop())
    return Quote(
        ObservationMeta(
            row_key=f"quote/{listing.symbol}/{at}",
            subjects=(listing.subject(),),
            source=Source(dataset("live", feed), venue),
            valid_from_ns=at,
            source_times=(SourceTime("event", at),),
            raw=RawRecordRef(raw_key),
            unconverted=tuple(values.unconverted),
        ),
        bid=sides["bid"][0],
        ask=sides["ask"][0],
        bid_quantity=sides["bid"][1],
        ask_quantity=sides["ask"][1],
    )


def snapshot(listing: Listing, item: Mapping[str, Any], raw_key: str, feed: str) -> list[Row]:
    """A snapshot's latest trade and latest quote, as `last` and `quote`
    rows; its bars are the daily dataset's, read from bars."""
    rows: list[Row] = []
    if item.get("latestTrade"):
        rows.append(last(listing, item["latestTrade"], f"{raw_key}#latestTrade", feed, "snapshots"))
    if item.get("latestQuote"):
        rows.append(
            quote(listing, item["latestQuote"], f"{raw_key}#latestQuote", feed, "snapshots")
        )
    return rows
