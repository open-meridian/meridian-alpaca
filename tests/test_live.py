"""The live smoke test: Alpaca's real endpoints, with the environment's key,
converted.

Opt in only: `make live` runs `pytest --live` with `APCA_API_KEY_ID` and
`APCA_API_SECRET_KEY` passed in from the developer's environment
(tests/keys.py), and CI never does. It asks which feeds the key is entitled
to, then each endpoint once (a dozen calls, well inside the rate limit), and
checks the conversion holds on real data: every row converts with nothing as
reported but condition codes, every number exact, the daily closes on
consecutive trading days at New York's midnight, bid below ask, every live
row naming its feed's dataset and, on IEX, IEX's MIC -- and that neither
part of the key reached any raw record. The real responses are written under
recordings/, which git ignores (the lake plan's Q11). What it saw is
printed, and kept as recordings/<run>/summary.json: booleans and figures,
never the key.
"""

from __future__ import annotations

import json
import time
from collections import Counter
from collections.abc import Iterator
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

from alpaca import assets, convert
from alpaca.raw import RawStore
from alpaca.sample import Session
from alpaca.settings import IEX, SIP
from alpaca.venue import Alpaca

import keys
from conftest import kept_text

pytestmark = pytest.mark.live

RECORDINGS = Path(__file__).resolve().parent.parent / "recordings"
SYMBOLS = ("SPY", "TQQQ", "BRK.B")


def scale(value: Decimal) -> int:
    exponent = value.as_tuple().exponent
    assert isinstance(exponent, int)
    return max(0, -exponent)


@pytest.fixture(scope="module")
def run() -> Iterator[tuple[Session, dict[str, Any], Path]]:
    credentials = keys.from_environment()
    if credentials is None:
        pytest.fail("make live needs " + " and ".join(keys.missing()) + " in the environment")
    root = RECORDINGS / time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    client = Alpaca(RawStore(root), credentials)
    entitled = client.entitlements()
    assert entitled.answered, f"Alpaca refused the key: {entitled.detail}"
    session = Session(client, SIP, entitled)
    summary: dict[str, Any] = {
        "venue": "alpaca",
        "recordings": str(root),
        "entitled": {
            "iex": entitled.iex,
            "sip_history": entitled.sip_history,
            "sip_live": entitled.sip_live,
        },
        "daily_feed": session.feed("daily"),
        "live_feed": session.feed("live"),
    }
    yield session, summary, root
    summary["calls"] = client.calls
    summary["rate_limited"] = client.rate_limited
    summary["limiter_waited_s"] = round(client.limiter.waited, 3)
    for secret in credentials.secrets():
        assert secret not in kept_text(root), "a part of the key reached a raw record"
    summary["key_in_raw_records"] = False
    root.mkdir(parents=True, exist_ok=True)
    (root / "summary.json").write_text(json.dumps(summary, indent=1, default=str))
    print("\nLIVE SUMMARY " + json.dumps(summary, indent=1, default=str))


def _clean(rows: list[Any]) -> None:
    for row in rows:
        others = [u for u in row.meta.unconverted if ".c:" not in u.scheme]
        assert others == [], (row.meta.row_key, others)


def test_daily_closes_and_bars(run: tuple[Session, dict[str, Any], Path]) -> None:
    session, summary, _ = run
    summary["daily"] = {}
    for code in SYMBOLS:
        listing = assets.listing(code)
        now = time.time_ns()
        closes, bars = session.daily(listing, 10, now)
        _clean(closes + bars)
        assert closes and len(closes) == len(bars)
        dates = [c.meta.business_date for c in closes]
        assert dates == sorted(set(dates)) and dates[-1] <= convert.new_york_date(now)
        for close in closes:
            start = convert.new_york_midnight_ns(close.meta.business_date)
            assert close.meta.valid_from_ns == start
            assert close.meta.source.dataset == convert.dataset("daily", session.feed("daily"))
        assert all(c.meta.source_times for c in closes[:-1])
        summary["daily"][code] = {
            "dates": dates,
            "closes": [str(c.price.amount) for c in closes if c.price],
            "close_scales": sorted({scale(c.price.amount) for c in closes if c.price}),
            "vwap": [str(b.vwap.amount) for b in bars[-2:] if b.vwap],
            "trade_counts": [b.trade_count for b in bars[-2:]],
        }


def test_last_quote_snapshot_and_trades(run: tuple[Session, dict[str, Any], Path]) -> None:
    session, summary, _ = run
    listing = assets.listing("SPY")
    feed = session.feed("live")
    last, quote = session.last(listing), session.quote(listing)
    snapshot = session.snapshot(listing)
    at = last.meta.valid_from_ns
    trades = session.trades(listing, at - 60 * 10**9, at + 10**9, 100)
    _clean([last, quote, *snapshot, *trades])
    assert last.price is not None and last.price.amount > 0
    if quote.bid is not None and quote.ask is not None:
        assert quote.bid.amount <= quote.ask.amount
    assert trades and all(t.aggressor == "" for t in trades)
    for row in (last, quote, *snapshot, *trades):
        assert row.meta.source.dataset == convert.dataset("live", feed)
        if feed == IEX:
            assert row.meta.source.venue in ("IEXG", "")
    conditions = Counter(u.code for t in trades for u in t.meta.unconverted)
    summary["live"] = {
        "feed": feed,
        "last": [str(last.price.amount), last.meta.source.venue],
        "bid": [str(quote.bid.amount) if quote.bid else None, str(quote.bid_quantity)],
        "ask": [str(quote.ask.amount) if quote.ask else None, str(quote.ask_quantity)],
        "quote_venue": quote.meta.source.venue,
        "trades": len(trades),
        "venues": dict(Counter(t.meta.source.venue for t in trades)),
        "conditions": dict(conditions.most_common(10)),
        "price_scales": sorted({scale(t.price.amount) for t in trades if t.price}),
    }
