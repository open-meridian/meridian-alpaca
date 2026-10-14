"""Connectivity's pages, from 0.7.0, each the same answer as its tool.

- Trading (`/admin/connections`, Manage; connectivity): `read_trading`.
  The key, set or not; the environment the account is in, said plainly --
  Paper (simulated money) or Live, or sample data; then each account the key
  reaches by its venue account code, read-only or with trading enabled, its
  catalogue and whether its ems mapped it here; and what is working, held
  back by a pause or a stop, or of unknown outcome. "Enable trading" on a
  read-only account, for the connectivity role's admin
  (`enable_trading`, checked by `check_enable_trading`, at
  `/admin/connections/enable-trading` as SnapTrade's): a new account is
  read-only until then. Counts and codes only: an admin page shows no
  account's data.
- Raw records (`/raw`, Open and View; custody and connectivity): each kind
  kept, its window and what becomes of it past it, and a raw record followed
  from a fact's or a row's reference, shown to a person who may read an
  account mapped on this instance or linked by it; no tool of its own.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

import meridian
from meridian.plugin.v1 import operations_pb2 as ops
from meridian.roles import data

from .kinds import KINDS
from .page import current, pages
from .records import AccountTrading, TradingAsked, TradingEnabled, TradingRead
from .settings import SAID

TRADING = "/admin/connections"
ENABLE_TRADING = f"{TRADING}/enable-trading"
RAW = "/raw"
CHECK = "/check"


def _shown(at: datetime | None) -> str:
    return at.astimezone(UTC).isoformat(timespec="seconds").replace("+00:00", "Z") if at else ""


def environment() -> str:
    config = current().config
    if config.sample_data:
        return "Sample data: a simulated brokerage, nothing sent to Alpaca"
    return SAID[config.environment]


def _reached() -> list[str]:
    trader = current().trader
    return list(trader.reached) if trader is not None else []


def trading_read() -> TradingRead:
    state = current()
    config = state.config
    trader = state.trader
    mapped = trader.mapped() if trader is not None else frozenset()
    shown = trader.shown if trader is not None else None
    return TradingRead(
        credentials=[
            data.Credential("API key ID", config.key_id_set),
            data.Credential("API secret key", config.secret_key_set),
        ],
        environment=environment(),
        accounts=[
            AccountTrading(
                venue_account_code=code,
                trading=trader is not None and trader.trading_on(code),
                order_types=[
                    ops.OrderType.Name(t).removeprefix("ORDER_TYPE_").lower()
                    for t, _ in (trader.offered(code) if trader is not None else ())
                ],
                mapped=code in mapped,
            )
            for code in _reached()
        ],
        working=shown.working if shown else 0,
        held=shown.held if shown else 0,
        unknown=shown.unknown if shown else 0,
        last_poll=_shown(shown.last_poll) if shown else "",
        updates=str(getattr(state.updates, "state", "") or ""),
        error=shown.error if shown else "",
    )


def _may_enable(request: meridian.Request) -> bool:
    return bool(request.caller.level_for("connectivity") == meridian.AccessLevel.ACCESS_LEVEL_ADMIN)


@pages.page(
    TRADING,
    "Trading",
    levels="admin",
    roles="connectivity",
    answers=TradingRead,
    name="read_trading",
    description=(
        "Connectivity's state at Alpaca: each credential, set or not (never a value); the "
        "environment, Paper (simulated money) or Live, or sample data; each account the key "
        "reaches by its venue account code, read-only or with trading enabled, the order "
        "types its catalogue offers and whether its ems mapped it here; how many placements "
        "are working, held back by a pause or a stop, or of unknown outcome; the last read "
        "of the orders and the trade updates stream's state. No account's data."
    ),
)
async def trading(request: meridian.Request) -> meridian.Response:
    return pages.answer(
        "trading.html", trading_read(), may_enable=_may_enable(request), enable=ENABLE_TRADING
    )


def _asked(request: meridian.Request) -> str:
    asked: TradingAsked = request.params
    code = (asked.venue_account_code or "").strip()
    if code not in _reached():
        pages.refuse(
            "the key reaches no account under this venue account code",
            ("venue_account_code", "not reached"),
        )
    return code


@pages.route(
    ENABLE_TRADING,
    levels="admin",
    roles="connectivity",
    methods=["POST"],
    params=TradingAsked,
    answers=TradingEnabled,
    name="enable_trading",
    checked_by="check_enable_trading",
    description=(
        "Enable trading on an account the key reaches, by its venue account code: a new "
        "account is read-only until the connectivity role's admin enables it. Its catalogue "
        "is published at once."
    ),
)
async def enable_trading(request: meridian.Request) -> meridian.Response:
    code = _asked(request)
    trader = current().trader
    if trader is None:
        pages.refuse("this instance holds no connectivity role", ("venue_account_code", "no role"))
    if not trader.trading_on(code):
        await trader.enable(
            code,
            {
                "venue_account_code": code,
                "enabled_by": request.caller.subject,
                "environment": environment(),
            },
        )
    said = f"Trading enabled on {code} ({environment()})."
    if request.tool_name:
        return pages.answer("trading.html", TradingEnabled(code, said))
    return pages.answer(
        "trading.html",
        trading_read(),
        may_enable=_may_enable(request),
        enable=ENABLE_TRADING,
        notice=said,
    )


@pages.route(
    ENABLE_TRADING + CHECK,
    levels="admin",
    roles="connectivity",
    methods=["POST"],
    params=TradingAsked,
    answers=TradingEnabled,
    reads=True,
    name="check_enable_trading",
    description=(
        "Check enable_trading without enabling anything: whether the key reaches the account, "
        "and whether trading is enabled on it already."
    ),
)
async def check_enable_trading(request: meridian.Request) -> meridian.Response:
    code = _asked(request)
    trader = current().trader
    already = trader is not None and trader.trading_on(code)
    said = (
        f"Trading is enabled on {code} already."
        if already
        else f"Would enable trading on {code} ({environment()})."
    )
    return pages.answer("trading.html", TradingEnabled(code, said))


def _may_read_mapped(request: meridian.Request) -> bool:
    """Whether the person may read an account mapped on this instance (the
    accounts connectivity's records are of) or linked by it (custody's)."""
    trader = current().trader
    scope = current().scope
    if any(link.account_id in request.caller.read_for("custody") for link in scope.links):
        return True
    if trader is None or scope.venue_account_map is None:
        return False
    allowed = request.caller.read_for("connectivity")
    return any(
        mapping.account_id in allowed
        for mapping in scope.venue_account_map.mappings
        if mapping.connectivity_instance_id == trader.me
    )


@pages.page(
    RAW,
    "Raw records",
    levels=["write", "read"],
    roles=["custody", "connectivity"],
    tool=False,
    why=(
        "Alpaca's answers as received, any JSON it sent, which no typed record holds; an "
        "agent reads what they came to with read_statements and read_account_activities, "
        "and in the book's placements and executions, each naming its raw record"
    ),
)
async def raw_records(request: meridian.Request) -> str:
    state = current()
    key = request.query.get("ref", "").strip()
    found: Any = None
    record: Any = None
    if key:
        finding = getattr(request.plugin, "find_record", None)
        found = finding(key.partition("#")[0]) if finding is not None else None
        held = state.store.get(key) if state.store is not None else None
        if held is not None and _may_read_mapped(request):
            record = json.dumps(held, indent=1, sort_keys=True)
    return pages.render(
        "raw_records.html",
        kinds=list(KINDS),
        stored=list(getattr(request.plugin, "stored", None) or []),
        past=state.values,
        key=key,
        found=found,
        record=record,
    )
