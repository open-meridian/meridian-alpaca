"""Alpaca's connectivity work run against the suites' recorder: the plugin's
own Trader (trading.py) over its own venue adapter (venue.py) and Trading API
client (broker.py), speaking to the simulated brokerage
(synthetic_broker.py); only the sidecar is stood in for by the recorder.

Shared by the connectivity suite's test, the trading tests and the custody
suite's `asks-for-a-settlement-date`. SIMULATED ONLY: nothing here reaches
Alpaca, its paper trading API or any brokerage.
"""

from __future__ import annotations

import tempfile
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

import meridian
from meridian.plugin.v1 import operations_pb2 as ops
from meridian.suites import Recorder

from alpaca.broker import PAPER_BASE, Broker
from alpaca.kinds import Store
from alpaca.synthetic_broker import ACCOUNT_NUMBER, SyntheticBroker
from alpaca.trading import Noted, Trader
from alpaca.updates import SyntheticUpdates
from alpaca.vendor import RateLimiter
from alpaca.venue import AlpacaVenue

Producer = Callable[[Recorder], Awaitable[None]]

CODE = ACCOUNT_NUMBER
ACCOUNT = "ACC-01JAE000000000000000000001"
INSTRUMENT = "INS-01J8XQ4M7K0000000000AAPL"
CALENDAR = "CAL-01JAE0000000000000000USSS"
NOW = datetime(2026, 9, 29, 14, 30, tzinfo=UTC)


def clock() -> datetime:
    return NOW


def decimal(text: str) -> ops.Decimal:
    exact = Decimal(text)
    scale = max(0, -int(exact.as_tuple().exponent))
    units = int(exact.scaleb(scale))
    return ops.Decimal(high=units >> 64, low=units & ((1 << 64) - 1), scale=scale)


def usd(text: str) -> ops.Money:
    return ops.Money(amount=decimal(text), currency_code="USD")


def placement(
    pid: str = "PLC-01JAE00000000000000000001",
    *,
    quantity: str = "10",
    instance: str = "alpaca",
    code: str = CODE,
    tif: ops.TimeInForce = ops.TIME_IN_FORCE_DAY,
    state: ops.PlacementState = ops.PLACEMENT_STATE_QUEUED,
    strategy: ops.StrategyInstruction | None = None,
    venue_order_id: str = "",
) -> ops.Placement:
    held = ops.Placement(
        placement_id=pid,
        order_id="ORD-" + pid[4:],
        account_id=ACCOUNT,
        ems_instance_id="desk",
        connectivity_instance_id=instance,
        venue_account_code=code,
        quantity=decimal(quantity),
        limit_price=usd("230.00"),
        time_in_force=tif,
        status=ops.PlacementStatus(state=state),
        venue_order_id=venue_order_id,
    )
    if strategy is not None:
        held.strategy.CopyFrom(strategy)
    return held


def asking(
    held: ops.Placement,
    kind: ops.PlacementChangeKind,
    crq: str = "CRQ-01JAE00000000000000000001",
    **change: Any,
) -> ops.Placement:
    """The placement as the book delivers it with a change requested."""
    asked = ops.Placement()
    asked.CopyFrom(held)
    asked.change_requests.append(
        ops.ChangeRequest(
            change_request_id=crq,
            order_id=held.order_id,
            placement_id=held.placement_id,
            placement_change=ops.PlacementChange(kind=kind, **change),
            outcome=ops.CHANGE_REQUEST_OUTCOME_PENDING,
        )
    )
    return asked


def scope(*codes: str, instance: str = "alpaca") -> meridian.AccountScope:
    return meridian.AccountScope(
        venue_account_map=meridian.VenueAccountMap(
            ems_instance_id="desk",
            mappings=tuple(meridian.VenueAccountMapping(instance, code, ACCOUNT) for code in codes),
        )
    )


def order_of(held: ops.Placement, **changes: Any) -> ops.Order:
    order = ops.Order(
        order_id=held.order_id,
        account_id=ACCOUNT,
        instrument_id=changes.pop("instrument_id", INSTRUMENT),
        side=changes.pop("side", ops.SIDE_BUY),
        quantity=held.quantity,
        order_type=changes.pop("order_type", ops.ORDER_TYPE_LIMIT),
        limit_price=usd("230.00"),
        time_in_force=changes.pop("time_in_force", held.time_in_force),
    )
    order.placements.append(held)
    return order


def instrument(symbol: str) -> ops.InstrumentRecord:
    return ops.InstrumentRecord(
        instrument_id=INSTRUMENT,
        currency="USD",
        identifiers=[ops.Identifier(scheme="symbol", value=symbol, source="alpaca")],
    )


def settled() -> ops.ComputeDateResult:
    return ops.ComputeDateResult(
        date="2026-09-30",
        outcome=ops.DATE_OUTCOME_COMPUTED,
        basis=ops.DateBasis(
            rule=ops.DateRuleRef(subject="XNAS", version=1),
            calendars=[ops.CalendarRef(calendar_id=CALENDAR, version=3)],
        ),
        business_day=True,
    )


class Rig:
    """A Trader over the recorder and the simulated brokerage, trading
    enabled on its account; the book's answers as the recorder gives them."""

    def __init__(
        self,
        recorder: Recorder,
        symbol: str = "AAPL",
        sent: Noted | None = None,
        mapped: tuple[str, ...] = (CODE,),
        enabled: bool = True,
        broker: SyntheticBroker | None = None,
    ) -> None:
        self.recorder = recorder
        self.sent = sent
        self.scope = scope(*mapped)
        self.brokerage = broker or SyntheticBroker(clock)
        self.store = Store(Path(tempfile.mkdtemp()))
        self.broker = Broker(
            self.store,
            None,
            PAPER_BASE,
            send=self.brokerage,
            limiter=RateLimiter(per_minute=6_000_000, sleep=lambda _: None),
            now=clock,
            keyless=True,
            synthetic=True,
        )
        self.venue = AlpacaVenue(self.broker)
        self.updates = SyntheticUpdates(self.brokerage)
        self.enabled = Noted(None)
        if enabled:
            self.enabled.note(CODE)
        self.orders: list[ops.Order] = []
        self.executions: list[ops.Execution] = []
        self.minted = 0
        recorder.answer("ListOrders", self._listed)
        recorder.answer(
            "ResolveInstrument",
            lambda _: ops.ResolveInstrumentResult(found=True, instrument=instrument(symbol)),
        )
        recorder.answer("ComputeDate", lambda _: settled())
        recorder.answer("ReportExecution", self._executed)
        self.trader = self.make()

    def make(self) -> Trader:
        trader = Trader(
            self.recorder,
            lambda: self.venue,
            now=clock,
            sent=self.sent,
            enabled=self.enabled,
            scope=lambda: self.scope,
        )
        # As a running plugin's, past its start.
        trader.started.set()
        return trader

    def _listed(self, _: Any) -> ops.ListOrdersResult:
        return ops.ListOrdersResult(
            orders=self.orders,
            executions=self.executions,
            as_of=ops.Watermark(partitions=[ops.PartitionSequence(partition="book", sequence=7)]),
        )

    def _executed(self, params: ops.ReportExecutionParams) -> ops.ReportExecutionResult:
        self.minted += 1
        held = ops.Execution(
            execution_id=f"EXE-{self.minted}",
            placement_id=params.placement_id,
            venue_execution_id=params.venue_execution_id,
            quantity=params.quantity,
            supersedes=params.supersedes,
        )
        self.executions.append(held)
        return ops.ReportExecutionResult(execution=held)

    def holds(self, *placements: ops.Placement, **changes: Any) -> None:
        self.orders = [order_of(each, **changes) for each in placements]

    async def hear(self, *placements: ops.Placement) -> None:
        self.recorder.answer(
            "PlacementUpdated",
            lambda _: [ops.PlacementUpdatedEvent(placement=each) for each in placements],
        )
        await self.recorder.receive(placement_updated=self.trader.on_placement)

    async def instruct(
        self, value: ops.TradingInstructionValue, *, instance: str = "alpaca"
    ) -> None:
        instruction = ops.TradingInstruction(
            target=ops.TradingInstructionTarget(instance_id=instance),
            value=value,
            entry=ops.EntryMeta(entry_id="ENT-stop-1"),
        )
        self.recorder.answer(
            "TradingInstructionChanged",
            lambda _: ops.TradingInstructionChangedEvent(instruction=instruction),
        )
        await self.recorder.receive(trading_instruction_changed=self.trader.on_instruction)

    async def poll(self, times: int = 1) -> None:
        """The Trader's reads of the orders, each followed by the trade
        updates the read's steps made, as the stream delivers them."""
        for _ in range(times):
            await self.trader.poll()
            await self.updates.drain(self.trader, self.store)


async def placed_and_filled(recorder: Recorder) -> None:
    """A placement of 10 placed and filled at the simulated brokerage: 4, then
    the rest, each with the date read's settlement date."""
    rig = Rig(recorder)
    held = placement()
    rig.holds(held)
    await rig.hear(held)
    await rig.poll(2)
