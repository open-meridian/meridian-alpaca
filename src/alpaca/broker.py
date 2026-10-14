"""Alpaca's Trading API v2: the one module that speaks to it (from 0.7.0).

The account side, custody (from 0.8.0) and connectivity, reads and trades
through the Trading API of the environment chosen (settings.py): its live
service, `https://api.alpaca.markets`, or its paper one,
`https://paper-api.alpaca.markets`, the same API with simulated money.

- custody reads `GET /v2/account` (the account, its number, status and
  cash), `GET /v2/positions` and `GET /v2/account/activities`;
- connectivity reads `GET /v2/account` (the account's number, its venue
  account code) and `GET /v2/orders`, and places, replaces and cancels with
  `POST /v2/orders`, `PATCH /v2/orders/{id}` and `DELETE /v2/orders/{id}`.

**The key.** Alpaca's two headers, from `settings.Credentials`, on the
request and nowhere else: a raw record keeps the request's other headers and
its body, and any text holding either part of the key is redacted before it
is kept. Without a key, nothing is asked.

**Every exchange is a raw record first** (kinds.py), with its request and
the moment its answer arrived, before anything reads it: a read under
`responses`, a page of activities under `activity`, a send under `trading`.
A send that never got an answer is kept too, saying so, and raises
`Unanswered`: it may have reached Alpaca, and is never sent again on its own
(trading.py). Alpaca's numbers are text; a body is parsed with every number
a Decimal from its own text.

**Rate limits.** Alpaca allows a key 200 Trading API calls a minute; this
client spends at most a hundred, one at a time (vendor.RateLimiter). A 429
is answered by waiting and asking again, a read only: a send is never asked
twice.

Synthetic mode hands the client a simulated brokerage as its transport
(synthetic_broker.py), so the same code is exercised and nothing reaches
Alpaca.
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from .kinds import ACTIVITY, RESPONSES, TRADING, Store
from .settings import LIVE, Credentials
from .vendor import REDACTED, RateLimiter, Response, parse

LIVE_BASE = "https://api.alpaca.markets"
PAPER_BASE = "https://paper-api.alpaca.markets"
#: The trade updates stream of each environment.
LIVE_STREAM = "wss://api.alpaca.markets/stream"
PAPER_STREAM = "wss://paper-api.alpaca.markets/stream"
USER_AGENT = "meridian-alpaca/0.8.0 (+https://open-meridian.dev)"
LIMIT_PER_MINUTE = 100
#: Alpaca's most activities in one page.
ACTIVITY_PAGE = 100
#: The most orders one read lists.
ORDERS_PAGE = 500
KEPT_HEADERS = ("date", "content-type", "x-ratelimit-remaining", "x-request-id")

#: One request: its method, URL, headers and body, answered.
Send = Callable[[str, str, Mapping[str, str], bytes | None], Response]


def base_for(environment: str) -> str:
    return LIVE_BASE if environment == LIVE else PAPER_BASE


def stream_for(environment: str) -> str:
    return LIVE_STREAM if environment == LIVE else PAPER_STREAM


def urllib_send(method: str, url: str, headers: Mapping[str, str], body: bytes | None) -> Response:
    """One request over HTTPS, with the standard library. A connection that
    fails raises OSError: no answer."""
    request = urllib.request.Request(url, data=body, headers=dict(headers), method=method)
    try:
        with urllib.request.urlopen(request, timeout=15) as answer:
            return Response(
                answer.status, {k.lower(): v for k, v in answer.headers.items()}, answer.read()
            )
    except urllib.error.HTTPError as answer:
        return Response(
            answer.code, {k.lower(): v for k, v in answer.headers.items()}, answer.read()
        )


class BrokerError(Exception):
    """Alpaca answered something other than what was asked: its status, and
    its code and words as it said them. Never a header, never the key."""

    def __init__(self, call: str, status: int, code: str, text: str, raw_key: str) -> None:
        super().__init__(f"{call}: {status} {code} {text}".replace("  ", " ").strip())
        self.call, self.status, self.code, self.text, self.raw_key = (
            call,
            status,
            code,
            text,
            raw_key,
        )


class Unanswered(Exception):
    """A request Alpaca never answered: it may have reached Alpaca."""

    def __init__(self, call: str, why: str, raw_key: str) -> None:
        super().__init__(f"{call}: no answer ({why})")
        self.call, self.why, self.raw_key = call, why, raw_key


class NoKey(Exception):
    """There is no key to ask Alpaca with: nothing was asked."""


@dataclass(frozen=True)
class Answer:
    """A parsed body, the key of the raw record it was kept as, and when it
    arrived."""

    value: Any
    raw_key: str
    received_at: datetime


class Broker:
    """Alpaca's Trading API for one environment, every exchange kept."""

    def __init__(
        self,
        store: Store,
        credentials: Credentials | None,
        base: str,
        send: Send = urllib_send,
        limiter: RateLimiter | None = None,
        now: Callable[[], datetime] = lambda: datetime.now(UTC),
        sleep: Callable[[float], None] = time.sleep,
        keyless: bool = False,
        synthetic: bool = False,
    ) -> None:
        self.store, self.credentials, self.base, self.send = store, credentials, base, send
        self.limiter = limiter or RateLimiter(LIMIT_PER_MINUTE)
        self.now, self.sleep = now, sleep
        self.keyless, self.synthetic = keyless, synthetic
        self.calls = 0
        self.last_error = ""

    def _redacted(self, text: str) -> str:
        if self.credentials is not None:
            for secret in self.credentials.secrets():
                text = text.replace(secret, REDACTED)
        return text

    def _ask(
        self,
        call: str,
        kind: str,
        name: str,
        method: str,
        path: str,
        params: Mapping[str, str] | None = None,
        body: Mapping[str, Any] | None = None,
    ) -> Answer:
        """One exchange, kept as a raw record before anything reads it."""
        if self.credentials is None and not self.keyless:
            raise NoKey(f"{call}: no Alpaca key is set, so nothing was asked")
        url = f"{self.base}{path}"
        if params:
            url += "?" + urllib.parse.urlencode(params)
        sent = None if body is None else json.dumps(body, default=str).encode()
        kept_headers = {"User-Agent": USER_AGENT, "Accept": "application/json"}
        if sent is not None:
            kept_headers["Content-Type"] = "application/json"
        headers = dict(kept_headers)
        if self.credentials is not None:
            headers.update(self.credentials.headers())
        request = {
            "method": method,
            "url": url,
            "headers": kept_headers,
            "body": None if sent is None else self._redacted(sent.decode()),
        }
        reads = method == "GET"
        for attempt in range(4 if reads else 1):
            self.limiter.acquire()
            self.calls += 1
            try:
                answer = self.send(method, url, headers, sent)
            except OSError as failed:
                why = type(failed).__name__
                key = self.store.keep(
                    kind,
                    name,
                    {
                        "venue": "alpaca",
                        "call": call,
                        "request": request,
                        "answered": False,
                        "why": why,
                        "synthetic": self.synthetic,
                    },
                    self.now(),
                )
                self.last_error = f"{call}: no answer ({why})"
                raise Unanswered(call, why, key) from None
            text = self._redacted(answer.body.decode("utf-8", errors="replace"))
            key = self.store.keep(
                kind,
                name,
                {
                    "venue": "alpaca",
                    "call": call,
                    "request": request,
                    "answered": True,
                    "status": answer.status,
                    "headers": {
                        h: self._redacted(answer.headers[h])
                        for h in KEPT_HEADERS
                        if h in answer.headers
                    },
                    "body": text,
                    "synthetic": self.synthetic,
                },
                self.now(),
            )
            if answer.status == 429 and reads and attempt < 3:
                self.sleep(2.0**attempt)
                continue
            if answer.status >= 300:
                code, words = _said(text)
                self.last_error = f"{call}: {answer.status} {words}"[:200]
                raise BrokerError(call, answer.status, code, words, key)
            try:
                value = parse(text) if text.strip() else None
            except json.JSONDecodeError:
                raise BrokerError(call, answer.status, "", "not JSON", key) from None
            self.last_error = ""
            return Answer(value, key, self.now())
        raise AssertionError("unreachable")

    def account(self) -> Answer:
        """The account the key reaches: its number is its venue account code."""
        return self._ask("account", RESPONSES, "account", "GET", "/v2/account")

    # ── Custody's reads, from 0.8.0 ─────────────────────────────────────

    def positions(self) -> Answer:
        """The account's open positions, as Alpaca states them now."""
        return self._ask("positions", RESPONSES, "positions", "GET", "/v2/positions")

    def activities(self, after: str = "", page_token: str = "") -> Answer:
        """One page of the account's activities, oldest first, after a date."""
        params = {"direction": "asc", "page_size": str(ACTIVITY_PAGE)}
        if after:
            params["after"] = after
        if page_token:
            params["page_token"] = page_token
        return self._ask(
            "activities", ACTIVITY, "activities", "GET", "/v2/account/activities", params
        )

    def orders(self) -> Answer:
        """Every order of the account, open and closed, the latest first."""
        params = {"status": "all", "limit": str(ORDERS_PAGE), "nested": "false"}
        return self._ask("orders", RESPONSES, "orders", "GET", "/v2/orders", params)

    def place(self, body: Mapping[str, Any]) -> Answer:
        return self._ask("placing an order", TRADING, "place", "POST", "/v2/orders", body=body)

    def replace(self, order_id: str, body: Mapping[str, Any]) -> Answer:
        return self._ask(
            "replacing an order",
            TRADING,
            "replace",
            "PATCH",
            f"/v2/orders/{urllib.parse.quote(order_id)}",
            body=body,
        )

    def cancel(self, order_id: str) -> Answer:
        return self._ask(
            "cancelling an order",
            TRADING,
            "cancel",
            "DELETE",
            f"/v2/orders/{urllib.parse.quote(order_id)}",
        )


def _said(text: str) -> tuple[str, str]:
    """Alpaca's code and words from an error's body (`{"code", "message"}`)."""
    try:
        parsed = json.loads(text, parse_float=Decimal)
    except json.JSONDecodeError:
        return "", text[:200]
    if isinstance(parsed, dict):
        return str(parsed.get("code") or ""), str(parsed.get("message") or text)[:200]
    return "", text[:200]
