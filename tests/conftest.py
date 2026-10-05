"""Alpaca's synthetic exchanges, and the adapter the shared conformance suite
runs against.

tests/exchanges/ holds bodies shaped as Alpaca's Market Data API v2
documents them (data.alpaca.markets, read 2026-10-04), with invented values
and no real data: prices are JSON numbers, as Alpaca sends them, sizes
integers of shares, times RFC 3339 to the nanosecond, a daily bar's time the
start of its New York day. Real recordings are never committed (the lake
plan's Q11): `make live` and `make sample` write them under recordings/,
which git ignores.

`Replay` answers the client's requests from those files, by the request's
path and feed, through the client's real request, raw-record and parsing
path, and holds what it was sent so a test can look for the key in it;
`Adapter` is what tests/test_conformance.py, the retail pair's suite, asks
for: the suite's expectations, in contract terms, held against this
plugin's exchanges.

KEY is an invented key, the only one the tests ever hold: the product
owner's is never read, here or anywhere in CI.
"""

from __future__ import annotations

import itertools
from collections.abc import Iterator, Mapping
from pathlib import Path
from types import ModuleType
from typing import Any
from urllib.parse import parse_qs, urlsplit

import pytest

from alpaca import assets, convert, rows
from alpaca.assets import Listing
from alpaca.raw import RawStore
from alpaca.rows import Bar, DatasetDeclaration, Price, Quote, RawRecordRef, Trade
from alpaca.sample import Session
from alpaca.settings import SIP, Credentials
from alpaca.venue import Alpaca, Entitlements, RateLimiter, Response

EXCHANGES = Path(__file__).parent / "exchanges"
#: The synthetic exchanges' now: noon in New York on Friday 2026-10-02, the
#: day still forming (16:00Z, New York on daylight time).
NOW_NS = 1_790_956_800 * 1_000_000_000

#: One clock for the whole run, so no two raw records share a key.
TICKS = itertools.count(NOW_NS, 1_000)

#: An invented key, never a real one.
KEY = Credentials("PKTESTKEYID0000000000", "testsecret0000000000000000000000000000000")

#: The body each path answers with, whichever feed is asked.
ROUTES = {
    "/v2/stocks/bars": "bars-daily.json",
    "/v2/stocks/trades": "trades.json",
    "/v2/stocks/trades/latest": "trades-latest.json",
    "/v2/stocks/quotes/latest": "quotes-latest.json",
    "/v2/stocks/snapshots": "snapshots.json",
}

#: A free account: IEX in real time, SIP past its latest 15 minutes.
FREE = Entitlements(answered=True, iex=True, sip_history=True, sip_live=False)


def pytest_addoption(parser: pytest.Parser) -> None:
    parser.addoption("--live", action="store_true", help="call Alpaca with the environment's key")


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    live = config.getoption("--live")
    for item in items:
        if ("live" in item.keywords) != live:
            item.add_marker(pytest.mark.skip(reason="make live" if not live else "not live"))


class Replay:
    """A transport answering from the synthetic exchanges, by path and feed.

    `routes` maps a path, or a path and a feed (`/v2/stocks/quotes/latest
    sip`), to a file; `refuse` maps the same to a status and a file, as many
    times as listed (`[429, 429]` refuses twice, then answers)."""

    def __init__(
        self,
        routes: Mapping[str, str],
        refuse: Mapping[str, list[tuple[int, str]]] | None = None,
        headers: Mapping[str, str] | None = None,
    ) -> None:
        self.routes = dict(routes)
        self.refuse = {k: list(v) for k, v in (refuse or {}).items()}
        self.headers = dict(headers or {})
        self.asked: list[tuple[str, dict[str, str]]] = []

    def __call__(self, url: str, headers: Mapping[str, str]) -> Response:
        self.asked.append((url, dict(headers)))
        parts = urlsplit(url)
        feed = parse_qs(parts.query).get("feed", [""])[0]
        page = parse_qs(parts.query).get("page_token", [""])[0]
        for key in (f"{parts.path} {feed} {page}".strip(), f"{parts.path} {feed}", parts.path):
            pending = self.refuse.get(key)
            if pending:
                status, name = pending.pop(0)
                body = (EXCHANGES / name).read_bytes()
                return Response(status, {"content-type": "application/json", **self.headers}, body)
        for key in (f"{parts.path} {feed} {page}".strip(), f"{parts.path} {feed}", parts.path):
            if key in self.routes:
                body = (EXCHANGES / self.routes[key]).read_bytes()
                return Response(200, {"content-type": "application/json"}, body)
        return Response(404, {}, b'{"message":"Not Found"}')


class Sleeps(list[float]):
    def __call__(self, seconds: float) -> None:
        self.append(seconds)


def client(
    root: Path,
    routes: Mapping[str, str] | None = None,
    credentials: Credentials | None = KEY,
    **kwargs: Any,
) -> tuple[Alpaca, Replay]:
    """A client on the synthetic exchanges, its clock fixed near NOW_NS."""
    replay = Replay(
        {**ROUTES, **(routes or {})}, kwargs.pop("refuse", None), kwargs.pop("headers", None)
    )
    return (
        Alpaca(
            RawStore(root),
            credentials,
            transport=replay,
            limiter=kwargs.pop("limiter", RateLimiter(1000.0, 1000)),
            clock_ns=lambda: next(TICKS),
            sleep=kwargs.pop("sleep", Sleeps()),
            **kwargs,
        ),
        replay,
    )


class Adapter:
    """What the retail pair's conformance suite asks of a plugin: its rows,
    from its own synthetic exchanges, for the suite's venue-neutral facts.

    Alpaca serves the suite's daily facts from SIP past its latest 15
    minutes (consolidated, which a free account is entitled to) and its live
    facts from IEX, as a free account with the feed set to SIP would."""

    plugin = "alpaca"
    rows: ModuleType = rows
    DAILY_DATASET = "daily-sip"
    LIVE_DATASET = "live-iex"

    def __init__(self, root: Path) -> None:
        self.root = root
        self.store = RawStore(root)

    def _session(self, routes: Mapping[str, str] | None = None) -> Session:
        alpaca, _ = client(self.root, routes)
        return Session(alpaca, SIP, FREE)

    def listing(self, code: str) -> Listing:
        return assets.listing(code)

    def daily(self, scenario: str) -> tuple[list[Price], list[Bar]]:
        session = self._session({"/v2/stocks/bars": f"bars-{scenario}.json"})
        return session.daily(assets.listing("TQQQ"), 3, NOW_NS)

    def last(self) -> Price:
        return self._session().last(assets.listing("TQQQ"))

    def quote(self, scenario: str = "latest") -> Quote:
        session = self._session({"/v2/stocks/quotes/latest": f"quotes-{scenario}.json"})
        return session.quote(assets.listing("TQQQ"))

    def trades(self) -> list[Trade]:
        return self._session().trades(assets.listing("TQQQ"), NOW_NS - 60 * 10**9, NOW_NS)

    def datasets(self) -> tuple[DatasetDeclaration, ...]:
        return convert.CATALOGUE

    def raw(self, ref: RawRecordRef) -> dict[str, Any] | None:
        return self.store.get(ref.key)


@pytest.fixture
def conformance(tmp_path: Path) -> Adapter:
    return Adapter(tmp_path / "raw")


@pytest.fixture
def exchanges() -> Iterator[Path]:
    yield EXCHANGES


def body(name: str) -> Any:
    return rows.json_decimal((EXCHANGES / name).read_bytes())


def kept_text(root: Path) -> str:
    """Every raw record under `root`, as one text, to look for a key in."""
    return "\n".join(path.read_text() for path in sorted(root.rglob("*.json*")))
