"""Synthetic Alpaca's Trading API: a simulated brokerage behind the shapes of
Alpaca's account, positions, activities and orders, for sample data, the
tests and `make e2e` (from 0.7.0; the account's positions and activities
from 0.8.0).

SIMULATED ONLY. Nothing here reaches Alpaca, its paper trading API or any
brokerage: an order "placed" here is a line in this module's state, and
every answer is shaped like Alpaca's own (its order object, its error body,
its account, its positions, its activities, its trade updates), invented,
every value of it. A
paper run against Alpaca is the product owner's alone, by hand, and never
this.

It is a `broker.Send`: broker.py speaks to it exactly as to Alpaca, so the
same code is exercised. What it holds survives the plugin's restart, as a
brokerage's orders do: a JSON file in the plugin's storage
(`synthetic-venue/orders.json`), written whole on every change; with none
given (a test) it lives in the process. Where the file changes beside it
(an order placed in Alpaca's own app, `place_at_the_brokerage`), the next
request reads it again.

The account (`SYN-ALP-1001`, a margin account, multiplier 2) opens with a
cash deposit of 100,000 dollars on 2026-09-01, and holds one order from the
start, three AAPL bought at the brokerage outside the platform on
2026-09-02 (`syn-out-0001`), which paid a dividend on 2026-09-15: the
history custody reads, and an order filled before the plugin watched the
account, which connectivity leaves alone. An order placed in Alpaca's own
app later (`place_at_the_brokerage`: `syn-app-<n>`, its client order ID
Alpaca's own) is worked as any other, and its fills are the venue-originated
fills connectivity reports on no placement (contract v23).

Each fill is also the account's activity, as Alpaca lists it (a `FILL`
naming the order, its quantity, price and cumulative quantity); the
positions and the cash are what the activities come to, a bust taking its
fill back.

How it works an order, each read of the orders taking it one step on, so a
test and the e2e see every state, and each step heard on its trade updates
(`updates`), as Alpaca's stream says it:

- placed: `new`, its ID `syn-ord-<n>`, its client order ID as given;
- a quantity of at most 100: on the next read `partially_filled`, 40% (to a
  whole share), then `filled`, at the limit price, else the symbol's
  synthetic price; more than 100 stays `new` until cancelled;
- cancelled (`DELETE`): `canceled`, what was filled kept; one ended is
  refused (422, "order is not cancelable");
- replaced (`PATCH`), while it works: the order `replaced`, naming its
  successor (`replaced_by`), and a new order (`replaces`) for the quantity
  asked less what the old one filled, at the price and time in force asked;
  one ended is refused (422).

And by the symbol, so each of Alpaca's answers has an order to show it:

- `SYNREJ`: refused when placed, 403, Alpaca's code and words;
- `SYNLOST`: taken, but the answer never arrives (the call fails with no
  status); the order is there when the orders are read;
- `SYNODD`: placed `held`, a status the plugin's mapping does not know, then
  `new` on the next read;
- `SYNBUST`: filled in full, then on a later read its filled quantity back
  to 0 and `canceled`, the brokerage having busted the fill (a `trade_bust`
  update);
- `SYNREN`: renamed while it works: once it fills, its order names the
  symbol `SYNRNM`.
"""

from __future__ import annotations

import json
import os
import tempfile
import urllib.parse
import zlib
from collections.abc import Callable, Mapping
from datetime import UTC, datetime, timedelta
from decimal import ROUND_HALF_EVEN, Decimal
from pathlib import Path
from typing import Any

from .vendor import Response, parse

ACCOUNT_NUMBER = "SYN-ALP-1001"
ACCOUNT_ID = "00000000-0000-4000-8000-0000000a1001"
REJECTED = "SYNREJ"
LOST = "SYNLOST"
ODD = "SYNODD"
BUSTED = "SYNBUST"
RENAMED = "SYNREN"
RENAMED_TO = "SYNRNM"
#: A status Alpaca lists that the plugin's mapping does not know.
ODD_STATUS = "held"
FILLS_AT_ONCE = Decimal(100)
PRICES = {"AAPL": Decimal("227.41"), "SPY": Decimal("575.44")}
PRICE = Decimal("100.00")
OUTSIDE_ID = "syn-out-0001"
DEPOSIT = Decimal("100000.00")
WORKING = ("new", "partially_filled", "accepted", "pending_new", ODD_STATUS)


def _stamp(moment: datetime) -> str:
    return moment.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%S.%f") + "Z"


def _text(value: Decimal) -> str:
    return format(value.normalize(), "f") if value == value.to_integral() else format(value, "f")


def _seeded() -> dict[str, Any]:
    """The account's history before the platform placed anything."""
    outside = {
        "id": OUTSIDE_ID,
        "client_order_id": "placed-at-the-brokerage",
        "symbol": "AAPL",
        "asset_class": "us_equity",
        "side": "buy",
        "type": "market",
        "time_in_force": "day",
        "qty": "3",
        "filled_qty": "3",
        "filled_avg_price": "226.10",
        "limit_price": None,
        "stop_price": None,
        "status": "filled",
        "created_at": "2026-09-02T14:31:00.000000Z",
        "submitted_at": "2026-09-02T14:31:00.000000Z",
        "updated_at": "2026-09-02T14:31:01.000000Z",
        "filled_at": "2026-09-02T14:31:01.000000Z",
        "replaced_by": None,
        "replaces": None,
    }
    return {
        "next": 1,
        "orders": [outside],
        "activities": [
            {
                "id": "20260901000000000::00000000-0000-4000-8000-0000000c5d01",
                "activity_type": "CSD",
                "date": "2026-09-01",
                "net_amount": _text(DEPOSIT),
                "description": "Deposit",
                "status": "executed",
            },
            {
                "id": "20260902143101000::00000000-0000-4000-8000-0000000f0001",
                "activity_type": "FILL",
                "transaction_time": "2026-09-02T14:31:01.000000Z",
                "type": "fill",
                "price": "226.10",
                "qty": "3",
                "side": "buy",
                "symbol": "AAPL",
                "leaves_qty": "0",
                "order_id": OUTSIDE_ID,
                "cum_qty": "3",
                "order_status": "filled",
            },
            {
                "id": "20260915000000000::00000000-0000-4000-8000-0000000d1001",
                "activity_type": "DIV",
                "date": "2026-09-15",
                "net_amount": "0.75",
                "description": "Cash DIV @ 0.25, Pos QTY: 3",
                "symbol": "AAPL",
                "qty": "3",
                "per_share_amount": "0.25",
                "status": "executed",
            },
        ],
    }


def place_at_the_brokerage(
    state: Path, symbol: str, qty: str, side: str = "buy", now: datetime | None = None
) -> str:
    """An order placed in Alpaca's own app, outside the platform, written to
    the simulated brokerage's state beside it (the e2e's way in, and a
    test's): a market order, worked from the next read on. Its ID."""
    broker = SyntheticBroker(lambda: now or datetime.now(UTC), state)
    return broker.at_the_brokerage(symbol, qty, side)


class SyntheticBroker:
    """The simulated brokerage, answering as Alpaca's Trading API."""

    def __init__(self, now: Callable[[], datetime], state: Path | None = None) -> None:
        self._now = now
        self._file = state
        self._stamp: float = 0.0
        self.held: dict[str, Any] = {}
        #: The trade updates each step made, as Alpaca's stream says them,
        #: for the stream to deliver (updates.py); not kept across a restart.
        self.updates: list[dict[str, Any]] = []
        #: Every request asked of it: method and path.
        self.asked: list[tuple[str, str]] = []
        self._load()

    # ── State ───────────────────────────────────────────────────────────

    def _load(self) -> None:
        held: Any = None
        if self._file is not None and self._file.exists():
            try:
                held = parse(self._file.read_text())
            except (OSError, ValueError):
                held = None
        self.held = held if isinstance(held, dict) and "orders" in held else _seeded()
        self.held.setdefault("activities", [])
        self._stamp = self._mtime()

    def _mtime(self) -> float:
        try:
            return self._file.stat().st_mtime if self._file is not None else 0.0
        except OSError:
            return 0.0

    def _reload_if_changed(self) -> None:
        """The state as written beside this process (an order placed in
        Alpaca's own app), read again where the file changed."""
        if self._file is not None and self._mtime() != self._stamp:
            self._load()

    def _save(self) -> None:
        if self._file is None:
            return
        self._file.parent.mkdir(parents=True, exist_ok=True)
        made, written = tempfile.mkstemp(dir=self._file.parent, suffix=".tmp")
        try:
            with os.fdopen(made, "w") as file:
                json.dump(self.held, file, default=str, sort_keys=True)
            os.replace(written, self._file)
        except BaseException:
            Path(written).unlink(missing_ok=True)
            raise
        self._stamp = self._mtime()

    def at_the_brokerage(self, symbol: str, qty: str, side: str = "buy") -> str:
        """An order placed in Alpaca's own app: a market order with Alpaca's
        own client order ID, worked from the next read on."""
        self._reload_if_changed()
        number = sum(1 for o in self.held["orders"] if o["id"].startswith("syn-app-")) + 1
        now = _stamp(self._now())
        order = {
            "id": f"syn-app-{number:04d}",
            "client_order_id": f"alpaca-app-{zlib.crc32(now.encode()):08x}",
            "symbol": symbol,
            "asset_class": "us_equity",
            "side": side,
            "type": "market",
            "time_in_force": "day",
            "qty": qty,
            "filled_qty": "0",
            "filled_avg_price": None,
            "limit_price": None,
            "stop_price": None,
            "status": "new",
            "created_at": now,
            "submitted_at": now,
            "updated_at": now,
            "filled_at": None,
            "replaced_by": None,
            "replaces": None,
        }
        self.held["orders"].append(order)
        self._update("new", order)
        self._save()
        return str(order["id"])

    # ── The transport ───────────────────────────────────────────────────

    def __call__(
        self, method: str, url: str, headers: Mapping[str, str], body: bytes | None
    ) -> Response:
        parsed = urllib.parse.urlparse(url)
        path = parsed.path
        self.asked.append((method, path))
        self._reload_if_changed()
        asked = parse(body.decode()) if body else {}
        if method == "GET" and path == "/v2/account":
            return _ok(self._account())
        if method == "GET" and path == "/v2/positions":
            return _ok(self._positions())
        if method == "GET" and path == "/v2/account/activities":
            return _ok(self._activities(urllib.parse.parse_qs(parsed.query)))
        if method == "GET" and path == "/v2/orders":
            for order in self.held["orders"]:
                self._step(order)
            self._save()
            return _ok(list(reversed(self.held["orders"])))
        if method == "POST" and path == "/v2/orders":
            return self._place(asked)
        if path.startswith("/v2/orders/"):
            order_id = urllib.parse.unquote(path.removeprefix("/v2/orders/"))
            if method == "DELETE":
                return self._cancel(order_id)
            if method == "PATCH":
                return self._replace(order_id, asked)
        return _refused(404, 40410000, "endpoint not found")

    # ── Orders ──────────────────────────────────────────────────────────

    def _order(self, order_id: str) -> dict[str, Any] | None:
        return next((o for o in self.held["orders"] if o["id"] == order_id), None)

    def _new_id(self) -> str:
        number = int(self.held["next"])
        self.held["next"] = number + 1
        return f"syn-ord-{number:04d}"

    def _place(self, asked: Mapping[str, Any]) -> Response:
        symbol = str(asked.get("symbol") or "")
        if symbol == REJECTED:
            return _refused(403, 40310000, "insufficient buying power")
        client = str(asked.get("client_order_id") or "")
        if client and any(o["client_order_id"] == client for o in self.held["orders"]):
            return _refused(422, 40010001, "client_order_id must be unique")
        now = _stamp(self._now())
        order = {
            "id": self._new_id(),
            "client_order_id": client,
            "symbol": symbol,
            "asset_class": "us_equity",
            "side": asked.get("side"),
            "type": asked.get("type"),
            "time_in_force": asked.get("time_in_force"),
            "qty": str(asked.get("qty")),
            "filled_qty": "0",
            "filled_avg_price": None,
            "limit_price": None if asked.get("limit_price") is None else str(asked["limit_price"]),
            "stop_price": None if asked.get("stop_price") is None else str(asked["stop_price"]),
            "status": ODD_STATUS if symbol == ODD else "new",
            "created_at": now,
            "submitted_at": now,
            "updated_at": now,
            "filled_at": None,
            "replaced_by": None,
            "replaces": None,
        }
        self.held["orders"].append(order)
        self._update("new", order)
        self._save()
        if symbol == LOST:
            raise ConnectionResetError("the answer was lost on the way back")
        return _ok(dict(order))

    def _cancel(self, order_id: str) -> Response:
        order = self._order(order_id)
        if order is None:
            return _refused(404, 40410000, "order not found")
        if order["status"] not in WORKING:
            return _refused(422, 42210000, "order is not cancelable")
        order.update(status="canceled", updated_at=_stamp(self._now()))
        self._update("canceled", order)
        self._save()
        return Response(204, {}, b"")

    def _replace(self, order_id: str, asked: Mapping[str, Any]) -> Response:
        order = self._order(order_id)
        if order is None:
            return _refused(404, 40410000, "order not found")
        if order["status"] not in ("new", "partially_filled", ODD_STATUS):
            return _refused(422, 42210000, "order is not replaceable")
        now = _stamp(self._now())
        filled = Decimal(order["filled_qty"])
        total = Decimal(str(asked.get("qty") or order["qty"]))
        successor = {
            **order,
            "id": self._new_id(),
            "client_order_id": str(asked.get("client_order_id") or order["client_order_id"]),
            "qty": _text(total - filled),
            "filled_qty": "0",
            "filled_avg_price": None,
            "limit_price": (
                str(asked["limit_price"])
                if asked.get("limit_price") is not None
                else order["limit_price"]
            ),
            "time_in_force": asked.get("time_in_force") or order["time_in_force"],
            "status": "new",
            "created_at": now,
            "submitted_at": now,
            "updated_at": now,
            "filled_at": None,
            "replaced_by": None,
            "replaces": order["id"],
        }
        successor.pop("reads_filled", None)
        order.update(status="replaced", replaced_by=successor["id"], updated_at=now)
        self.held["orders"].append(successor)
        self._update("replaced", order)
        self._update("new", successor)
        self._save()
        return _ok(dict(successor))

    def _step(self, order: dict[str, Any]) -> None:
        status, symbol = order["status"], order["symbol"]
        total = Decimal(order["qty"])
        price = Decimal(order["limit_price"]) if order["limit_price"] else PRICES.get(symbol, PRICE)
        now = self._now()
        before = Decimal(order["filled_qty"])
        if status == ODD_STATUS:
            order["status"] = "new"
        elif status == "new" and total <= FILLS_AT_ONCE:
            first = (total * Decimal("0.4")).quantize(Decimal(1), rounding=ROUND_HALF_EVEN)
            first = first if 0 < first < total else total
            if symbol == RENAMED:
                order["symbol"] = RENAMED_TO
            order.update(
                status="partially_filled" if first < total else "filled",
                filled_qty=_text(first),
                filled_avg_price=_text(price),
                filled_at=_stamp(now) if first == total else None,
            )
        elif status == "partially_filled":
            order.update(
                status="filled",
                filled_qty=_text(total),
                filled_avg_price=_text(price),
                filled_at=_stamp(now),
            )
        elif status == "filled" and symbol == BUSTED:
            order["reads_filled"] = int(order.get("reads_filled", 0)) + 1
            if order["reads_filled"] >= 2:
                order.update(status="canceled", filled_qty="0", filled_avg_price=None)
                order["updated_at"] = _stamp(now + timedelta(microseconds=1))
                self._update("trade_bust", order, qty=before, price=price)
            return
        else:
            return
        order["updated_at"] = _stamp(now + timedelta(microseconds=1))
        filled = Decimal(order["filled_qty"])
        if filled > before:
            self._filled(order, filled - before, price, now)

    def _filled(self, order: dict[str, Any], qty: Decimal, price: Decimal, at: datetime) -> None:
        """A step's fill, on the trade updates and in the account's activity."""
        event = "fill" if order["status"] == "filled" else "partial_fill"
        self._update(event, order, qty=qty, price=price)
        number = len(self.held["activities"]) + 1
        self.held["activities"].append(
            {
                "id": f"{at.strftime('%Y%m%d%H%M%S%f')[:17]}::00000000-0000-4000-8000-"
                f"{number:012d}",
                "activity_type": "FILL",
                "transaction_time": _stamp(at),
                "type": event,
                "price": _text(price),
                "qty": _text(qty),
                "side": order["side"],
                "symbol": order["symbol"],
                "leaves_qty": _text(Decimal(order["qty"]) - Decimal(order["filled_qty"])),
                "order_id": order["id"],
                "cum_qty": order["filled_qty"],
                "order_status": order["status"],
            }
        )

    def _update(self, event: str, order: Mapping[str, Any], **fill: Decimal) -> None:
        said: dict[str, Any] = {
            "event": event,
            "timestamp": _stamp(self._now()),
            "order": {k: v for k, v in order.items() if k != "reads_filled"},
        }
        if fill:
            said.update(
                price=_text(fill["price"]),
                qty=_text(fill["qty"]),
                execution_id=f"syn-exe-{len(self.updates) + 1:05d}",
            )
        self.updates.append(said)

    # ── The account, its positions and its activities ──────────────────

    def _busted(self) -> set[str]:
        """The orders whose fills the brokerage took back."""
        return {
            o["id"]
            for o in self.held["orders"]
            if o["symbol"] == BUSTED and o["status"] == "canceled"
        }

    def _fills(self) -> list[dict[str, Any]]:
        busted = self._busted()
        return [
            a
            for a in self.held["activities"]
            if a["activity_type"] == "FILL" and a.get("order_id") not in busted
        ]

    def _activities(self, query: Mapping[str, list[str]]) -> list[dict[str, Any]]:
        """The account's activities, oldest first, on or after `after`'s day,
        a page of `page_size` after `page_token`."""
        after = (query.get("after") or [""])[0][:10]
        token = (query.get("page_token") or [""])[0]
        size = int((query.get("page_size") or ["100"])[0])
        listed = [
            a
            for a in self.held["activities"]
            if not after or str(a.get("date") or a.get("transaction_time") or "")[:10] >= after
        ]
        if token:
            ids = [a["id"] for a in listed]
            listed = listed[ids.index(token) + 1 :] if token in ids else []
        return listed[:size]

    def _positions(self) -> list[dict[str, Any]]:
        held: dict[str, tuple[Decimal, Decimal]] = {}
        for fill in self._fills():
            qty, cost = held.get(fill["symbol"], (Decimal(0), Decimal(0)))
            units = Decimal(fill["qty"]) * (1 if fill["side"] == "buy" else -1)
            held[fill["symbol"]] = (qty + units, cost + units * Decimal(fill["price"]))
        out = []
        for symbol, (qty, cost) in sorted(held.items()):
            if qty == 0:
                continue
            price = PRICES.get(symbol, PRICE)
            average = (cost / qty).quantize(Decimal("0.0001"))
            out.append(
                {
                    "asset_id": "00000000-0000-4000-8000-"
                    f"{zlib.crc32(symbol.encode()) % 10**12:012d}",
                    "symbol": symbol,
                    "exchange": "NASDAQ",
                    "asset_class": "us_equity",
                    "asset_marginable": True,
                    "qty": _text(qty),
                    "qty_available": _text(qty),
                    "avg_entry_price": _text(average),
                    "side": "long" if qty > 0 else "short",
                    "market_value": _text(qty * price),
                    "cost_basis": _text(cost),
                    "current_price": _text(price),
                }
            )
        return out

    def _cash(self) -> Decimal:
        cash = Decimal(0)
        for fill in self._fills():
            sign = -1 if fill["side"] == "buy" else 1
            cash += sign * Decimal(fill["qty"]) * Decimal(fill["price"])
        for activity in self.held["activities"]:
            if activity["activity_type"] != "FILL" and activity.get("net_amount") is not None:
                cash += Decimal(activity["net_amount"])
        return cash

    def _account(self) -> dict[str, Any]:
        cash = self._cash()
        long_value = sum((Decimal(p["market_value"]) for p in self._positions()), Decimal(0))
        return {
            "id": ACCOUNT_ID,
            "account_number": ACCOUNT_NUMBER,
            "status": "ACTIVE",
            "currency": "USD",
            "cash": _text(cash),
            "buying_power": _text(cash * 2),
            "equity": _text(cash + long_value),
            "long_market_value": _text(long_value),
            "short_market_value": "0",
            "multiplier": "2",
            "trading_blocked": False,
            "account_blocked": False,
            "created_at": "2026-09-01T00:00:00Z",
        }


def _ok(value: Any) -> Response:
    return Response(200, {"content-type": "application/json"}, json.dumps(value).encode())


def _refused(status: int, code: int, message: str) -> Response:
    body = json.dumps({"code": code, "message": message}).encode()
    return Response(status, {"content-type": "application/json"}, body)
