"""Alpaca's trade updates, streamed (from 0.7.0): how connectivity hears a
fill as Alpaca states it, at once, between its reads of the orders.

**Alpaca's protocol** (its "WebSocket streaming", the Trading API's stream
of the environment chosen: `wss://api.alpaca.markets/stream` or
`wss://paper-api.alpaca.markets/stream`): the client authenticates with the
key (a message the key's two parts travel in, never kept), Alpaca answers
`authorized` on the `authorization` stream, the client listens to
`trade_updates`, and each update follows: an event (`new`, `fill`,
`partial_fill`, `canceled`, `expired`, `done_for_day`, `replaced`,
`rejected`, `trade_bust`, `order_replace_rejected`, ...) with the order as it
stands, and for a fill its price, quantity and time. The paper stream sends
its frames as binary, which ws.py reads as text.

**Every update is a raw record first** (the `trading` kind), before the
Trader converts it. A session that breaks or goes silent is opened again, a
second later, doubling to a minute; what it missed while down the Trader's
next read of the orders finds, its fills derived the same way, so nothing is
reported twice.

Synthetic mode hears the simulated brokerage's updates (synthetic_broker.py)
the same way, nothing reaching Alpaca.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from . import ws
from .kinds import TRADING, Store
from .settings import Credentials
from .synthetic_broker import SyntheticBroker
from .trading import Trader
from .venue import SOURCE, update_of

log = logging.getLogger("alpaca.updates")

FIRST_WAIT, MOST_WAIT = 1.0, 60.0
SILENT_SECONDS = 600.0


def _textual(value: Any) -> Any:
    if isinstance(value, dict):
        return {k: _textual(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_textual(v) for v in value]
    if isinstance(value, Decimal):
        return str(value)
    return value


async def hear(trader: Trader, store: Store, said: dict[str, Any], synthetic: bool) -> None:
    """One update: kept, then handed to the Trader."""
    key = store.keep(
        TRADING,
        "update",
        {"venue": SOURCE, "call": "a trade update", "synthetic": synthetic, "body": _textual(said)},
        datetime.now(UTC),
    )
    event, record, fill = update_of(said)
    await trader.on_update(event, record, fill, key)


class AlpacaUpdates:
    """Alpaca's trade updates stream, opened again whenever it breaks."""

    def __init__(
        self,
        credentials: Credentials,
        url: str,
        connect: Callable[..., Awaitable[Any]] = ws.connect,
    ) -> None:
        self.credentials, self.url, self.connect = credentials, url, connect
        self.state = "idle"
        self.last_error = ""

    async def _session(self, trader: Trader, store: Store) -> None:
        socket = await self.connect(self.url)
        try:
            await socket.send(
                json.dumps(
                    {
                        "action": "auth",
                        "key": self.credentials.key_id,
                        "secret": self.credentials.secret_key,
                    }
                )
            )
            said = json.loads(await asyncio.wait_for(socket.recv(), 30))
            data = said.get("data") if isinstance(said, dict) else None
            if not isinstance(data, dict) or data.get("status") != "authorized":
                raise ws.Closed("Alpaca did not authorize the key on its trade updates")
            await socket.send(
                json.dumps({"action": "listen", "data": {"streams": ["trade_updates"]}})
            )
            self.state = "streaming"
            while True:
                text = await asyncio.wait_for(socket.recv(), SILENT_SECONDS)
                message = json.loads(text, parse_float=Decimal)
                if isinstance(message, dict) and message.get("stream") == "trade_updates":
                    await hear(trader, store, message.get("data") or {}, False)
        finally:
            with contextlib.suppress(Exception):
                await socket.close()

    async def run(self, trader: Trader, store: Store) -> None:
        wait = FIRST_WAIT
        while True:
            try:
                self.state = "connecting"
                await self._session(trader, store)
            except asyncio.CancelledError:
                raise
            except (ws.Closed, OSError, TimeoutError, ValueError) as failed:
                self.state, self.last_error = "waiting", str(failed)[:200]
                log.warning("the trade updates stopped (%s); again in %.0fs", failed, wait)
            await asyncio.sleep(wait)
            wait = min(wait * 2, MOST_WAIT)


class SyntheticUpdates:
    """The simulated brokerage's trade updates, heard each second."""

    def __init__(self, broker: SyntheticBroker, every: float = 1.0) -> None:
        self.broker, self.every = broker, every
        self.state = "streaming"
        self.last_error = ""

    async def drain(self, trader: Trader, store: Store) -> None:
        while self.broker.updates:
            await hear(trader, store, self.broker.updates.pop(0), True)

    async def run(self, trader: Trader, store: Store) -> None:
        while True:
            await self.drain(trader, store)
            await asyncio.sleep(self.every)
