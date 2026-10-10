"""Alpaca's Market Data API v2 for US stocks: the one module that speaks to it.

REST at data.alpaca.markets: `/v2/stocks/bars` (daily bars, paged),
`/v2/stocks/trades/latest` and `/v2/stocks/quotes/latest`. Every request
names its feed, `sip` (every US exchange, consolidated) or `iex` (the one
exchange a free account sees in real time), because Alpaca's own default
differs by subscription. Nothing here streams: Alpaca's WebSocket feed waits
for the lake's trades and quotes (contract v19).

**The key.** Alpaca's two headers, `APCA-API-KEY-ID` and
`APCA-API-SECRET-KEY`, from `settings.Credentials`. They are added to the
request and to nothing else: a raw record keeps the request's other headers,
and as a last guard any text holding either value is redacted before it is
kept. Without a key, nothing is asked.

**Rate limits, honoured.** Alpaca's Basic plan allows 200 calls a minute;
this client spends at most half of it, 100 a minute, one at a time, from a
token bucket, whichever plan the key is on. A 429 is waited out
(Retry-After, else X-RateLimit-Reset, else 1, 2, 4 seconds) and asked again
at most three times, each counted for the Connection page.

**Entitlements.** A free account gets IEX in real time, and SIP only past its
latest 15 minutes; SIP in real time needs Algo Trader Plus. Alpaca answers
403 to a request past what the key may ask. `entitlements()` asks once for
each and says which answered. The daily dataset is SIP's history (the
product owner's choice, 2026-10-04); the live one SIP's where the key has it
in real time, else IEX's.

**Every response is a raw record first** (raw.py), and a body is parsed with
Decimals: Alpaca's prices are JSON numbers, which become Decimals from their
own text and never a float.
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

from .raw import RawStore
from .settings import Credentials

DATA = "https://data.alpaca.markets"
USER_AGENT = "meridian-alpaca/0.2.0 (+https://open-meridian.dev)"
NS = 1_000_000_000

IEX = "iex"
SIP = "sip"
FEEDS = (IEX, SIP)

#: Alpaca's Basic plan allows 200 calls a minute; this spends half of it.
LIMIT_PER_MINUTE = 100
#: Alpaca's most bars in one page.
PAGE_LIMIT = 10_000
#: The most symbols one latest-trade or latest-quote call names.
SYMBOLS_PER_CALL = 100
#: The symbol the entitlement probe asks about: an ETF every feed carries.
PROBE_SYMBOL = "SPY"
#: How far back a free account may ask SIP for: past its latest 15 minutes,
#: with a minute's margin.
SIP_DELAY_NS = 16 * 60 * NS
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


def parse(text: str) -> Any:
    """JSON with every non-integer number a Decimal from its own text."""
    return json.loads(text, parse_float=Decimal)


class RateLimiter:
    """A token bucket: `per_minute` calls a minute, one at a time."""

    def __init__(
        self,
        per_minute: int = LIMIT_PER_MINUTE,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.rate = per_minute / 60
        self._clock, self._sleep = clock, sleep
        self._tokens = 1.0
        self._at = clock()

    def acquire(self) -> None:
        now = self._clock()
        self._tokens = min(1.0, self._tokens + (now - self._at) * self.rate)
        self._at = now
        if self._tokens < 1:
            self._sleep((1 - self._tokens) / self.rate)
            self._at = self._clock()
            self._tokens = 1.0
        self._tokens -= 1


class VendorError(Exception):
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

    @property
    def live_feed(self) -> str:
        """The feed live prices come from: SIP where the key has it in real
        time, else IEX."""
        return SIP if self.sip_live else IEX


@dataclass
class Usage:
    """What the client has spent, for the Connection page: never a value."""

    calls: int = 0
    rate_limited: int = 0
    last_call_at_ns: int = 0
    last_status: int = 0
    last_error: str = ""
    by_call: dict[str, int] = field(default_factory=dict)


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
        keyless: bool = False,
    ) -> None:
        self.store, self.credentials, self.transport = store, credentials, transport
        self.limiter = limiter or RateLimiter()
        self.clock_ns, self.sleep, self.retries = clock_ns, sleep, retries
        # Synthetic mode asks its built-in exchange with no key.
        self.keyless = keyless
        self.usage = Usage()
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
        retry_after = headers.get("retry-after", "")
        if retry_after.isdigit():
            return float(retry_after)
        reset = headers.get("x-ratelimit-reset", "")
        if reset.isdigit():
            return float(min(60, max(1, int(reset) - self.clock_ns() // NS)))
        return 2.0**attempt

    def get(self, call: str, key: str, path: str, params: Mapping[str, str]) -> Fetched:
        """One call, kept as a raw record before anything reads it."""
        if self.credentials is None and not self.keyless:
            raise NoKey(f"{call}: no Alpaca key is set, so nothing was asked")
        feed = params.get("feed", "")
        if feed not in FEEDS:
            raise ValueError(f"{call}: every request names its feed, iex or sip")
        url = f"{DATA}{path}?{urllib.parse.urlencode(params)}"
        kept_headers = {"User-Agent": USER_AGENT, "Accept": "application/json"}
        headers = dict(kept_headers)
        if self.credentials is not None:
            headers.update(self.credentials.headers())
        for attempt in range(self.retries + 1):
            self.limiter.acquire()
            self.usage.calls += 1
            self.usage.by_call[call] = self.usage.by_call.get(call, 0) + 1
            answer = self.transport(url, headers)
            received = self.clock_ns()
            self.usage.last_call_at_ns, self.usage.last_status = received, answer.status
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
                self.usage.rate_limited += 1
                self.sleep(self._wait(answer.headers, attempt))
                continue
            if answer.status != 200:
                failed = VendorError(call, answer.status, _message(text), raw_key)
                self.usage.last_error = f"{call}: {answer.status} {failed.detail}"
                raise failed
            try:
                value = parse(text)
            except json.JSONDecodeError as broken:
                self.usage.last_error = f"{call}: not JSON"
                raise VendorError(call, answer.status, f"not JSON: {broken}", raw_key) from None
            self.usage.last_error = ""
            return Fetched(value, raw_key, received, feed)
        raise AssertionError("unreachable")

    def daily_bars(
        self, symbols: Sequence[str], first: str, last: str, feed: str = SIP, pages: int = 20
    ) -> list[Fetched]:
        """Daily bars for New York dates `first` to `last`, unadjusted, oldest
        first, every page kept. SIP's end is held 16 minutes back, past a free
        account's restriction."""
        end = min(_new_york_end_ns(last), self.clock_ns() - SIP_DELAY_NS)
        params = {
            "symbols": ",".join(symbols),
            "timeframe": "1Day",
            "start": first,
            "end": _iso(end),
            "adjustment": "raw",
            "sort": "asc",
            "limit": str(PAGE_LIMIT),
            "feed": feed,
        }
        out: list[Fetched] = []
        token = ""
        for _ in range(pages):
            fetched = self.get(
                "bars",
                f"bars/{feed}/1Day",
                "/v2/stocks/bars",
                {**params, **({"page_token": token} if token else {})},
            )
            out.append(fetched)
            token = str(fetched.value.get("next_page_token") or "")
            if not token:
                return out
        raise ValueError(f"bars: more than {pages} pages; ask for less")

    def latest_trades(self, symbols: Sequence[str], feed: str) -> Fetched:
        params = {"symbols": ",".join(symbols), "feed": feed}
        return self.get(
            "latest trades", f"trades-latest/{feed}", "/v2/stocks/trades/latest", params
        )

    def latest_quotes(self, symbols: Sequence[str], feed: str) -> Fetched:
        params = {"symbols": ",".join(symbols), "feed": feed}
        return self.get(
            "latest quotes", f"quotes-latest/{feed}", "/v2/stocks/quotes/latest", params
        )

    def entitlements(self, symbol: str = PROBE_SYMBOL) -> Entitlements:
        """Ask once for each feed the key might have, and say which answered:
        IEX's latest quote (every account), SIP's daily bars ending 16
        minutes ago (every account, past the free plan's restriction), and
        SIP's latest quote (Algo Trader Plus). Raises NoKey without a key."""
        try:
            self.latest_quotes([symbol], IEX)
        except VendorError as refused:
            if refused.status in (401, 403):
                return Entitlements(answered=False, detail=f"{refused.status} {refused.detail}")
            raise
        now = self.clock_ns()
        week_ago = time.strftime("%Y-%m-%d", time.gmtime(now // NS - 10 * 86_400))
        today = time.strftime("%Y-%m-%d", time.gmtime(now // NS))
        sip_history = self._entitled(lambda: self.daily_bars([symbol], week_ago, today, SIP, 1))
        sip_live = self._entitled(lambda: self.latest_quotes([symbol], SIP))
        return Entitlements(answered=True, iex=True, sip_history=sip_history, sip_live=sip_live)

    @staticmethod
    def _entitled(ask: Callable[[], object]) -> bool:
        """Whether Alpaca answered, or refused with 403: not entitled."""
        try:
            ask()
        except VendorError as refused:
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
        return str(parsed["message"])[:200]
    return text[:200]


def _iso(at_ns: int) -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(at_ns // NS))


def _new_york_end_ns(day: str) -> int:
    """The end of a New York calendar date, in UTC nanoseconds."""
    from .convert import new_york_midnight_ns, next_date

    return new_york_midnight_ns(next_date(day))
