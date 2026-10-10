"""What the plugin records in the lake, and how it answers what the lake wants
(contract v18, W10; plans/the-lake-prices-the-book, the retail pair).

Two datasets, apart (the plan's Q21), each declared in the catalogue
(declaration.py):

- **daily**: each listing's close and daily bar for a business date, from
  the source's consolidated daily bars. Pulled: recorded when a reader's read
  is wanted (W10.7). Pushed: once a session's close is final, the day's
  closes of every listing it has served, unasked, so a book valued each
  evening finds them already kept. A day still forming is recorded and
  restated under the same row key as it fills, until final.
- **live**: the latest trade's price (`last`) and the best bid and ask. Pulled
  for a read, and kept current at the dataset's cadence while a standing want
  stands; nothing once it is withdrawn.

**Every subject is the deployment's own instrument.** The lake wants
instruments by the deployment's ID; the plugin asks the instrument store
what the record is (W3.6) and takes the listing's symbol from its
identifiers: the one this source reported (`symbol` at this source) where it
has one, else a `symbol` another source reported, in this source's one
spelling of a share class. Using another source's symbol, it reports its own
beside it (W3.1), so the record carries this source's code; where that
resolve is ambiguous it is reported as a miss (W3.2), and the want is still
answered for the subject the lake named. A subject whose record carries no
symbol is declined, not covered. Which symbol each subject is, is kept in
the plugin's storage, to rebuild from.

**Every venue is the venue master's.** A live price's exchange is resolved
by the code the source prints, as a `symbol` at this source (W3.14); a
venue the deployment does not hold is reported (W3.15), and the row is
recorded with no venue and the code as reported beside it, which no reader
takes for the consolidated view. A consolidated price names no venue.

What it recorded, declined and could not convert is counted here for the
Datasets page.
"""

from __future__ import annotations

import asyncio
import datetime as dt
import json
import logging
import time
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

import meridian
from meridian import Bar, Money, ObservationMeta, Price, SourceTime
from meridian.plugin.v1 import operations_pb2 as ops

from .convert import (
    SOURCE,
    DailyBar,
    Quoted,
    close_of,
    new_york_date,
    previous_close_of,
    spelled,
)
from .vendor import NS

log = logging.getLogger("alpaca.lake")

DAILY = "daily"
LIVE = "live"
CURRENCY = "USD"
PRICE = "meridian.v1.Price"
BAR = "meridian.v1.Bar"
DAY_NS = 86_400 * NS
#: The most rows one batch carries (W10.4).
BATCH = 500
#: The live dataset's cadence: a standing want is kept current this often.
LIVE_CADENCE_SECONDS = 60
#: How long after a session's close its day is still read again for a
#: restatement: the extended session's end, and the source's delay, past it.
FINAL_AFTER_NS = (4 * 60 + 15) * 60 * NS
#: How often a day still forming is read again.
FORMING_EVERY_NS = 5 * 60 * NS
#: The earliest day the source's consolidated history reaches.
HISTORY_FROM = dt.date(2016, 1, 1)
#: A venue the deployment did not hold is asked again after this long.
VENUE_MISS_NS = 3600 * NS
LIVE_KINDS = ("last", "bid", "ask")


class Source(Protocol):
    """The vendor, as lake.py asks it: blocking calls, run in a thread."""

    def daily(self, symbols: Sequence[str], first: dt.date, last: dt.date) -> list[DailyBar]: ...

    def live(self, symbols: Sequence[str]) -> list[Quoted]: ...


@dataclass
class Counts:
    """What one dataset did since the plugin started, for the Datasets page."""

    recorded: int = 0
    restated: int = 0
    unchanged: int = 0
    declined: int = 0
    unconverted: int = 0
    wants: int = 0
    last_recorded_at_ns: int = 0
    last_declined: str = ""


@dataclass
class Standing:
    """A standing want on the live dataset, kept current until withdrawn."""

    want_id: str
    subjects: tuple[str, ...]
    kinds: tuple[str, ...]
    last_at_ns: int = 0


@dataclass(frozen=True)
class Forming:
    """A day recorded before its close was final, to be read again."""

    entity_id: str
    symbol: str
    day: dt.date
    data_type: str


class Products:
    """Which listing each subject is: the deployment's instrument ID to this
    source's symbol, kept in the plugin's storage (decisions/028) so a new
    process rebuilds from it rather than asking again."""

    def __init__(self, path: Path | None) -> None:
        self.path = path
        self.symbols: dict[str, str] = {}
        if path is not None and path.exists():
            try:
                self.symbols = {str(k): str(v) for k, v in json.loads(path.read_text()).items()}
            except (OSError, ValueError):
                log.warning("the product map in storage did not read; asking again")

    def put(self, entity_id: str, symbol: str) -> None:
        if self.symbols.get(entity_id) == symbol:
            return
        self.symbols[entity_id] = symbol
        if self.path is not None:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            partial = self.path.with_suffix(".partial")
            partial.write_text(json.dumps(self.symbols, sort_keys=True, indent=1))
            partial.replace(self.path)


def _chunks(items: Sequence[Any], size: int = BATCH) -> Iterable[Sequence[Any]]:
    for start in range(0, len(items), size):
        yield items[start : start + size]


def _kind_name(value: int) -> str:
    return str(ops.PriceKind.Name(value)).removeprefix("PRICE_KIND_").lower()


class Lake:
    """The plugin's half of the lake, on whatever operations it is given: the
    sidecar's (`meridian.Plugin`) or the suite's `Recorder`."""

    def __init__(
        self,
        plugin: Any,
        source: Source | None,
        products: Products | None = None,
        clock_ns: Callable[[], int] = time.time_ns,
    ) -> None:
        self.plugin = plugin
        self.source = source
        self.products = products or Products(None)
        self.clock_ns = clock_ns
        self.counts = {DAILY: Counts(), LIVE: Counts()}
        self.standing: dict[str, Standing] = {}
        self.forming: dict[Forming, str] = {}
        self.pushed: dt.date | None = None
        self._venues: dict[str, tuple[str, int]] = {}
        self._forming_at = 0
        #: Whether the deployment answers this plugin's ResolveInstrument.
        self.reads_instruments = True
        self.last_error = ""

    # ── Names ────────────────────────────────────────────────────────────

    def dataset(self, key: str) -> str:
        """A dataset's ID in the deployment: the instance, a colon, its key."""
        return f"{self.plugin.identity.instance_id}:{key}"

    def _key_of(self, dataset: str) -> str:
        return dataset.rpartition(":")[2]

    # ── Subjects and venues (W3.6, W3.1, W3.2, W3.14, W3.15) ─────────────

    async def symbol_of(self, entity_id: str) -> str | None:
        """The listing a subject is, in this source's spelling; None where
        its record carries no symbol, or is no record the deployment holds."""
        known = self.products.symbols.get(entity_id)
        if known:
            return known
        if not self.reads_instruments:
            return None
        try:
            found = await self.plugin.resolve_instrument(
                instrument_id=entity_id, as_of_ns=self.clock_ns()
            )
        except meridian.NotGranted:
            # Contract v18 grants a dgm no read of what an instrument is
            # (W3.6): a subject it did not resolve itself is not one it can
            # name to its source. Asked once, and said on the Datasets page.
            log.warning("this dgm may not read an instrument's record; serving its own")
            self.reads_instruments = False
            return None
        if not found.found:
            return None
        record = found.instrument
        if record.currency and record.currency != CURRENCY:
            return None
        symbols = [i for i in record.identifiers if i.scheme == "symbol"]
        own = next((i for i in symbols if i.source == SOURCE and spelled(i.value)), None)
        if own is not None:
            symbol = spelled(own.value) or own.value
        else:
            other = next((i for i in symbols if spelled(i.value)), None)
            if other is None:
                return None
            symbol = spelled(other.value) or other.value
            await self._join(entity_id, symbol, other)
        self.products.put(entity_id, symbol)
        return symbol

    async def _join(self, entity_id: str, symbol: str, other: ops.Identifier) -> None:
        """Report this source's symbol beside the one another source gave the
        record (W3.1): one identifier in common is one security, so the
        record gains this source's code. Ambiguous is a miss (W3.2)."""
        own = meridian.Identifier(scheme="symbol", value=symbol, source=SOURCE)
        given = meridian.Identifier(scheme=other.scheme, value=other.value, source=other.source)
        now = self.clock_ns()
        found = await self.plugin.resolve_identifier(
            identifiers=[own, given], as_of_ns=now, stated_currency=CURRENCY
        )
        if found.found and found.instrument_id == entity_id:
            return
        reason = found.miss_reason or ops.MISS_REASON_AMBIGUOUS
        if found.found:
            # Matched another record: two records for one listing, which a
            # person merges (W3.13).
            reason = ops.MISS_REASON_AMBIGUOUS
        await self.plugin.report_missing_instrument(
            source=SOURCE, identifiers=[own, given], as_of_ns=now, reason=reason
        )

    async def seed(self, symbols: Iterable[str]) -> None:
        """Resolve listings by this source's own symbol (W3.1), so the lake's
        wants for them can be answered: the deployment's record of each,
        found or minted, kept in the product map. What does not resolve is
        reported (W3.2)."""
        now = self.clock_ns()
        for symbol in symbols:
            if symbol in self.products.symbols.values():
                continue
            own = meridian.Identifier(scheme="symbol", value=symbol, source=SOURCE)
            found = await self.plugin.resolve_identifier(
                identifiers=[own], as_of_ns=now, stated_currency=CURRENCY
            )
            if found.instrument_id:
                self.products.put(found.instrument_id, symbol)
                continue
            await self.plugin.report_missing_instrument(
                source=SOURCE,
                identifiers=[own],
                as_of_ns=now,
                reason=found.miss_reason or ops.MISS_REASON_NOT_FOUND,
            )

    async def venue_of(self, code: str) -> tuple[str, list[ops.AsReported]]:
        """The venue master's ID for an exchange the source printed, and what
        to keep as reported where the deployment does not hold it."""
        if not code:
            return "", []
        now = self.clock_ns()
        held = self._venues.get(code)
        if held is not None and (held[0] or now - held[1] < VENUE_MISS_NS):
            return held[0], [] if held[0] else [self._venue_reported(code)]
        identifier = meridian.Identifier(scheme="symbol", value=code, source=SOURCE)
        found = await self.plugin.resolve_venue(identifiers=[identifier], as_of_ns=now)
        if found.found and found.venue.venue_id:
            self._venues[code] = (found.venue.venue_id, now)
            return found.venue.venue_id, []
        await self.plugin.report_missing_venue(
            source=SOURCE,
            identifiers=[identifier],
            as_of_ns=now,
            reason=found.miss_reason or ops.MISS_REASON_NOT_FOUND,
        )
        self._venues[code] = ("", now)
        return "", [self._venue_reported(code)]

    @staticmethod
    def _venue_reported(code: str) -> ops.AsReported:
        from meridian.edge import as_reported

        return as_reported(f"{SOURCE}:venue", code)

    # ── Rows ─────────────────────────────────────────────────────────────

    def _daily_meta(self, entity_id: str, bar: DailyBar, final: bool) -> ObservationMeta:
        day = bar.business_date
        return ObservationMeta(
            row_key=bar.row_key,
            subjects=[ops.SubjectRef(entity_id=entity_id)],
            source=ops.Source(dataset=self.dataset(DAILY)),
            valid_from_ns=previous_close_of(day),
            valid_until_ns=close_of(day),
            business_date=day,
            source_times=[SourceTime(kind="published", at_ns=close_of(day))] if final else [],
            raw=self.plugin.raw_record(bar.raw_key[:256]),
        )

    def final(self, day: dt.date) -> bool:
        """Whether a business date's close is final: its session has closed."""
        return self.clock_ns() >= close_of(day)

    def close(self, entity_id: str, bar: DailyBar) -> Price | None:
        if bar.close is None:
            return None
        return Price(
            meta=self._daily_meta(entity_id, bar, self.final(bar.business_date)),
            kind="close",
            price=Money(bar.close, CURRENCY),
            basis="per_unit",
        )

    def bar(self, entity_id: str, bar: DailyBar) -> Bar | None:
        if not bar.complete or bar.volume is None:
            return None
        assert bar.open is not None and bar.high is not None and bar.low is not None
        assert bar.close is not None
        return Bar(
            meta=self._daily_meta(entity_id, bar, self.final(bar.business_date)),
            open=Money(bar.open, CURRENCY),
            high=Money(bar.high, CURRENCY),
            low=Money(bar.low, CURRENCY),
            close=Money(bar.close, CURRENCY),
            volume=bar.volume,
            vwap=None if bar.vwap is None else Money(bar.vwap, CURRENCY),
            trade_count=bar.trade_count,
        )

    async def live_price(self, entity_id: str, quoted: Quoted) -> Price | None:
        if quoted.amount is None:
            return None
        venue, reported = await self.venue_of(quoted.venue_code)
        return Price(
            meta=ObservationMeta(
                row_key=quoted.row_key,
                subjects=[ops.SubjectRef(entity_id=entity_id)],
                source=ops.Source(dataset=self.dataset(LIVE), venue_id=venue),
                valid_from_ns=quoted.at_ns,
                source_times=[SourceTime(kind="event", at_ns=quoted.at_ns)],
                raw=self.plugin.raw_record(quoted.raw_key[:256]),
                unconverted=reported,
            ),
            kind=quoted.kind,
            price=Money(quoted.amount, CURRENCY),
            basis="per_unit",
        )

    async def _record(self, key: str, data_type: str, rows: Sequence[Any], want_id: str) -> None:
        counts = self.counts[key]
        for batch in _chunks(rows):
            if data_type == BAR:
                done = await self.plugin.record_bars(bars=list(batch), want_id=want_id)
            else:
                done = await self.plugin.record_prices(prices=list(batch), want_id=want_id)
            counts.recorded += done.recorded
            counts.restated += done.restated
            counts.unchanged += done.unchanged
            counts.last_recorded_at_ns = self.clock_ns()

    async def _decline(self, key: str, want_id: str, subjects: Sequence[str], reason: str) -> None:
        if not subjects or not want_id:
            return
        counts = self.counts[key]
        for batch in _chunks(list(subjects)):
            await self.plugin.decline_want(
                want_id=want_id,
                subjects=[ops.SubjectRef(entity_id=s) for s in batch],
                reason=reason,
            )
        counts.declined += len(subjects)
        counts.last_declined = reason

    async def _fetch(self, call: Callable[[], Any]) -> Any:
        """A blocking call to the source, in a thread; None where it failed,
        which is said on the Connection page."""
        try:
            answer = await asyncio.to_thread(call)
        except Exception as failed:  # noqa: BLE001 - the source's failure, shown
            self.last_error = f"{type(failed).__name__}: {failed}"[:300]
            log.warning("the source failed: %s", self.last_error)
            return None
        self.last_error = ""
        return answer

    # ── Daily ────────────────────────────────────────────────────────────

    def _days_wanted(self, want: ops.ObservationsWantedEvent) -> tuple[dt.date, dt.date, bool]:
        """The New York dates a want covers, and whether it asks the latest."""
        if want.business_date:
            day = dt.date.fromisoformat(want.business_date)
            return day, day, False
        if want.valid_from_ns or want.valid_until_ns:
            first = new_york_date(want.valid_from_ns or self.clock_ns())
            last = new_york_date((want.valid_until_ns or self.clock_ns()) - 1)
            return first, max(first, last), False
        today = new_york_date(self.clock_ns())
        return today - dt.timedelta(days=7), today, True

    async def daily(
        self,
        subjects: Sequence[str],
        first: dt.date,
        last: dt.date,
        *,
        data_type: str = PRICE,
        want_id: str = "",
        latest: bool = False,
    ) -> list[str]:
        """Record the closes (or daily bars) of `subjects` over New York dates
        `first` to `last`; the subjects the source had nothing for."""
        counts = self.counts[DAILY]
        symbols: dict[str, str] = {}
        uncovered: list[str] = []
        for subject in subjects:
            symbol = await self.symbol_of(subject)
            if symbol is None:
                uncovered.append(subject)
            else:
                symbols[subject] = symbol
        await self._decline(DAILY, want_id, uncovered, "not_covered")
        today = new_york_date(self.clock_ns())
        if last < HISTORY_FROM or first > today:
            reason = "beyond_history" if last < HISTORY_FROM else "source_silent"
            await self._decline(DAILY, want_id, list(symbols), reason)
            return list(symbols)
        if not symbols or self.source is None:
            await self._decline(DAILY, want_id, list(symbols), "source_silent")
            return list(symbols)
        source = self.source
        wanted = sorted(set(symbols.values()))
        bars = await self._fetch(lambda: source.daily(wanted, max(first, HISTORY_FROM), last))
        if bars is None:
            await self._decline(DAILY, want_id, list(symbols), "source_silent")
            return list(symbols)
        by_symbol: dict[str, list[DailyBar]] = {}
        for bar in bars:
            counts.unconverted += len(bar.unconverted)
            if first <= bar.business_date <= last:
                by_symbol.setdefault(bar.symbol, []).append(bar)
        rows: list[Any] = []
        silent: list[str] = []
        for subject, symbol in symbols.items():
            days = by_symbol.get(symbol, [])
            if latest and days:
                days = [max(days, key=lambda b: b.business_date)]
            made = [
                row
                for row in (
                    self.bar(subject, b) if data_type == BAR else self.close(subject, b)
                    for b in days
                )
                if row is not None
            ]
            if not made:
                silent.append(subject)
                continue
            rows.extend(made)
            for b in days:
                forming = Forming(subject, symbol, b.business_date, data_type)
                if self.clock_ns() < close_of(b.business_date) + FINAL_AFTER_NS:
                    self.forming[forming] = want_id
                else:
                    self.forming.pop(forming, None)
        if rows:
            await self._record(DAILY, data_type, rows, want_id)
        await self._decline(DAILY, want_id, silent, "source_silent")
        return silent

    # ── Live ─────────────────────────────────────────────────────────────

    async def live(
        self, subjects: Sequence[str], kinds: Sequence[str] = LIVE_KINDS, want_id: str = ""
    ) -> list[str]:
        """Record the latest live prices of `subjects`; those the source had
        nothing for."""
        symbols: dict[str, str] = {}
        uncovered: list[str] = []
        for subject in subjects:
            symbol = await self.symbol_of(subject)
            if symbol is None:
                uncovered.append(subject)
            else:
                symbols[subject] = symbol
        await self._decline(LIVE, want_id, uncovered, "not_covered")
        if not symbols or self.source is None:
            await self._decline(LIVE, want_id, list(symbols), "source_silent")
            return list(symbols)
        source = self.source
        wanted = sorted(set(symbols.values()))
        quoted = await self._fetch(lambda: source.live(wanted))
        if quoted is None:
            await self._decline(LIVE, want_id, list(symbols), "source_silent")
            return list(symbols)
        counts = self.counts[LIVE]
        by_symbol: dict[str, list[Quoted]] = {}
        for each in quoted:
            counts.unconverted += len(each.unconverted)
            if each.kind in kinds:
                by_symbol.setdefault(each.symbol, []).append(each)
        rows: list[Price] = []
        silent: list[str] = []
        for subject, symbol in symbols.items():
            made = [await self.live_price(subject, q) for q in by_symbol.get(symbol, [])]
            kept = [row for row in made if row is not None]
            if not kept:
                silent.append(subject)
            rows.extend(kept)
        if rows:
            await self._record(LIVE, PRICE, rows, want_id)
        await self._decline(LIVE, want_id, silent, "source_silent")
        return silent

    # ── Wants (W10.7) ────────────────────────────────────────────────────

    async def on_want(self, heard: meridian.Heard[ops.ObservationsWantedEvent]) -> None:
        """Record what a want asks, against it, or decline it per subject."""
        want = heard.message
        key = self._key_of(want.dataset)
        subjects = [s.entity_id for s in want.subjects]
        if key not in self.counts:
            log.warning("a want for a dataset this plugin does not declare: %s", want.dataset)
            return
        self.counts[key].wants += 1
        kinds = [_kind_name(k) for k in want.kinds]
        if key == DAILY:
            ok_type = want.data_type in (PRICE, BAR)
            ok_kind = want.data_type == BAR or not kinds or "close" in kinds
            ok_interval = want.data_type != BAR or want.interval_ns in (0, DAY_NS)
            if not (ok_type and ok_kind and ok_interval):
                await self._decline(DAILY, want.want_id, subjects, "not_covered")
                return
            first, last, latest = self._days_wanted(want)
            await self.daily(
                subjects,
                first,
                last,
                data_type=want.data_type,
                want_id=want.want_id,
                latest=latest,
            )
            return
        live_kinds = tuple(k for k in (kinds or LIVE_KINDS) if k in LIVE_KINDS)
        if want.data_type != PRICE or not live_kinds:
            await self._decline(LIVE, want.want_id, subjects, "not_covered")
            return
        await self.live(subjects, live_kinds, want.want_id)
        if want.standing:
            self.standing[want.want_id] = Standing(
                want.want_id, tuple(subjects), live_kinds, self.clock_ns()
            )

    async def on_withdrawn(self, heard: meridian.Heard[ops.WantWithdrawnEvent]) -> None:
        """A standing want nobody read within the cadence: no longer kept
        current."""
        self.standing.pop(heard.message.want_id, None)
        for forming, want_id in list(self.forming.items()):
            if want_id == heard.message.want_id:
                self.forming.pop(forming, None)

    # ── The timetable ────────────────────────────────────────────────────

    async def tick(self) -> None:
        """What is due now: each standing want kept current at the live
        cadence; each day still forming read again; and, once a session's
        close is final, the day's closes of every listing served, pushed."""
        now = self.clock_ns()
        for standing in list(self.standing.values()):
            if now - standing.last_at_ns >= LIVE_CADENCE_SECONDS * NS:
                standing.last_at_ns = now
                await self.live(standing.subjects, standing.kinds, standing.want_id)
        if now - self._forming_at >= FORMING_EVERY_NS:
            self._forming_at = now
            for forming, want_id in list(self.forming.items()):
                await self.daily(
                    [forming.entity_id],
                    forming.day,
                    forming.day,
                    data_type=forming.data_type,
                    want_id=want_id,
                )
        await self.push()

    async def push(self) -> None:
        """Once today's close is final, its closes and bars for every listing
        served, unasked (the daily dataset's push mode)."""
        today = new_york_date(self.clock_ns())
        if self.pushed == today or not self.products.symbols:
            return
        if self.clock_ns() < close_of(today) + FINAL_AFTER_NS:
            return
        self.pushed = today
        subjects = sorted(self.products.symbols)
        for data_type in (PRICE, BAR):
            await self.daily(subjects, today, today, data_type=data_type)
