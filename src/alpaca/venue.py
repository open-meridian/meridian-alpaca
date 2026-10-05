"""Alpaca's Market Data API v2 for US stocks: the one module that speaks to it.

REST at data.alpaca.markets: `/v2/stocks/bars` (daily bars, paged),
`/v2/stocks/trades` (historical trades, paged), `/v2/stocks/trades/latest`,
`/v2/stocks/quotes/latest` and `/v2/stocks/snapshots`. Every request names
its feed, `iex` or `sip`, because Alpaca's own default differs by
subscription (SIP for an unlimited one, else IEX for the latest endpoints;
SIP always for bars), and a row says which feed it came from by its dataset
(convert.py). Nothing here streams: the WebSocket feed is the lake's 1b.

**The key.** Alpaca's two headers, `APCA-API-KEY-ID` and
`APCA-API-SECRET-KEY`, from `settings.Credentials`, which the plugin's
secret settings or the developer's environment (tests/keys.py) hand in.
They are added to the request and to nothing else: a raw record keeps the
request's other headers, and as a last guard any text holding either value
is redacted before it is kept. Without a key, nothing is asked.

**Rate limits, honoured.** Alpaca's Basic plan allows 200 calls a minute
(Algo Trader Plus 10,000); this client spends at most half the Basic
allowance, 100 a minute, one at a time, from a token bucket, whichever plan
the key is on. A 429 is waited out (Retry-After, else Alpaca's
X-RateLimit-Reset, else 1, 2, 4 seconds) and asked again at most three
times, each one counted.

**Entitlements.** A free account gets IEX in real time, and SIP only past
its latest 15 minutes; SIP in real time needs Algo Trader Plus. Alpaca
answers 403, "subscription does not permit querying recent SIP data", to a
request past that. `entitlements()` asks once for each and says which
answered, from which each dataset's feed is decided (`feed_for`).

**Every response is a raw record first** (raw.py), and a body is parsed
with `json_decimal`: Alpaca's prices are JSON numbers, which become
Decimals from their own text and never a float.
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable, Iterator, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from .raw import RawStore
from .rows import NANOS_PER_SECOND, json_decimal
from .settings import FEEDS, IEX, SIP, Credentials

DATA = "https://data.alpaca.markets"
USER_AGENT = "meridian-alpaca/0.1.0 (+https://open-meridian.dev)"

#: Half of Alpaca's Basic plan, 200 calls a minute: 100 a minute, one at a time.
RATE_PER_SECOND = 100 / 60
BURST = 1
#: Alpaca's most bars, or trades, in one page.
PAGE_LIMIT = 10_000
#: The symbol the entitlement probe asks about: an ETF every feed carries.
PROBE_SYMBOL = "SPY"
#: How far back a free account may ask SIP for: past its latest 15 minutes,
#: with a minute's margin.
SIP_DELAY_NS = 16 * 60 * NANOS_PER_SECOND
#: Alpaca's authentication headers: sent, never kept.
AUTH_HEADERS = frozenset({"APCA-API-KEY-ID", "APCA-API-SECRET-KEY"})
#: The headers kept with a record: freshness, rate limits, the request's ID.
KEPT_HEADERS = (
    "date",
    "content-type",
    "x-ratelimit-limit",
    "x-ratelimit-remaining",
    "x-ratelimit-reset",
    "retry-after",
    "x-request-id",
)
REDACTED = "[redacted]"


@dataclass(frozen=True)
class Response:
    status: int
    headers: Mapping[str, str]
    body: bytes


Transport = Callable[[str, Mapping[str, str]], Response]


def urllib_transport(url: str, headers: Mapping[str, str]) -> Response:
    """One GET over HTTPS, with the standard library."""
    request = urllib.request.Request(url, headers=dict(headers))
    try:
        with urllib.request.urlopen(request, timeout=15) as answer:
            return Response(
                answer.status, {k.lower(): v for k, v in answer.headers.items()}, answer.read()
            )
    except urllib.error.HTTPError as answer:
        return Response(
            answer.code, {k.lower(): v for k, v in answer.headers.items()}, answer.read()
        )


class RateLimiter:
    """A token bucket: `rate` calls a second, `burst` at once."""

    def __init__(
        self,
        rate: float,
        burst: int,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.rate, self.burst = rate, burst
        self._clock, self._sleep = clock, sleep
        self._tokens = float(burst)
        self._at = clock()
        self.waited = 0.0

    def acquire(self) -> None:
        now = self._clock()
        self._tokens = min(self.burst, self._tokens + (now - self._at) * self.rate)
        self._at = now
        if self._tokens < 1:
            wait = (1 - self._tokens) / self.rate
            self._sleep(wait)
            self.waited += wait
            self._at = self._clock()
            self._tokens = 1.0
        self._tokens -= 1


class VenueError(Exception):
    """Alpaca answered with something other than what was asked for. Its
    message is Alpaca's, from the body; never a header, never the key."""

    def __init__(self, call: str, status: int, detail: str, raw_key: str) -> None:
        super().__init__(f"{call}: {status} {detail} (raw record {raw_key})")
        self.call, self.status, self.detail, self.raw_key = call, status, detail, raw_key


class NoKey(Exception):
    """There is no key to ask Alpaca with: nothing was asked."""


@dataclass(frozen=True)
class Fetched:
    """A parsed body, the key of the raw record it was kept as, when it
    arrived, and the feed it was asked of."""

    value: Any
    raw_key: str
    received_at_ns: int
    feed: str


@dataclass(frozen=True)
class Entitlements:
    """What the key answered for, asked once each (never its value).

    `answered` is False when Alpaca refused the key itself (401, or a 403
    to IEX, which every account has); `detail` says what it said."""

    answered: bool
    iex: bool = False
    sip_history: bool = False
    sip_live: bool = False
    detail: str = ""


def feed_for(kind: str, preferred: str, entitled: Entitlements | None) -> str:
    """The feed a dataset of `kind` (`daily` or `live`) is served from: IEX
    unless SIP is preferred and the key is entitled to it for that kind."""
    if preferred != SIP or entitled is None:
        return IEX
    allowed = entitled.sip_history if kind == "daily" else entitled.sip_live
    return SIP if allowed else IEX


class Alpaca:
    """Alpaca's stock market data, every response kept."""

    def __init__(
        self,
        store: RawStore,
        credentials: Credentials | None,
        transport: Transport = urllib_transport,
        limiter: RateLimiter | None = None,
        clock_ns: Callable[[], int] = time.time_ns,
        sleep: Callable[[float], None] = time.sleep,
        retries: int = 3,
    ) -> None:
        self.store, self.credentials, self.transport = store, credentials, transport
        self.limiter = limiter or RateLimiter(RATE_PER_SECOND, BURST)
        self.clock_ns, self.sleep, self.retries = clock_ns, sleep, retries
        self.calls = 0
        self.rate_limited = 0
        self._kept = 0

    def _keep(self, key: str, record: dict[str, Any]) -> str:
        self._kept += 1
        return self.store.keep(f"{key}/{record['received_at_ns']}-{self._kept}", record)

    def _redacted(self, text: str) -> str:
        """The text with neither part of the key in it, should Alpaca ever
        echo one."""
        if self.credentials is not None:
            for secret in self.credentials.secrets():
                text = text.replace(secret, REDACTED)
        return text

    def _wait(self, headers: Mapping[str, str], attempt: int) -> float:
        """How long a 429 asks to be waited out: Retry-After, else until
        X-RateLimit-Reset (epoch seconds), else 1, 2, 4 seconds."""
        retry_after = headers.get("retry-after", "")
        if retry_after.isdigit():
            return float(retry_after)
        reset = headers.get("x-ratelimit-reset", "")
        if reset.isdigit():
            return float(min(60, max(1, int(reset) - self.clock_ns() // NANOS_PER_SECOND)))
        return 2.0**attempt

    def _get(self, call: str, key: str, path: str, params: Mapping[str, str]) -> Fetched:
        if self.credentials is None:
            raise NoKey(f"{call}: no Alpaca key is set, so nothing was asked")
        feed = params.get("feed", "")
        if feed not in FEEDS:
            raise ValueError(f"{call}: every request names its feed, iex or sip")
        url = f"{DATA}{path}?{urllib.parse.urlencode(params)}"
        kept_headers = {"User-Agent": USER_AGENT, "Accept": "application/json"}
        headers = {**kept_headers, **self.credentials.headers()}
        for attempt in range(self.retries + 1):
            self.limiter.acquire()
            self.calls += 1
            answer = self.transport(url, headers)
            received = self.clock_ns()
            text = self._redacted(answer.body.decode("utf-8", errors="replace"))
            raw_key = self._keep(
                key,
                {
                    "venue": "alpaca",
                    "call": call,
                    "feed": feed,
                    "request": {"method": "GET", "url": url, "headers": kept_headers},
                    "received_at_ns": received,
                    "status": answer.status,
                    "headers": {
                        h: self._redacted(answer.headers[h])
                        for h in KEPT_HEADERS
                        if h in answer.headers
                    },
                    "body": text,
                },
            )
            if answer.status == 429 and attempt < self.retries:
                self.rate_limited += 1
                self.sleep(self._wait(answer.headers, attempt))
                continue
            if answer.status != 200:
                raise VenueError(call, answer.status, _message(text), raw_key)
            try:
                return Fetched(json_decimal(text), raw_key, received, feed)
            except json.JSONDecodeError as broken:
                raise VenueError(call, answer.status, f"not JSON: {broken}", raw_key) from None
        raise AssertionError("unreachable")

    def _paged(
        self, call: str, key: str, path: str, params: Mapping[str, str], pages: int
    ) -> Iterator[Fetched]:
        """Each page in turn, following `next_page_token`, at most `pages`."""
        token = ""
        for _ in range(pages):
            fetched = self._get(
                call, key, path, {**params, **({"page_token": token} if token else {})}
            )
            yield fetched
            token = str(fetched.value.get("next_page_token") or "")
            if not token:
                return
        raise ValueError(f"{call}: more than {pages} pages; ask for less")

    def bars(
        self,
        symbols: Sequence[str],
        start_ns: int,
        end_ns: int,
        feed: str,
        timeframe: str = "1Day",
        pages: int = 10,
    ) -> list[Fetched]:
        """Bars from `start_ns` to `end_ns`, unadjusted, oldest first, every
        page kept."""
        params = {
            "symbols": ",".join(symbols),
            "timeframe": timeframe,
            "start": _iso(start_ns),
            "end": _iso(end_ns),
            "adjustment": "raw",
            "sort": "asc",
            "limit": str(PAGE_LIMIT),
            "feed": feed,
        }
        return list(
            self._paged("bars", f"bars/{feed}/{timeframe}", "/v2/stocks/bars", params, pages)
        )

    def trades(
        self, symbol: str, start_ns: int, end_ns: int, feed: str, limit: int = 100
    ) -> Fetched:
        """Trades from `start_ns` to `end_ns`, oldest first, one page."""
        params = {
            "symbols": symbol,
            "start": _iso(start_ns),
            "end": _iso(end_ns),
            "sort": "asc",
            "limit": str(limit),
            "feed": feed,
        }
        return self._get("trades", f"trades/{feed}/{symbol}", "/v2/stocks/trades", params)

    def latest_trades(self, symbols: Sequence[str], feed: str) -> Fetched:
        params = {"symbols": ",".join(symbols), "feed": feed}
        return self._get(
            "latest_trades", f"trades-latest/{feed}", "/v2/stocks/trades/latest", params
        )

    def latest_quotes(self, symbols: Sequence[str], feed: str) -> Fetched:
        params = {"symbols": ",".join(symbols), "feed": feed}
        return self._get(
            "latest_quotes", f"quotes-latest/{feed}", "/v2/stocks/quotes/latest", params
        )

    def snapshots(self, symbols: Sequence[str], feed: str) -> Fetched:
        params = {"symbols": ",".join(symbols), "feed": feed}
        return self._get("snapshots", f"snapshots/{feed}", "/v2/stocks/snapshots", params)

    def entitlements(self, symbol: str = PROBE_SYMBOL) -> Entitlements:
        """Ask once for each feed the key might have, and say which answered:
        IEX's latest quote (every account), SIP's daily bars ending 16
        minutes ago (every account, past the free plan's restriction), and
        SIP's latest quote (Algo Trader Plus). Raises NoKey without a key."""
        try:
            self.latest_quotes([symbol], IEX)
        except VenueError as refused:
            if refused.status in (401, 403):
                return Entitlements(answered=False, detail=f"{refused.status} {refused.detail}")
            raise
        now = self.clock_ns()
        history_start = now - 10 * 86_400 * NANOS_PER_SECOND
        sip_history = self._entitled(
            lambda: self.bars([symbol], history_start, now - SIP_DELAY_NS, SIP, pages=1)
        )
        sip_live = self._entitled(lambda: self.latest_quotes([symbol], SIP))
        return Entitlements(answered=True, iex=True, sip_history=sip_history, sip_live=sip_live)

    @staticmethod
    def _entitled(ask: Callable[[], object]) -> bool:
        """Whether Alpaca answered, or refused with 403: not entitled."""
        try:
            ask()
        except VenueError as refused:
            if refused.status != 403:
                raise
            return False
        return True


def _message(text: str) -> str:
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        return text[:200]
    if isinstance(parsed, dict) and "message" in parsed:
        return str(parsed["message"])
    return text[:200]


def _iso(at_ns: int) -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(at_ns // NANOS_PER_SECOND))
