"""Alpaca's stock market data, converted to what the plugin records.

The edge is the only place Alpaca's vocabulary is read. Each converter takes
what Alpaca sent, parsed with Decimals (vendor.parse), and the raw record it
was kept as, and returns observations named by Alpaca's own symbol and
exchange letter, which lake.py resolves to the deployment's instrument and
venue before anything is recorded.

- **The daily close on the declared day.** The daily dataset's day is New
  York's, ending at 16:00, the exchanges' session close (`day_time_zone`
  America/New_York, `day_end_minute` 960). Alpaca's daily bar is stamped at
  the start of its New York day (`2026-10-08T04:00:00Z`): its business date
  is that New York date, a plain date. The bar's close is the day's close,
  valid from the previous session's close until this one's; once 16:00 has
  passed its `published` source time is that close, and before then the day
  is still forming, recorded under the same row key and restated as it
  fills. The bar's VWAP (`vw`) and trade count (`n`) are kept.
- **Live prices:** the latest trade's price is `last`, at its time, on the
  exchange whose letter Alpaca prints (`x`, a SIP participant letter, which
  the venue master holds as Alpaca's code); an IEX quote's bid and ask are
  IEX's; a SIP quote's are the consolidated best bid and offer, no venue.
- **Exact.** A value that is not a number, or past the contract's Decimal (18
  places), does not convert: the row is not recorded, and Alpaca's text is
  kept as reported (`alpaca:bars.c`) for the Datasets page to count. A price
  is never read through a float.
- **One spelling of a share class:** Alpaca spells it with a dot (`BRK.B`),
  which is the spelling this plugin names a listing by.
"""

from __future__ import annotations

import datetime as dt
import re
from collections.abc import Iterator, Mapping
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any
from zoneinfo import ZoneInfo

from meridian.edge import as_reported
from meridian.plugin.v1.operations_pb2 import AsReported

from .vendor import IEX, NS, Fetched

SOURCE = "alpaca"
NEW_YORK = ZoneInfo("America/New_York")
#: The session's close, the daily dataset's day end: 16:00 New York.
DAY_END_MINUTE = 16 * 60
#: The most decimal places a Decimal crosses the wire with.
MOST_PLACES = 18
#: The most digits, as the wire's two 64-bit halves hold them.
MOST_DIGITS = 38

#: A US listing as Alpaca spells it: a root of capitals (digits after the
#: first), and a class or series after a dot.
SYMBOL = re.compile(r"[A-Z][A-Z0-9]{0,5}(?:\.[A-Z0-9]{1,4})?")


def spelled(symbol: str) -> str | None:
    """The listing in the one spelling this plugin names it by, from a
    symbol in any of the usual spellings (`BRK.B`, `BRK/B`, `BRK-B`,
    `brk.b`), or None where it is not a US listing's symbol."""
    candidate = symbol.strip().upper().replace("/", ".").replace("-", ".")
    return candidate if SYMBOL.fullmatch(candidate) else None


# ── Days ────────────────────────────────────────────────────────────────────


def new_york_date(at_ns: int) -> dt.date:
    """The New York calendar date a moment falls on."""
    return dt.datetime.fromtimestamp(at_ns / NS, NEW_YORK).date()


def new_york_midnight_ns(day: dt.date | str) -> int:
    """The moment a New York calendar date starts, in UTC nanoseconds."""
    date = dt.date.fromisoformat(day) if isinstance(day, str) else day
    moment = dt.datetime(date.year, date.month, date.day, tzinfo=NEW_YORK)
    return int(moment.timestamp()) * NS


def next_date(day: dt.date | str) -> dt.date:
    date = dt.date.fromisoformat(day) if isinstance(day, str) else day
    return date + dt.timedelta(days=1)


def close_of(day: dt.date) -> int:
    """The moment a business date's session closes: 16:00 New York."""
    return new_york_midnight_ns(day) + DAY_END_MINUTE * 60 * NS


def previous_close_of(day: dt.date) -> int:
    """The previous calendar day's 16:00, where the business date begins."""
    return close_of(day - dt.timedelta(days=1))


def parse_time(text: str) -> int:
    """An RFC 3339 time, to the nanosecond Alpaca states it to."""
    match = re.fullmatch(r"(.+?)(?:\.(\d{1,9}))?(Z|[+-]\d\d:\d\d)", text)
    if match is None:
        raise ValueError(f"{text!r} is not an RFC 3339 time")
    base, fraction, zone = match.groups()
    moment = dt.datetime.fromisoformat(base + ("+00:00" if zone == "Z" else zone))
    return int(moment.timestamp()) * NS + int((fraction or "").ljust(9, "0"))


# ── Values ──────────────────────────────────────────────────────────────────


def exact(value: object) -> Decimal | None:
    """A number exactly as Alpaca stated it, or None where it is not one the
    contract carries: not a number, not finite, or past 18 places."""
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        number = Decimal(value)
    elif isinstance(value, Decimal):
        number = value
    else:
        return None
    if not number.is_finite():
        return None
    _, digits, exponent = number.as_tuple()
    if not isinstance(exponent, int) or -exponent > MOST_PLACES:
        return None
    if len(digits) + max(exponent, 0) > MOST_DIGITS:
        return None
    return number


@dataclass
class _Values:
    """Converts one record's values, collecting what did not convert."""

    endpoint: str
    unconverted: list[AsReported] = field(default_factory=list)

    def number(self, name: str, value: object, *, positive: bool = False) -> Decimal | None:
        converted = exact(value)
        if converted is None or (positive and converted <= 0) or converted < 0:
            if value is not None:
                self.unconverted.append(
                    as_reported(f"{SOURCE}:{self.endpoint}.{name}", str(value)[:64])
                )
            return None
        return converted


# ── Daily bars ──────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class DailyBar:
    """One listing's day, as Alpaca's daily bar states it."""

    symbol: str
    business_date: dt.date
    open: Decimal | None
    high: Decimal | None
    low: Decimal | None
    close: Decimal | None
    volume: Decimal | None
    vwap: Decimal | None
    trade_count: int | None
    raw_key: str
    unconverted: tuple[AsReported, ...] = ()

    @property
    def row_key(self) -> str:
        """The bar's identity, from the raw record: the same day again is the
        same row, and a changed value under it a restatement."""
        return f"{self.symbol}:1d:{self.business_date.isoformat()}"

    @property
    def complete(self) -> bool:
        return self.close is not None and None not in (self.open, self.high, self.low)


def daily_bars(fetched: Fetched) -> Iterator[DailyBar]:
    """Every bar of a page of `/v2/stocks/bars`, in Alpaca's order."""
    bars: Mapping[str, Any] = fetched.value.get("bars") or {}
    for symbol, items in bars.items():
        for index, item in enumerate(items or []):
            values = _Values("bars")
            try:
                day = new_york_date(parse_time(str(item.get("t"))))
            except ValueError:
                continue
            count = item.get("n")
            yield DailyBar(
                symbol=str(symbol),
                business_date=day,
                open=values.number("o", item.get("o"), positive=True),
                high=values.number("h", item.get("h"), positive=True),
                low=values.number("l", item.get("l"), positive=True),
                close=values.number("c", item.get("c"), positive=True),
                volume=values.number("v", item.get("v")),
                vwap=values.number("vw", item.get("vw"), positive=True),
                trade_count=count if isinstance(count, int) and count >= 0 else None,
                raw_key=f"{fetched.raw_key}#{symbol}/{index}",
                unconverted=tuple(values.unconverted),
            )


# ── Live prices ─────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class Quoted:
    """One live price: the latest trade's (`last`), or a quote's side."""

    symbol: str
    kind: str
    amount: Decimal | None
    at_ns: int
    venue_code: str
    raw_key: str
    unconverted: tuple[AsReported, ...] = ()

    @property
    def row_key(self) -> str:
        return f"{self.symbol}:{self.kind}:{self.at_ns}"


def latest_trades(fetched: Fetched) -> Iterator[Quoted]:
    """`last` for each symbol of `/v2/stocks/trades/latest`, on the exchange
    whose letter it printed (`x`)."""
    trades: Mapping[str, Any] = fetched.value.get("trades") or {}
    for symbol, item in trades.items():
        values = _Values("trades")
        try:
            at = parse_time(str(item.get("t")))
        except ValueError:
            continue
        yield Quoted(
            symbol=str(symbol),
            kind="last",
            amount=values.number("p", item.get("p"), positive=True),
            at_ns=at,
            venue_code=str(item.get("x") or ""),
            raw_key=f"{fetched.raw_key}#{symbol}",
            unconverted=tuple(values.unconverted),
        )


def latest_quotes(fetched: Fetched) -> Iterator[Quoted]:
    """`bid` and `ask` for each symbol of `/v2/stocks/quotes/latest`: IEX's
    on IEX's feed, the consolidated best bid and offer on SIP's (no venue).
    A side with no price (0, as Alpaca sends an empty side) is not sent."""
    quotes: Mapping[str, Any] = fetched.value.get("quotes") or {}
    for symbol, item in quotes.items():
        try:
            at = parse_time(str(item.get("t")))
        except ValueError:
            continue
        for kind, price, exchange in (("bid", "bp", "bx"), ("ask", "ap", "ax")):
            if exact(item.get(price)) == 0:
                continue
            values = _Values("quotes")
            yield Quoted(
                symbol=str(symbol),
                kind=kind,
                amount=values.number(price, item.get(price), positive=True),
                at_ns=at,
                venue_code=str(item.get(exchange) or "") if fetched.feed == IEX else "",
                raw_key=f"{fetched.raw_key}#{symbol}/{kind}",
                unconverted=tuple(values.unconverted),
            )
