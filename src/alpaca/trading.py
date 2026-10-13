"""The connectivity role (contract v22; meridian-design plans/an-order-reaches-
a-venue-through-the-book; W11.8 to W11.12, W11.20), from 0.7.0, in
SnapTrade 0.16.0's shape (its trading.py), over Alpaca's Trading API
(venue.py, the adapter; broker.py, the client).

**What it does.** It hears each placement naming its own instance, and no
other's (the sidecar delivers a placement to the connectivity plugin it
names alone; one naming another is never acted on). For each queued one it
reads the placement's order from the book (ListOrders), the instrument's
record (ResolveInstrument) for the symbol Alpaca names it by, and places it
at the venue in the account the placement's venue account code names -- the
code its `ems` mapped (`AccountScope.venue_account_map`), Alpaca's account
number -- with the placement's ID as the client order ID. It
reports what the venue did in the contract's meanings (ReportPlacement) and
every fill (ReportExecution), each with the raw record it was converted
from, kept in the plugin's storage with every request and answer (kinds.py,
the `trading` kind).

**Rejections, never refusals.** What the venue or this plugin does not
offer for the account -- an account on which trading was never enabled
(read-only until the connectivity role's admin enables it), an order type or
time in force its catalogue does not list, a strategy (Alpaca works none
for the platform; the `ems` works strategies), an instrument with no symbol,
a code the key reaches no account under -- is the placement rejected
`unsupported`, nothing sent, its raw record what the decision was read from.
What the venue answered with a refusal is `venue rejected`, its code and
words as reported; a status the mapping does not know is outcome unknown,
`venue status not mapped`, the status as reported. A send never answered is
outcome unknown, `request unanswered`, and is resolved by reading the
account's orders for its client order ID, never by sending it again: before
every send the placement is noted in the plugin's storage (`Sent`,
`trading/sent.json`), so a placement the book still holds queued after a
restart is asked about, never sent twice.

**Fills.** Where the venue states each fill (Alpaca's trade updates stream:
its price, quantity and time), the fill is reported as stated (kind `venue
reported`); where it is read from an order's filled quantity growing between
two reads, with only the average price, the fill is inferred (kind
`inferred`), its price worked out from the two averages (exact where it
divides; otherwise rounded half-even to `PRICE_PLACES` places, which the log
says). Either way its ID is derived from the venue's order ID and the filled
quantity it brought the order to, so a fill heard on the stream and read
again on a poll, or read again after a restart, is the same fill
(requirement 33). Its trade date is the date its time falls on in New York,
the listing market's. Alpaca states no fill's settlement date, so every fill
carries the date read's (ComputeDate, `computed_settlement_date` with its
basis); where it answers none, the fill waits for the next read, never sent
with a guessed date.

**Busts.** A filled quantity that falls (a later read, or Alpaca's
`trade_bust`) is the venue taking fills back: each fill reported beyond what
the venue now states is busted, a ReportExecution of quantity 0 superseding
the book's execution, its ID derived as the fill's was.

**The firm's controls.** It reads the trading instructions in force on
start and hears each change. Under a pause or a stop on All accounts, on
this instance, or on a placement's account, it sends nothing new: a queued
placement is held, and sent once the instruction is lifted if the book still
holds it queued. A stop cancels at the venue at once every placement it is
working that the stop covers, and reports each cancel as a fact naming the
stop's entry (RequestPlacementChange with `cause.entry_id`).

**A change the `ems` asks for** is read from the placement's change
requests (`Placement.change_requests`), each by its kind. A cancel is sent
at the venue. A replace is sent where the venue replaces (Alpaca's `PATCH
/v2/orders/{id}`, which answers a new order in the old one's place), and
reported replaced with what the venue answered, its new order followed and
its fills counted on from the old one's. A change the venue refused, or a
replace this plugin or the account's catalogue does not offer, is the
placement rejected while it works, which the book reads as the venue
refusing the change: the request ends refused and the placement works on. A
replace never answered is not sent again: the read showing the order
replaced reports it.

**Its catalogue.** On start, and when trading is enabled on an account, it
publishes per venue account code the order types and times in force the
venue takes for it (ReportVenueCatalogue), and none for a read-only one;
`execution_acknowledgement` unset; never a strategy.

**No venue-originated orders** (ruled 2026-10-13): an order the venue lists
on a mapped account that no placement names is never recorded, never
placed, and its fills are not reported in v22: it is kept in the venue's
answer as received and said once in the log.

**A venue's symbol on a fill** that is not the symbol placed is resolved by
the fill's time (ResolveIdentifier) and, where it names none of the
deployment's records, reported missing (ReportMissingInstrument); the fill
is reported on the placement either way.

What it does not do: a locate (no locate API), a place of execution (the
venue states none on a fill), a charge on a placement (the venue states no
fee naming an order), a venue-originated fill (v23's).
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
import tempfile
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from decimal import ROUND_HALF_EVEN, Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Protocol
from zoneinfo import ZoneInfo

import meridian
from meridian.operations import as_decimal
from meridian.plugin.v1 import operations_pb2 as ops

log = logging.getLogger("alpaca.trading")

#: The places a fill's price worked out from two averages is rounded to
#: where the division is not exact.
PRICE_PLACES = 8
#: The listing market's day a fill's trade date is in: US equities only.
MARKET_ZONE = ZoneInfo("America/New_York")
#: How many polls after a placement ends its order is still read, for a
#: bust after it filled; then it is let go.
ENDED_POLLS = 10
HOLDING = (ops.TRADING_INSTRUCTION_VALUE_PAUSE, ops.TRADING_INSTRUCTION_VALUE_STOP)

Json = dict[str, Any]
#: What the venue offers: each order type with its times in force.
Catalogue = tuple[tuple[int, tuple[int, ...]], ...]

#: The categories a venue's statuses are mapped to (venue.py's STATUSES).
WORKING, FILLED, CANCELLED, REJECTED = "working", "filled", "cancelled", "rejected"
EXPIRED, SUSPENDED, DONE_FOR_DAY, REPLACED = "expired", "suspended", "done for day", "replaced"
ENDING = {CANCELLED: "cancelled", REJECTED: "rejected", EXPIRED: "expired", FILLED: "filled"}


def _ns(moment: datetime) -> int:
    return int(moment.timestamp()) * 1_000_000_000 + moment.microsecond * 1000


def number(value: Any) -> Decimal | None:
    """A JSON number or numeral read exactly, or None."""
    if value is None or isinstance(value, bool):
        return None
    try:
        return Decimal(str(value)) if isinstance(value, (int, str, Decimal)) else None
    except InvalidOperation:
        return None


def moment(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    text = value.replace("Z", "+00:00")
    # Python takes at most six places of a second.
    head, dot, rest = text.partition(".")
    if dot:
        digits = "".join(c for c in rest if c.isdigit())
        zone = rest[len(digits) :]
        text = f"{head}.{digits[:6]}{zone}"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=UTC)


def fill_price(
    before: Decimal, average_before: Decimal | None, after: Decimal, average_after: Decimal
) -> tuple[Decimal, bool]:
    """A fill's price from the averages before and after it, and whether it
    was rounded: exact where the division is."""
    paid = average_after * after - (average_before or Decimal(0)) * before
    quantity = after - before
    price = paid / quantity
    rounded = price.quantize(Decimal(1).scaleb(-PRICE_PLACES), rounding=ROUND_HALF_EVEN)
    exact = rounded * quantity == paid
    return (rounded.normalize() if exact else rounded), not exact


def execution_id(order_id: str, filled: Decimal) -> str:
    """A fill's derived ID: the order and the filled quantity it brought the
    order to, the same however often, and however, it is read."""
    return f"{order_id}:{filled.normalize():f}"


def bust_id(fill: str) -> str:
    return f"{fill}:busted"


def trade_date_of(at: datetime) -> date:
    return at.astimezone(MARKET_ZONE).date()


# ── What the adapter speaks in ──────────────────────────────────────────


@dataclass(frozen=True)
class Asked:
    """A placement as the venue is asked to place it."""

    placement_id: str
    symbol: str
    side: int
    order_type: int
    time_in_force: int
    quantity: Decimal
    limit: Decimal | None = None
    stop: Decimal | None = None


@dataclass(frozen=True)
class Change:
    """A replace as the venue is asked to make it: the quantity, limit and
    time in force now, and the order's type, side and stop as they stand."""

    quantity: Decimal
    limit: Decimal | None
    time_in_force: int
    order_type: int
    side: int
    stop: Decimal | None = None
    quantity_changed: bool = False


@dataclass(frozen=True)
class VenueOrder:
    """One of the venue's orders, as the adapter read it."""

    order_id: str
    client_order_id: str
    status: str
    symbol: str = ""
    quantity: Decimal | None = None
    filled: Decimal = Decimal(0)
    average: Decimal | None = None
    limit: Decimal | None = None
    placed_at: datetime | None = None
    updated_at: datetime | None = None
    filled_at: datetime | None = None
    replaced_by: str = ""
    replaces: str = ""
    # The last fill the venue states, quantity and price, where it does.
    last_fill: tuple[Decimal, Decimal] | None = None
    reason: str = ""


@dataclass(frozen=True)
class Fill:
    """One fill as the venue stated it on its trade updates."""

    quantity: Decimal
    price: Decimal
    at: datetime | None


class VenueRefused(Exception):
    """The venue answered with a refusal: its code and words, and the raw
    record they are in."""

    def __init__(self, code: str, text: str, raw_key: str) -> None:
        super().__init__(f"{code} {text}".strip())
        self.code, self.text, self.raw_key = code, text, raw_key


class VenueUnanswered(Exception):
    """The venue never answered: the request may have reached it."""

    def __init__(self, why: str, raw_key: str) -> None:
        super().__init__(why)
        self.raw_key = raw_key


class Venue(Protocol):
    """What the Trader asks of a venue (venue.py)."""

    #: The venue's statuses by their category; any other is not mapped.
    statuses: dict[str, str]
    #: The sides it places, by the contract's, in its words.
    sides: dict[int, str]
    source: str

    def accounts(self) -> tuple[str, ...]: ...

    def catalogue(self, code: str) -> Catalogue: ...

    def place(self, code: str, asked: Asked) -> tuple[VenueOrder, str]: ...

    def orders(self, code: str) -> tuple[list[VenueOrder], str]: ...

    def cancel(self, code: str, order_id: str) -> str: ...

    def replace(
        self, code: str, order: VenueOrder | None, order_id: str, change: Change
    ) -> tuple[VenueOrder | None, str]: ...

    def not_replaceable(self, change: Change) -> str: ...

    def keep(self, name: str, record: Json) -> str: ...


# ── What it keeps in storage ────────────────────────────────────────────


class Noted:
    """A set of names kept in a JSON file in the plugin's storage, written
    whole on every change: the placements sent (`trading/sent.json`), noted
    before each send so a restart never sends one twice, and the accounts
    trading is enabled on."""

    def __init__(self, file: Path | None) -> None:
        self._file = file
        self._held: set[str] = set()
        if file is not None and file.exists():
            with contextlib.suppress(OSError, ValueError):
                held = json.loads(file.read_text())
                if isinstance(held, list):
                    self._held = {str(each) for each in held}

    def __contains__(self, name: object) -> bool:
        return name in self._held

    def __iter__(self) -> Any:
        return iter(sorted(self._held))

    def note(self, name: str) -> None:
        self._held.add(name)
        if self._file is None:
            return
        self._file.parent.mkdir(parents=True, exist_ok=True)
        made, written = tempfile.mkstemp(dir=self._file.parent, suffix=".tmp")
        try:
            with os.fdopen(made, "w") as file:
                json.dump(sorted(self._held), file)
            os.replace(written, self._file)
        except BaseException:
            Path(written).unlink(missing_ok=True)
            raise


Sent = Noted


@dataclass
class Worked:
    """A placement the venue holds an order for, as this plugin works it."""

    placement_id: str
    order_id: str
    code: str
    instrument_id: str
    currency: str
    venue_order_id: str = ""
    symbol: str = ""
    # The filled quantity reported, and the venue's average price then: of
    # the order it holds now, the fills of any it replaced counted in
    # `carried`.
    filled: Decimal = Decimal(0)
    average: Decimal | None = None
    carried: Decimal = Decimal(0)
    fills: list[tuple[str, Decimal, str]] = field(default_factory=list)
    executions: dict[str, str] = field(default_factory=dict)
    unknown: str = ""
    unanswered: bool = False
    acknowledged: bool = False
    ended: str = ""
    cancel_sent: bool = False
    done_for_day: bool = False
    changes: set[str] = field(default_factory=set)
    replacing: tuple[str, Decimal, ops.Money | None] | None = None
    symbols: set[str] = field(default_factory=set)
    ended_polls: int = 0
    waiting: bool = False
    # The latest moment the venue stated the order changed, as last read:
    # an update heard after a later read is older news, and passed over.
    seen: datetime | None = None


@dataclass
class Shown:
    """What the Connections page shows of trading: counts and states only."""

    working: int = 0
    held: int = 0
    unknown: int = 0
    last_poll: datetime | None = None
    error: str = ""


class Trader:
    """The connectivity role's work, against one venue."""

    def __init__(
        self,
        plugin: Any,
        venue: Callable[[], Venue | None],
        *,
        now: Callable[[], datetime],
        sent: Noted | None = None,
        enabled: Noted | None = None,
        scope: Callable[[], meridian.AccountScope] = meridian.AccountScope,
    ) -> None:
        self._plugin = plugin
        self._venue = venue
        self._now = now
        self._scope = scope
        self.me: str = plugin.identity.instance_id
        self._sent = sent if sent is not None else Noted(None)
        self.enabled = enabled if enabled is not None else Noted(None)
        self.instructions: dict[tuple[Any, ...], ops.TradingInstruction] = {}
        self.working: dict[str, Worked] = {}
        self.held: dict[str, ops.Placement] = {}
        self.decided: set[str] = set()
        self.venue_orders: set[str] = set()
        self.outside: set[str] = set()
        self.watermark: ops.Watermark | None = None
        self.published: dict[str, Catalogue] = {}
        #: The accounts the key reached when last asked, by their codes.
        self.reached: tuple[str, ...] = ()
        self.shown = Shown()
        self._lock = asyncio.Lock()
        #: Set once it has read the book on start: a placement heard before
        #: then is the start's to find.
        self.started = asyncio.Event()

    async def _call(self, work: Callable[..., Any], *args: Any) -> Any:
        """A venue's call, off the loop: its client blocks."""
        return await asyncio.to_thread(work, *args)

    def _raw(self, key: str) -> ops.RawRecordRef:
        reference: ops.RawRecordRef = self._plugin.raw_record(key or "unkept")
        return reference

    def mapped(self) -> frozenset[str]:
        """The venue account codes its `ems` mapped on this instance, as the
        account scope last delivered its map (W11.22)."""
        held = self._scope().venue_account_map
        if held is None:
            return frozenset()
        return frozenset(
            mapping.venue_account_code
            for mapping in held.mappings
            if mapping.connectivity_instance_id == self.me and mapping.venue_account_code
        )

    def trading_on(self, code: str) -> bool:
        """Whether trading is enabled on an account: read-only until the
        connectivity role's admin enables it."""
        return code in self.enabled

    def catalogue_of(self, venue: Venue, code: str) -> Catalogue:
        return venue.catalogue(code) if self.trading_on(code) else ()

    def offered(self, code: str) -> Catalogue:
        """What the venue offers on an account now: none on a read-only one."""
        venue = self._venue()
        return self.catalogue_of(venue, code) if venue is not None else ()

    # ── Reading the book ────────────────────────────────────────────────

    async def start(self) -> None:
        """On start: the instructions in force, its placements' orders, to
        work again what it was working; then its catalogue."""
        listed = await self._plugin.list_trading_instructions()
        for instruction in listed.instructions:
            self._hold(instruction)
        await self.catch_up(since=None)
        await self.publish(fresh=True)

    async def catch_up(self, since: ops.Watermark | None) -> None:
        cursor = ""
        while True:
            listed = await self._plugin.list_orders(since=since, cursor=cursor)
            orders = {order.order_id: order for order in listed.orders}
            for order in listed.orders:
                for placement in order.placements:
                    await self._resume(placement, order, listed.executions)
            for placement in listed.placements:
                await self._resume(placement, orders.get(placement.order_id), listed.executions)
            if listed.HasField("as_of"):
                self.watermark = listed.as_of
            if not listed.next_cursor:
                return
            cursor = listed.next_cursor

    async def _resume(
        self,
        placement: ops.Placement,
        order: ops.Order | None,
        executions: Sequence[ops.Execution],
    ) -> None:
        if placement.connectivity_instance_id != self.me:
            return
        self.venue_orders.update(placement.venue_order_ids)
        if placement.venue_order_id:
            self.venue_orders.add(placement.venue_order_id)
        state = placement.status.state
        worked = self.working.get(placement.placement_id)
        if worked is None and state in (ops.PLACEMENT_STATE_SENT, ops.PLACEMENT_STATE_WORKING):
            worked = await self._worked(placement, order)
            if worked is None:
                return
            worked.acknowledged = bool(placement.venue_order_id)
            for execution in executions:
                if execution.placement_id == placement.placement_id:
                    worked.executions[execution.venue_execution_id] = execution.execution_id
            worked.filled = _filled_in_book(placement.placement_id, executions)
            if worked.venue_order_id:
                worked.carried = _filled_in_book(
                    placement.placement_id, executions, besides=worked.venue_order_id
                )
            worked.unanswered = not worked.venue_order_id
            self.working[placement.placement_id] = worked
            self.decided.add(placement.placement_id)
            log.info("working %s again: %s filled", placement.placement_id, worked.filled)
        if (
            state == ops.PLACEMENT_STATE_QUEUED
            and placement.placement_id not in self.decided
            and placement.placement_id not in self.working
        ):
            await self.on_queued(placement, order)
        if worked is not None:
            await self._asked_to_change(placement, order, worked)

    async def _order_of(self, placement: ops.Placement) -> ops.Order | None:
        listed = await self._plugin.list_orders(account_id=placement.account_id)
        for order in listed.orders:
            if order.order_id == placement.order_id:
                found: ops.Order = order
                return found
        return None

    # ── Hearing ─────────────────────────────────────────────────────────

    async def on_placement(self, heard: Any) -> None:
        placement: ops.Placement = heard.message.placement
        if placement.connectivity_instance_id != self.me or not self.started.is_set():
            return
        async with self._lock:
            await self._resume(placement, None, ())

    async def on_instruction(self, heard: Any) -> None:
        instruction: ops.TradingInstruction = heard.message.instruction
        if not self.started.is_set():
            # Read on start, with every instruction in force.
            return
        async with self._lock:
            self._hold(instruction)
            if instruction.value == ops.TRADING_INSTRUCTION_VALUE_STOP:
                await self._stop(instruction)
            elif instruction.value == ops.TRADING_INSTRUCTION_VALUE_ACTIVE:
                await self._release_held()

    @staticmethod
    def _key(instruction: ops.TradingInstruction) -> tuple[Any, ...]:
        target = instruction.target
        return (
            target.all_accounts,
            target.account_id,
            target.instance_id,
            target.subject,
            target.service_account_id,
            instruction.setter_level,
        )

    def _hold(self, instruction: ops.TradingInstruction) -> None:
        key = self._key(instruction)
        if instruction.value in HOLDING:
            self.instructions[key] = instruction
        else:
            self.instructions.pop(key, None)

    def _covers(self, instruction: ops.TradingInstruction, account_id: str) -> bool:
        """Whether an instruction binds this plugin's placements in an
        account: on All accounts, this instance, or the account. One aimed at
        a person or a service account binds the plugins acting for them."""
        target = instruction.target
        if target.subject or target.service_account_id:
            return False
        if target.instance_id and target.instance_id != self.me:
            return False
        if target.account_id:
            return target.account_id == account_id
        return target.all_accounts or target.instance_id == self.me

    def in_force(self, account_id: str) -> ops.TradingInstruction | None:
        binding = [i for i in self.instructions.values() if self._covers(i, account_id)]
        binding.sort(key=lambda i: i.value != ops.TRADING_INSTRUCTION_VALUE_STOP)
        return binding[0] if binding else None

    async def _stop(self, instruction: ops.TradingInstruction) -> None:
        """A stop's cancels, at the venue at once, each reported as a fact
        naming the stop's entry, never waiting for the book."""
        accounts: dict[str, str] | None = None
        for worked in list(self.working.values()):
            if worked.ended or worked.cancel_sent:
                continue
            if accounts is None:
                listed = await self._plugin.list_orders()
                accounts = {o.order_id: str(o.account_id) for o in listed.orders}
            account = accounts.get(worked.order_id, "")
            if account and not self._covers(instruction, account):
                continue
            await self._cancel(worked, cause=instruction.entry, reason="a stop is in force")

    async def _release_held(self) -> None:
        for placement_id, placement in list(self.held.items()):
            if self.in_force(placement.account_id) is not None:
                continue
            self.held.pop(placement_id, None)
            order = await self._order_of(placement)
            current = next(
                (p for p in (order.placements if order else ()) if p.placement_id == placement_id),
                placement,
            )
            if current.status.state == ops.PLACEMENT_STATE_QUEUED:
                await self.on_queued(current, order)

    # ── Sending ─────────────────────────────────────────────────────────

    async def on_queued(self, placement: ops.Placement, order: ops.Order | None) -> None:
        """A queued placement naming this instance: held, rejected, or sent."""
        pid = placement.placement_id
        if self.in_force(placement.account_id) is not None:
            self.held[pid] = placement
            log.info("holding %s back: trading is paused or stopped", pid)
            return
        if pid in self._sent:
            # Sent before this process: asked about, never sent again.
            worked = await self._worked(placement, order)
            if worked is not None:
                worked.unanswered = True
                self.working[pid] = worked
                self.decided.add(pid)
            return
        self.decided.add(pid)
        order = order if order is not None else await self._order_of(placement)
        venue = self._venue()
        if order is None or venue is None:
            self.decided.discard(pid)
            return
        code = placement.venue_account_code
        reached = await self._reached(venue)
        if code not in reached:
            await self._reject_unsupported(
                venue, placement, "the key reaches no account under this venue account code"
            )
            return
        why = self._unsupported(venue, placement, order)
        if why:
            await self._reject_unsupported(venue, placement, why)
            return
        found = await self._plugin.resolve_instrument(instrument_id=order.instrument_id)
        symbol = symbol_of(found.instrument, venue.source) if found.found else ""
        if not symbol:
            await self._reject_unsupported(
                venue, placement, "the instrument's record holds no symbol to place it under"
            )
            return
        asked = _asked(placement, order, symbol)
        worked = Worked(
            placement_id=pid,
            order_id=order.order_id,
            code=code,
            instrument_id=order.instrument_id,
            currency=found.instrument.currency or "USD",
            symbol=symbol,
        )
        self._sent.note(pid)
        try:
            record, key = await self._call(venue.place, code, asked)
        except VenueRefused as refused:
            await self._plugin.report_placement(
                placement_id=pid,
                event=ops.PLACEMENT_EVENT_REJECTED,
                rejection_reason=ops.REJECTION_REASON_VENUE_REJECTED,
                rejection_as_reported=ops.AsReported(
                    scheme=f"{venue.source}:error",
                    code=refused.code,
                    text=refused.text or "the venue refused the order",
                ),
                raw_record=self._raw(refused.raw_key),
            )
            return
        except VenueUnanswered as lost:
            worked.unanswered = True
            self.working[pid] = worked
            await self._plugin.report_placement(
                placement_id=pid,
                event=ops.PLACEMENT_EVENT_OUTCOME_UNKNOWN,
                unknown_reason=ops.UNKNOWN_REASON_REQUEST_UNANSWERED,
                raw_record=self._raw(lost.raw_key),
            )
            return
        worked.venue_order_id = record.order_id
        self.working[pid] = worked
        await self._report_status(venue, worked, record, key)

    def _unsupported(self, venue: Venue, placement: ops.Placement, order: ops.Order) -> str:
        code = placement.venue_account_code
        if not self.trading_on(code):
            return "trading is not enabled on the account: it is read-only"
        if placement.HasField("strategy") and placement.strategy.kind:
            return "the venue works no strategy for the platform: the ems works strategies"
        if order.side not in venue.sides:
            return "the venue takes no such side"
        offered = dict(venue.catalogue(code))
        if order.order_type not in offered:
            return "the account's catalogue lists no such order type"
        tif = placement.time_in_force or order.time_in_force
        if tif not in offered[order.order_type]:
            return "the account's catalogue lists no such time in force for the order type"
        return ""

    async def _reject_unsupported(self, venue: Venue, placement: ops.Placement, why: str) -> None:
        code = placement.venue_account_code
        stated: Json = {
            "why": why,
            "venue_account_code": code,
            "trading_enabled": self.trading_on(code),
            "catalogue": [
                {
                    "order_type": ops.OrderType.Name(t),
                    "times_in_force": [ops.TimeInForce.Name(each) for each in tifs],
                }
                for t, tifs in self.catalogue_of(venue, code)
            ],
        }
        key = await self._call(
            venue.keep, "offered", {"call": "stating what the venue offers", "stated": stated}
        )
        log.info("rejected %s unsupported: %s", placement.placement_id, why)
        await self._plugin.report_placement(
            placement_id=placement.placement_id,
            event=ops.PLACEMENT_EVENT_REJECTED,
            rejection_reason=ops.REJECTION_REASON_UNSUPPORTED,
            raw_record=self._raw(key),
        )

    async def _worked(self, placement: ops.Placement, order: ops.Order | None) -> Worked | None:
        order = order if order is not None else await self._order_of(placement)
        venue = self._venue()
        if order is None or venue is None:
            return None
        found = await self._plugin.resolve_instrument(instrument_id=order.instrument_id)
        return Worked(
            placement_id=placement.placement_id,
            order_id=order.order_id,
            code=placement.venue_account_code,
            instrument_id=order.instrument_id,
            currency=(found.instrument.currency if found.found else "") or "USD",
            venue_order_id=placement.venue_order_id,
            symbol=symbol_of(found.instrument, venue.source) if found.found else "",
        )

    # ── Changing: a cancel or a replace ─────────────────────────────────

    async def _asked_to_change(
        self, placement: ops.Placement, order: ops.Order | None, worked: Worked
    ) -> None:
        pending = [
            request
            for request in placement.change_requests
            if request.outcome == ops.CHANGE_REQUEST_OUTCOME_PENDING
            and request.HasField("placement_change")
            and request.change_request_id not in worked.changes
        ]
        if not pending or worked.ended:
            return
        if not worked.acknowledged or not worked.venue_order_id:
            # Answered once the venue has acknowledged the order: the book
            # delivers the placement again with its acknowledgement.
            return
        worked.changes.update(request.change_request_id for request in pending)
        kinds = [request.placement_change.kind for request in pending]
        if ops.PLACEMENT_CHANGE_KIND_CANCEL in kinds:
            if not worked.cancel_sent:
                await self._cancel(worked, cause=None, reason="")
            return
        replaces = [
            request
            for request in pending
            if request.placement_change.kind == ops.PLACEMENT_CHANGE_KIND_REPLACE
        ]
        if replaces:
            await self._replace(placement, order, worked, replaces[-1])

    async def _cancel(self, worked: Worked, *, cause: ops.EntryMeta | None, reason: str) -> None:
        venue = self._venue()
        if venue is None or not worked.venue_order_id:
            return
        worked.cancel_sent = True
        try:
            await self._call(venue.cancel, worked.code, worked.venue_order_id)
        except VenueRefused as refused:
            log.info("%s: the cancel was not taken: %s", worked.placement_id, refused)
            if cause is None:
                # The venue refused the cancel the `ems` asked for: the
                # request ends refused, as a venue's OrderCancelReject. A
                # stop's cancel refused is no fact.
                await self._change_refused(
                    venue, worked, refused.code, refused.text, refused.raw_key
                )
            return
        except VenueUnanswered as lost:
            # The venue's next answer says what became of the order.
            log.info("%s: the cancel was not answered: %s", worked.placement_id, lost)
            return
        if cause is not None:
            await self._plugin.request_placement_change(
                placement_id=worked.placement_id,
                change=meridian.PlacementChange(kind=ops.PLACEMENT_CHANGE_KIND_CANCEL),
                reason=reason,
                cause=ops.EntryMeta(entry_id=cause.entry_id),
                idempotency_key=f"stop:{cause.entry_id}:{worked.placement_id}",
            )

    async def _replace(
        self,
        placement: ops.Placement,
        order: ops.Order | None,
        worked: Worked,
        request: ops.ChangeRequest,
    ) -> None:
        venue = self._venue()
        order = order if order is not None else await self._order_of(placement)
        if venue is None or order is None or not worked.venue_order_id:
            return
        change = request.placement_change
        quantity = (
            as_decimal(change.quantity)
            if change.HasField("quantity")
            else as_decimal(placement.quantity)
        )
        limit = (
            change.limit_price
            if change.HasField("limit_price")
            else (placement.limit_price if placement.HasField("limit_price") else None)
        )
        tif = change.time_in_force or placement.time_in_force or order.time_in_force
        asked = Change(
            quantity=quantity,
            limit=None if limit is None else as_decimal(limit.amount),
            time_in_force=tif,
            order_type=order.order_type,
            side=order.side,
            stop=as_decimal(order.stop_price.amount) if order.HasField("stop_price") else None,
            quantity_changed=quantity != as_decimal(placement.quantity),
        )
        offered = dict(self.catalogue_of(venue, worked.code))
        why = venue.not_replaceable(asked)
        if not why and tif not in offered.get(order.order_type, ()):
            why = "the account's catalogue lists no such time in force for the order type"
        if why:
            key = await self._call(
                venue.keep, "offered", {"call": "stating what the venue offers", "why": why}
            )
            log.info("%s: the replace was rejected unsupported: %s", worked.placement_id, why)
            await self._plugin.report_placement(
                placement_id=worked.placement_id,
                event=ops.PLACEMENT_EVENT_REJECTED,
                rejection_reason=ops.REJECTION_REASON_UNSUPPORTED,
                raw_record=self._raw(key),
            )
            return
        try:
            record, key = await self._call(
                venue.replace, worked.code, None, worked.venue_order_id, asked
            )
        except VenueRefused as refused:
            await self._change_refused(venue, worked, refused.code, refused.text, refused.raw_key)
            return
        except VenueUnanswered:
            worked.replacing = (request.change_request_id, quantity, limit)
            log.info("%s: the replace was not answered; asked about each read", worked.placement_id)
            return
        await self._replaced(worked, record, quantity, limit, key)

    async def _replaced(
        self,
        worked: Worked,
        record: VenueOrder | None,
        quantity: Decimal,
        limit: ops.Money | None,
        key: str,
        event: int = ops.PLACEMENT_EVENT_REPLACED,
    ) -> None:
        """A replace the venue took, reported with the quantity and limit now;
        a new order, where the venue gave one, followed from here, the old
        one's fills carried."""
        worked.replacing = None
        now_id = (record.order_id if record is not None else "") or worked.venue_order_id
        if now_id != worked.venue_order_id:
            worked.carried = worked.filled
            worked.average = None
            worked.seen = None
            worked.venue_order_id = now_id
            self.venue_orders.add(now_id)
        await self._plugin.report_placement(
            placement_id=worked.placement_id,
            event=event,
            venue_order_id=now_id,
            quantity=quantity,
            limit_price=(
                None
                if limit is None
                else meridian.Money(as_decimal(limit.amount), limit.currency_code)
            ),
            raw_record=self._raw(key),
        )

    async def _change_refused(
        self, venue: Venue, worked: Worked, code: str, text: str, key: str
    ) -> None:
        """The venue refused a change: the placement rejected while it works,
        the request ending refused, the venue's code and words as reported."""
        await self._plugin.report_placement(
            placement_id=worked.placement_id,
            event=ops.PLACEMENT_EVENT_REJECTED,
            rejection_reason=ops.REJECTION_REASON_VENUE_REJECTED,
            rejection_as_reported=ops.AsReported(
                scheme=f"{venue.source}:error", code=code, text=text or "the venue refused"
            ),
            raw_record=self._raw(key),
        )

    # ── Reading the venue ───────────────────────────────────────────────

    async def poll(self) -> None:
        """One poll: the book's placements changed since the last (a backstop
        for a delivery lost), then each account's orders at the venue."""
        async with self._lock:
            venue = self._venue()
            if venue is None:
                return
            if self.watermark is not None:
                await self.catch_up(since=self.watermark)
            for worked in self.working.values():
                if worked.ended:
                    worked.ended_polls += 1
            watched = {w.code for w in self.working.values() if w.ended_polls <= ENDED_POLLS}
            codes = sorted(watched | (self.mapped() & set(await self._reached(venue))))
            for code in codes:
                try:
                    orders, key = await self._call(venue.orders, code)
                except (VenueRefused, VenueUnanswered) as failed:
                    self.shown.error = str(failed)
                    log.warning("%s: the orders were not read: %s", code, failed)
                    continue
                await self._read_orders(venue, code, orders, key)
            self.shown = Shown(
                working=sum(1 for w in self.working.values() if not w.ended),
                held=len(self.held),
                unknown=sum(1 for w in self.working.values() if w.unknown or w.unanswered),
                last_poll=self._now(),
            )

    async def _read_orders(
        self, venue: Venue, code: str, orders: list[VenueOrder], key: str
    ) -> None:
        """An account's orders as the venue answered them: each placement's
        order reported as it stands now."""
        by_id = {o.order_id: o for o in orders}
        by_client = {o.client_order_id: o for o in orders if o.client_order_id}
        mine = [w for w in self.working.values() if w.code == code]
        for worked in mine:
            if worked.ended and not (
                worked.ended == "filled" and worked.ended_polls <= ENDED_POLLS
            ):
                continue
            if worked.unanswered:
                record = by_client.get(worked.placement_id)
                if record is None:
                    continue
                worked.venue_order_id = record.order_id
                worked.unanswered = False
            record = by_id.get(worked.venue_order_id)
            if record is None:
                continue
            await self._report_status(venue, worked, record, key, by_id)
        if any(w.unanswered for w in mine):
            return
        placed = {w.venue_order_id for w in self.working.values()} | self.venue_orders
        for record in orders:
            if record.order_id in placed or record.order_id in self.outside:
                continue
            if any(record.client_order_id == w.placement_id for w in self.working.values()):
                continue
            if record.replaces and record.replaces in placed:
                continue
            self.outside.add(record.order_id)
            log.info(
                "%s: the venue lists an order no placement names (%s), placed outside the "
                "platform: kept as received, not reported",
                code,
                record.order_id,
            )

    async def on_update(self, event: str, record: VenueOrder, fill: Fill | None, key: str) -> None:
        """One of the venue's trade updates (Alpaca's stream): the order as it
        stands, and the fill as the venue stated it, where it is one."""
        async with self._lock:
            venue = self._venue()
            if venue is None:
                return
            worked = next(
                (
                    w
                    for w in self.working.values()
                    if w.venue_order_id == record.order_id
                    or (not w.venue_order_id and w.placement_id == record.client_order_id)
                    or (
                        w.venue_order_id
                        and w.venue_order_id == record.replaces
                        and w.replacing is not None
                    )
                ),
                None,
            )
            if worked is None:
                return
            if worked.unanswered and not worked.venue_order_id:
                worked.venue_order_id = record.order_id
                worked.unanswered = False
            if event in ("order_replace_rejected", "order_cancel_rejected"):
                if worked.replacing is not None or worked.cancel_sent:
                    worked.replacing = None
                    worked.cancel_sent = False
                    await self._change_refused(venue, worked, event, record.reason or event, key)
                return
            if (
                record.order_id != worked.venue_order_id
                and record.replaces == worked.venue_order_id
            ):
                _, quantity, limit = worked.replacing or ("", record.quantity or Decimal(0), None)
                await self._replaced(worked, record, quantity, limit, key)
            await self._report_status(venue, worked, record, key, None, fill)

    async def _report_status(
        self,
        venue: Venue,
        worked: Worked,
        record: VenueOrder,
        key: str,
        by_id: dict[str, VenueOrder] | None = None,
        fill: Fill | None = None,
    ) -> None:
        """What the venue's record of the order says now, as facts."""
        if record.order_id == worked.venue_order_id and record.updated_at is not None:
            if worked.seen is not None and record.updated_at < worked.seen:
                return
            worked.seen = record.updated_at
        category = venue.statuses.get(record.status)
        if category is None:
            if worked.unknown != record.status:
                worked.unknown = record.status
                await self._plugin.report_placement(
                    placement_id=worked.placement_id,
                    event=ops.PLACEMENT_EVENT_OUTCOME_UNKNOWN,
                    unknown_reason=ops.UNKNOWN_REASON_VENUE_STATUS_NOT_MAPPED,
                    status_as_reported=ops.AsReported(
                        scheme=f"{venue.source}:order-status",
                        code=record.status,
                        text=record.status,
                    ),
                    raw_record=self._raw(key),
                )
            return
        if category == REJECTED and not worked.ended:
            worked.ended = "rejected"
            await self._plugin.report_placement(
                placement_id=worked.placement_id,
                event=ops.PLACEMENT_EVENT_REJECTED,
                rejection_reason=ops.REJECTION_REASON_VENUE_REJECTED,
                rejection_as_reported=ops.AsReported(
                    scheme=f"{venue.source}:order-status",
                    code=record.status,
                    text=record.reason or record.status,
                ),
                raw_record=self._raw(key),
            )
            return
        if not worked.acknowledged or worked.unknown:
            worked.acknowledged = True
            worked.unknown = ""
            if worked.venue_order_id:
                self.venue_orders.add(worked.venue_order_id)
            await self._plugin.report_placement(
                placement_id=worked.placement_id,
                event=ops.PLACEMENT_EVENT_ACKNOWLEDGED,
                venue_order_id=worked.venue_order_id,
                routed_at_ns=_ns(record.placed_at or self._now()),
                routed_precision=ops.TIME_PRECISION_MICROSECOND,
                raw_record=self._raw(key),
            )
        if category == REPLACED and record.replaced_by and by_id is not None:
            successor = by_id.get(record.replaced_by)
            await self._fills(venue, worked, record, key, None)
            if successor is not None:
                if worked.replacing is not None:
                    _, quantity, limit = worked.replacing
                    await self._replaced(worked, successor, quantity, limit, key)
                else:
                    # The venue replaced it on its own (a corporate action,
                    # say): restated, and its new order followed.
                    await self._replaced(
                        worked,
                        successor,
                        successor.quantity or Decimal(0),
                        None,
                        key,
                        event=ops.PLACEMENT_EVENT_RESTATED,
                    )
                await self._report_status(venue, worked, successor, key, by_id)
            return
        if worked.replacing is not None and category == WORKING:
            # A replace the venue never answered, shown taken in place.
            _, quantity, limit = worked.replacing
            if record.quantity == quantity and (
                limit is None or record.limit == as_decimal(limit.amount)
            ):
                await self._replaced(worked, record, quantity, limit, key)
        await self._fills(venue, worked, record, key, fill)
        if category in (CANCELLED, EXPIRED) and not worked.ended:
            worked.ended = ENDING[category]
            await self._plugin.report_placement(
                placement_id=worked.placement_id,
                event=(
                    ops.PLACEMENT_EVENT_CANCELLED
                    if category == CANCELLED
                    else ops.PLACEMENT_EVENT_EXPIRED
                ),
                raw_record=self._raw(key),
            )
        elif category == SUSPENDED:
            await self._plugin.report_placement(
                placement_id=worked.placement_id,
                event=ops.PLACEMENT_EVENT_SUSPENDED,
                raw_record=self._raw(key),
            )
        elif category == DONE_FOR_DAY and not worked.done_for_day:
            worked.done_for_day = True
            await self._plugin.report_placement(
                placement_id=worked.placement_id,
                event=ops.PLACEMENT_EVENT_DONE_FOR_DAY,
                raw_record=self._raw(key),
            )
        elif category == FILLED:
            worked.ended = "filled"

    async def _fills(
        self, venue: Venue, worked: Worked, record: VenueOrder, key: str, fill: Fill | None
    ) -> None:
        """A fill from the order's filled quantity growing: of the order the
        venue holds now, counted on from the fills of any it replaced; as the
        venue stated it where it did, else inferred from the averages."""
        own = record.filled
        filled = worked.carried + own
        if filled < worked.filled:
            await self._busts(worked, filled, key)
        if filled <= worked.filled:
            return
        grown = filled - worked.filled
        kind = ops.EXECUTION_KIND_VENUE_REPORTED
        at = record.filled_at or record.updated_at
        if fill is not None and fill.quantity == grown:
            price, at = fill.price, fill.at or at
        elif record.last_fill is not None and record.last_fill[0] == grown:
            price = record.last_fill[1]
        elif record.average is not None:
            price, rounded = fill_price(
                worked.filled - worked.carried, worked.average, own, record.average
            )
            kind = ops.EXECUTION_KIND_INFERRED
            if rounded:
                log.info(
                    "%s: a fill's price rounded to %s places: %s",
                    worked.placement_id,
                    PRICE_PLACES,
                    price,
                )
        else:
            return
        at = at or self._now()
        traded = trade_date_of(at)
        await self._symbol_filled(venue, worked, record, at)
        settles = await self._plugin.compute_date(
            settlement=ops.SettlementDateInputs(
                instrument_id=worked.instrument_id, trade_date=traded.isoformat()
            ),
            as_of_ns=_ns(at),
        )
        if not (settles.outcome == ops.DATE_OUTCOME_COMPUTED and settles.date):
            # Never sent with a guessed date: asked again on the next read.
            if not worked.waiting:
                worked.waiting = True
                log.info(
                    "%s: no settlement date yet (%s); asked again each read",
                    worked.placement_id,
                    ops.DateOutcome.Name(settles.outcome),
                )
            return
        worked.waiting = False
        derived = execution_id(worked.venue_order_id, own)
        done = await self._plugin.report_execution(
            placement_id=worked.placement_id,
            venue_account_code=worked.code,
            venue_execution_id=derived,
            kind=kind,
            source=ops.EXECUTION_SOURCE_PRIMARY,
            quantity=grown,
            price=meridian.Money(price, worked.currency),
            trade_time_ns=_ns(at),
            trade_date=traded.isoformat(),
            finality=meridian.Finality(level=ops.FINALITY_LEVEL_FINAL, at_ns=_ns(at)),
            computed_settlement_date=settles.date,
            settlement_date_basis=basis_of(settles.basis),
            raw_record=self._raw(key),
            idempotency_key=f"fill:{derived}",
        )
        if done.HasField("execution") and done.execution.execution_id:
            worked.executions[derived] = done.execution.execution_id
        worked.fills.append((derived, filled, traded.isoformat()))
        worked.filled = filled
        worked.average = record.average

    async def _symbol_filled(
        self, venue: Venue, worked: Worked, record: VenueOrder, at: datetime
    ) -> None:
        """The venue's symbol on a fill, where it is not the symbol placed:
        resolved by the fill's time and reported missing where it names none
        of the deployment's records, once per symbol."""
        said = record.symbol
        if not said or said == worked.symbol or said in worked.symbols:
            return
        worked.symbols.add(said)
        identifiers = [meridian.Identifier(scheme="symbol", value=said, source=venue.source)]
        found = await self._plugin.resolve_identifier(identifiers=identifiers, as_of_ns=_ns(at))
        if found.found:
            if found.instrument_id != worked.instrument_id:
                log.warning(
                    "%s: filled under %s, which names %s, not the order's %s",
                    worked.placement_id,
                    said,
                    found.instrument_id,
                    worked.instrument_id,
                )
            return
        await self._plugin.report_missing_instrument(
            source=venue.source,
            identifiers=identifiers,
            as_of_ns=_ns(at),
            reason=found.miss_reason or ops.MISS_REASON_NOT_FOUND,
            observed_at_ns=_ns(self._now()),
        )

    async def _busts(self, worked: Worked, filled: Decimal, key: str) -> None:
        """Each fill reported beyond what the venue now states filled, busted."""
        for fill, cumulative, traded in sorted(worked.fills, key=lambda f: f[1], reverse=True):
            if cumulative <= filled:
                break
            busted = worked.executions.get(fill) or await self._execution_in_book(worked, fill)
            if not busted:
                log.warning(
                    "%s: no execution in the book for %s to bust", worked.placement_id, fill
                )
                continue
            await self._plugin.report_execution(
                placement_id=worked.placement_id,
                venue_account_code=worked.code,
                venue_execution_id=bust_id(fill),
                kind=ops.EXECUTION_KIND_VENUE_REPORTED,
                source=ops.EXECUTION_SOURCE_PRIMARY,
                quantity=Decimal(0),
                price=meridian.Money(worked.average or Decimal(0), worked.currency),
                trade_date=traded,
                supersedes=busted,
                raw_record=self._raw(key),
                idempotency_key=f"bust:{fill}",
            )
            worked.fills.remove((fill, cumulative, traded))
        worked.filled = max([c for _, c, _ in worked.fills], default=Decimal(0))
        worked.carried = min(worked.carried, worked.filled)
        if worked.filled == worked.carried:
            worked.average = None

    async def _execution_in_book(self, worked: Worked, fill: str) -> str:
        listed = await self._plugin.list_orders()
        for execution in listed.executions:
            if (
                execution.placement_id == worked.placement_id
                and execution.venue_execution_id == fill
            ):
                return str(execution.execution_id)
        return ""

    # ── Accounts and the catalogue ──────────────────────────────────────

    async def _reached(self, venue: Venue) -> tuple[str, ...]:
        try:
            reached: tuple[str, ...] = await self._call(venue.accounts)
        except (VenueRefused, VenueUnanswered) as failed:
            self.shown.error = str(failed)
            log.warning("the accounts were not read: %s", failed)
            return ()
        self.reached = reached
        return reached

    async def enable(self, code: str, why: Json) -> None:
        """Trading enabled on an account by the connectivity role's admin:
        noted in storage, kept as a trading record, and its catalogue
        published at once."""
        async with self._lock:
            venue = self._venue()
            self.enabled.note(code)
            if venue is not None:
                await self._call(venue.keep, "enabled", {"call": "enabling trading", **why})
        await self.publish(fresh=True)

    async def publish(self, fresh: bool = False) -> None:
        """Each venue account code's catalogue, where it changed since the
        last published (W11.20)."""
        venue = self._venue()
        if venue is None:
            return
        stated_at = _ns(self._now())
        for code in sorted(await self._reached(venue)):
            offered = self.catalogue_of(venue, code)
            if code in self.published and self.published[code] == offered:
                continue
            await self._plugin.report_venue_catalogue(
                catalogue=meridian.VenueCatalogue(
                    venue_account_code=code,
                    order_types=[
                        meridian.VenueOrderType(
                            order_type=ops.OrderType.Name(each),
                            times_in_force=[ops.TimeInForce.Name(t) for t in tifs],
                            stated_at_ns=stated_at,
                        )
                        for each, tifs in offered
                    ],
                )
            )
            self.published[code] = offered

    # ── Running ─────────────────────────────────────────────────────────

    async def run(self, poll_seconds: Callable[[], float], hear: bool = True) -> None:
        """Start, hear placements and instructions (unless the plugin hears
        them for it, beside its other rows), and poll until cancelled."""
        while self._venue() is None:
            await asyncio.sleep(1)
        await self.start()
        self.started.set()
        hearing = (
            asyncio.create_task(
                self._plugin.receive(
                    placement_updated=self.on_placement,
                    trading_instruction_changed=self.on_instruction,
                    seed=False,
                )
            )
            if hear
            else None
        )
        try:
            while True:
                try:
                    await self.poll()
                    await self.publish()
                except meridian.MeridianError as failed:
                    self.shown.error = str(failed)
                    log.warning("this poll stopped: %s", failed)
                await asyncio.sleep(poll_seconds())
        finally:
            if hearing is not None:
                hearing.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await hearing


def _filled_in_book(
    placement_id: str, executions: Sequence[ops.Execution], besides: str = ""
) -> Decimal:
    """What the book holds filled on a placement, each bust taken off; with
    `besides`, only the fills of venue orders other than it."""
    busted = {e.supersedes for e in executions if e.supersedes}
    total = Decimal(0)
    for execution in executions:
        if execution.placement_id != placement_id or execution.execution_id in busted:
            continue
        if execution.supersedes:
            continue
        if besides and execution.venue_execution_id.startswith(f"{besides}:"):
            continue
        total += as_decimal(execution.quantity)
    return total


def symbol_of(record: ops.InstrumentRecord, source: str) -> str:
    """The symbol the venue names an instrument by: a symbol under the
    venue's own source first, any symbol else; "" for none."""
    symbols = [held for held in record.identifiers if held.scheme == "symbol" and held.value]
    for held in symbols:
        if held.source == source:
            return str(held.value)
    return str(symbols[0].value) if symbols else ""


def _asked(placement: ops.Placement, order: ops.Order, symbol: str) -> Asked:
    price = (
        placement.limit_price
        if placement.HasField("limit_price")
        else (order.limit_price if order.HasField("limit_price") else None)
    )
    stop = order.stop_price if order.HasField("stop_price") else None
    return Asked(
        placement_id=placement.placement_id,
        symbol=symbol,
        side=order.side,
        order_type=order.order_type,
        time_in_force=placement.time_in_force or order.time_in_force,
        quantity=as_decimal(placement.quantity),
        limit=None if price is None else as_decimal(price.amount),
        stop=None if stop is None else as_decimal(stop.amount),
    )


def basis_of(basis: ops.DateBasis) -> meridian.DateBasis:
    """The date read's basis, as the SDK's DateBasis carries it on a fill."""
    rule = basis.rule
    return meridian.DateBasis(
        rule=meridian.DateRuleRef(kind=rule.kind, subject=rule.subject, version=rule.version),
        calendars=list(basis.calendars),
    )


def trading_files(base: Path | None) -> tuple[Path | None, Path | None]:
    """Where the placements sent and the accounts trading is enabled on are
    noted: `trading/sent.json` and `trading/enabled.json` in storage."""
    if base is None:
        return None, None
    return base / "trading" / "sent.json", base / "trading" / "enabled.json"
