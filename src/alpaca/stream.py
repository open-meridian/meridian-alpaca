"""Alpaca's market data stream, over WebSocket, with reconnect (contract v19;
plans/the-lake-prices-the-book, row 19).

What is streamed is what the lake keeps current: the listings under a
standing want (lake.py says which), and nothing while there is none;
nothing of it is kept here. A standing want is heard once, when the lake
asks: contract v19 gives a want no catch-up read and the lake does not ask
again while it stands, so a process started anew streams nothing until a
want is asked anew (raised with the lake's plan).

**Alpaca's protocol** (`wss://stream.data.alpaca.markets/v2/{feed}`): the
server says it is connected; the client authenticates with the key (a
message the key's two parts travel in, never kept); then subscribes to
trades and quotes by symbol, and changes that as the wants change. Each
frame is a list of messages: trades (`T: t`), quotes (`q`), a trade
withdrawn (`x`) or corrected (`c`), and Alpaca's answers (`success`,
`subscription`, `error`). A free account streams IEX alone, 30 symbols on
one connection; SIP needs Algo Trader Plus. An error naming the key (402),
the plan (409) or the connection limit (406) is said on the Connection page
and asked again later; the symbol limit (405) is kept from by the lake.

**Reconnect.** A connection that breaks, closes or goes silent is opened
again, a second later, then doubling to a minute; the subscriptions are made
again from what is wanted. On each session after the first, the gap is
caught up: the lake reads, from Alpaca's trade history, the trades since the
last one heard of each listing, and records those it has not, so the lake
holds every trade and its readers catch up from it (W10.5, by watermark).

**Every message is a raw record first**, a line of the session's record
(raw.StreamLog), before anything converts it; the key is never in one.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import time
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any, Protocol

from . import convert, ws
from .convert import SourceQuote, SourceTrade
from .raw import RawStore, StreamLog
from .settings import Credentials
from .vendor import IEX, NS, STREAM

log = logging.getLogger("alpaca.stream")

#: A free account's most symbols on IEX's stream; SIP's plan has no limit.
IEX_SYMBOLS = 30
#: How long a session may say nothing before it is taken for broken: Alpaca
#: sends nothing between trades, so this is long.
SILENT_SECONDS = 120.0
#: How often what is heard is flushed to the lake, at the most.
FLUSH_SECONDS = 0.5
#: The waits between sessions: a second, doubling to a minute.
FIRST_WAIT, MOST_WAIT = 1.0, 60.0
#: After the key is refused, how long before it is tried again.
REFUSED_WAIT = 300.0

Stated = SourceTrade | SourceQuote


class Refused(Exception):
    """Alpaca refused the key, the plan or the connection: tried again only
    after a while."""


class FeedError(Exception):
    """Alpaca said something went wrong in the session."""


class Feed(Protocol):
    """One session with a stream: opened, subscribed, read, closed."""

    name: str

    async def open(self) -> None: ...

    async def subscribe(self, symbols: Sequence[str]) -> None: ...

    async def receive(self) -> list[Stated]: ...

    async def close(self) -> None: ...


def _parse(text: str) -> Any:
    return json.loads(text, parse_float=Decimal)


class AlpacaFeed:
    """Feed on Alpaca's stream, every message kept."""

    def __init__(
        self,
        credentials: Credentials,
        feed: str,
        store: RawStore,
        connect: Callable[..., Awaitable[Any]] = ws.connect,
        url: str = STREAM,
        clock_ns: Callable[[], int] = time.time_ns,
    ) -> None:
        self.credentials, self.name, self.store = credentials, feed, store
        self.connect, self.url, self.clock_ns = connect, url, clock_ns
        self.socket: Any = None
        self.log: StreamLog | None = None
        self.subscribed: set[str] = set()

    async def open(self) -> None:
        self.subscribed = set()
        now = self.clock_ns()
        day = time.strftime("%Y%m%d", time.gmtime(now // NS))
        self.log = StreamLog(self.store, f"stream/{self.name}/{day}/{now}")
        self.socket = await self.connect(f"{self.url}/{self.name}")
        await self._expect("connected")
        await self.socket.send(
            json.dumps(
                {
                    "action": "auth",
                    "key": self.credentials.key_id,
                    "secret": self.credentials.secret_key,
                }
            )
        )
        await self._expect("authenticated")

    async def _expect(self, said: str) -> None:
        """Read until Alpaca says `said`; refused where it says an error."""
        for _ in range(5):
            for message in self._keep(_parse(await self.socket.recv())):
                if message.get("T") == "success" and message.get("msg") == said:
                    return
                if message.get("T") == "error":
                    raise Refused(f"{message.get('code')} {message.get('msg')}")
        raise FeedError(f"Alpaca never said {said}")

    def _keep(self, frame: Any) -> list[dict[str, Any]]:
        """Each message of a frame, kept as a line of the session's record,
        with the key it was kept under."""
        messages = frame if isinstance(frame, list) else [frame]
        out: list[dict[str, Any]] = []
        received = self.clock_ns()
        for message in messages:
            if not isinstance(message, dict):
                continue
            key = (
                self.log.keep(
                    {"feed": self.name, "received_at_ns": received, "message": _textual(message)}
                )
                if self.log is not None
                else ""
            )
            out.append({**message, "_raw": key})
        return out

    async def subscribe(self, symbols: Sequence[str]) -> None:
        wanted = set(symbols)
        added, dropped = sorted(wanted - self.subscribed), sorted(self.subscribed - wanted)
        if added:
            await self.socket.send(
                json.dumps({"action": "subscribe", "trades": added, "quotes": added})
            )
        if dropped:
            await self.socket.send(
                json.dumps({"action": "unsubscribe", "trades": dropped, "quotes": dropped})
            )
        self.subscribed = wanted

    async def receive(self) -> list[Stated]:
        out: list[Stated] = []
        for message in self._keep(_parse(await self.socket.recv())):
            if message.get("T") == "error":
                code = message.get("code")
                if code in (402, 406, 409):
                    raise Refused(f"{code} {message.get('msg')}")
                raise FeedError(f"{code} {message.get('msg')}")
            out.extend(convert.streamed(message, str(message.get("_raw")), self.name))
        return out

    async def close(self) -> None:
        if self.socket is not None:
            with contextlib.suppress(Exception):
                await self.socket.close()
        if self.log is not None:
            self.log.close()
        self.socket, self.log = None, None


def _textual(value: Any) -> Any:
    """A parsed message with each number as its text, as it arrived."""
    if isinstance(value, dict):
        return {k: _textual(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_textual(v) for v in value]
    if isinstance(value, Decimal):
        return str(value)
    return value


@dataclass
class Session:
    """The stream as the Connection page shows it: never a key."""

    state: str = "idle"
    feed: str = ""
    subscribed: int = 0
    limit: int | None = None
    opened: int = 0
    reconnects: int = 0
    messages: int = 0
    caught_up: int = 0
    last_message_at_ns: int = 0
    last_error: str = ""
    since_ns: int = 0
    symbols: list[str] = field(default_factory=list)


class Restart(Exception):
    """The settings changed: the session is closed and opened again."""


class Streamer:
    """Keeps one session open while anything is wanted, subscribed to what
    is; hands what it hears to `heard`, flushes with `flush`, and after a
    reconnect asks `caught_up` for the gap since the last trade of each
    listing."""

    def __init__(
        self,
        make_feed: Callable[[], Feed | None],
        heard: Callable[[list[Stated]], Awaitable[None]],
        flush: Callable[[], Awaitable[None]],
        caught_up: Callable[[dict[str, int]], Awaitable[int]] | None = None,
        clock_ns: Callable[[], int] = time.time_ns,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        silent_seconds: float = SILENT_SECONDS,
    ) -> None:
        self.make_feed, self.heard, self.flush, self.caught_up = make_feed, heard, flush, caught_up
        self.clock_ns, self.sleep, self.silent_seconds = clock_ns, sleep, silent_seconds
        self.wanted: list[str] = []
        self.session = Session()
        #: The last trade heard of each listing, for the gap after a reconnect.
        self.last_at: dict[str, int] = {}
        self._changed = asyncio.Event()
        self._restart = False

    def want(self, symbols: Sequence[str]) -> None:
        """Stream these listings, and only these, from now on."""
        if list(symbols) != self.wanted:
            self.wanted = list(symbols)
            self._changed.set()

    def restart(self) -> None:
        """Open the session again: the key, or the feed it is entitled to,
        changed."""
        self._restart = True
        self._changed.set()

    async def run(self, sessions: int | None = None) -> None:
        """Forever (or for `sessions` sessions, in the tests)."""
        wait = FIRST_WAIT
        held = 0
        while sessions is None or held < sessions:
            if not self.wanted:
                self.session.state = "idle"
                self.session.subscribed, self.session.symbols = 0, []
                self._changed.clear()
                self._restart = False
                await self._changed.wait()
                continue
            feed = self.make_feed()
            if feed is None:
                self.session.state = "no key"
                self._changed.clear()
                self._restart = False
                await self._changed.wait()
                continue
            held += 1
            self._restart = False
            self.session.feed = feed.name
            try:
                await self._session(feed)
                # Nothing wanted any more: closed, and idle until there is.
                wait = FIRST_WAIT
                continue
            except Restart:
                wait = FIRST_WAIT
                continue
            except Refused as refused:
                self.session.state, self.session.last_error = "refused", f"refused: {refused}"
                log.warning("Alpaca refused the stream: %s", refused)
                await self._pause(REFUSED_WAIT)
                continue
            except (ws.Closed, FeedError, OSError, TimeoutError, ValueError) as broken:
                self.session.last_error = f"{type(broken).__name__}: {broken}"[:300]
                log.warning("the stream stopped: %s", self.session.last_error)
            finally:
                await feed.close()
            self.session.reconnects += 1
            self.session.state = "waiting"
            await self._pause(wait)
            wait = min(wait * 2, MOST_WAIT)

    async def _pause(self, seconds: float) -> None:
        """Wait, or less where the settings change meanwhile."""
        if self._restart:
            return
        changed = asyncio.ensure_future(self._changed.wait())
        slept = asyncio.ensure_future(self.sleep(seconds))
        try:
            await asyncio.wait({changed, slept}, return_when=asyncio.FIRST_COMPLETED)
        finally:
            changed.cancel()
            slept.cancel()

    async def _session(self, feed: Feed) -> None:
        self.session.state = "connecting"
        await feed.open()
        self.session.opened += 1
        self.session.since_ns = self.clock_ns()
        self._changed.clear()
        await self._subscribe(feed)
        self.session.state = "streaming"
        if self.session.opened > 1 and self.caught_up is not None and self.last_at:
            self.session.caught_up += await self.caught_up(dict(self.last_at))
        pending: asyncio.Future[list[Stated]] | None = None
        quiet_since = time.monotonic()
        try:
            while True:
                if pending is None:
                    pending = asyncio.ensure_future(feed.receive())
                changed: asyncio.Future[Any] = asyncio.ensure_future(self._changed.wait())
                waited: set[asyncio.Future[Any]] = {pending, changed}
                done, _ = await asyncio.wait(
                    waited, timeout=FLUSH_SECONDS, return_when=asyncio.FIRST_COMPLETED
                )
                if changed in done:
                    self._changed.clear()
                    if self._restart:
                        raise Restart()
                    if not self.wanted:
                        return
                    await self._subscribe(feed)
                else:
                    changed.cancel()
                if pending in done:
                    stated = pending.result()
                    pending = None
                    quiet_since = time.monotonic()
                    self._note(stated)
                    await self.heard(stated)
                elif time.monotonic() - quiet_since > self.silent_seconds:
                    raise ws.Closed(f"nothing heard for {self.silent_seconds:.0f} seconds")
                await self.flush()
        finally:
            if pending is not None:
                pending.cancel()
                with contextlib.suppress(BaseException):
                    await pending
            await self.flush()

    async def _subscribe(self, feed: Feed) -> None:
        await feed.subscribe(self.wanted)
        self.session.subscribed = len(self.wanted)
        self.session.symbols = list(self.wanted)

    def _note(self, stated: list[Stated]) -> None:
        now = self.clock_ns()
        for each in stated:
            self.session.messages += 1
            self.session.last_message_at_ns = now
            if isinstance(each, SourceTrade) and not each.cancelled:
                self.last_at[each.symbol] = max(self.last_at.get(each.symbol, 0), each.at_ns)


def symbol_limit(feed: str) -> int | None:
    """How many listings one session may stream: IEX's 30 on a free account,
    SIP's no limit."""
    return IEX_SYMBOLS if feed == IEX else None
