"""Converted rows from Alpaca, for a person to eyeball: what `make sample` runs.

It asks Alpaca once each -- which feeds the key is entitled to, the daily
bars, the latest trade and quote, a snapshot, recent trades -- keeps every
response as a raw record under `--raw-dir` (default `recordings/`, which git
ignores: real recordings stay local), and prints each row it converted as
one JSON line, every number its exact decimal text. Nothing is recorded
anywhere else: there is no lake to record into yet.

It needs a key, which `main` is handed and never reads for itself: `make
sample` runs tests/sample_live.py, which takes it from the environment
(`APCA_API_KEY_ID`, `APCA_API_SECRET_KEY`). What it prints says whether each
feed is entitled, never the key.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from collections.abc import Sequence
from pathlib import Path

from . import assets, convert
from .assets import Listing
from .raw import RawStore, storage_root
from .rows import NANOS_PER_SECOND, Bar, Price, Quote, Row, Trade, as_dict
from .settings import FEEDS, IEX, Credentials
from .venue import SIP_DELAY_NS, Alpaca, Entitlements, feed_for

DAY_NS = 86_400 * NANOS_PER_SECOND


class Session:
    """Fetch and convert, one call per step: what the CLI, the live smoke
    test and the conformance adapter share. Each dataset's feed is decided
    once, from the preferred feed and what the key is entitled to."""

    def __init__(
        self, client: Alpaca, preferred: str = IEX, entitled: Entitlements | None = None
    ) -> None:
        self.client, self.preferred, self.entitled = client, preferred, entitled

    def feed(self, kind: str) -> str:
        return feed_for(kind, self.preferred, self.entitled)

    def daily(self, listing: Listing, days: int, now_ns: int) -> tuple[list[Price], list[Bar]]:
        """The last `days` calendar days' daily bars, today's included; SIP
        ends 16 minutes ago, which a free account may ask for."""
        feed = self.feed("daily")
        today = convert.new_york_midnight_ns(convert.new_york_date(now_ns))
        end = now_ns - SIP_DELAY_NS if feed != IEX else now_ns
        closes: list[Price] = []
        bars: list[Bar] = []
        for page in self.client.bars([listing.symbol], today - (days - 1) * DAY_NS, end, feed):
            got = convert.daily(
                listing, page.value["bars"].get(listing.symbol, []), page.raw_key, feed, now_ns
            )
            closes.extend(got[0])
            bars.extend(got[1])
        return closes, bars

    def last(self, listing: Listing) -> Price:
        fetched = self.client.latest_trades([listing.symbol], self.feed("live"))
        item = fetched.value["trades"][listing.symbol]
        return convert.last(listing, item, f"{fetched.raw_key}#{listing.symbol}", fetched.feed)

    def quote(self, listing: Listing) -> Quote:
        fetched = self.client.latest_quotes([listing.symbol], self.feed("live"))
        item = fetched.value["quotes"][listing.symbol]
        return convert.quote(listing, item, f"{fetched.raw_key}#{listing.symbol}", fetched.feed)

    def snapshot(self, listing: Listing) -> list[Row]:
        fetched = self.client.snapshots([listing.symbol], self.feed("live"))
        item = fetched.value[listing.symbol]
        return convert.snapshot(listing, item, fetched.raw_key, fetched.feed)

    def trades(self, listing: Listing, start_ns: int, end_ns: int, limit: int = 100) -> list[Trade]:
        fetched = self.client.trades(listing.symbol, start_ns, end_ns, self.feed("live"), limit)
        items = fetched.value["trades"].get(listing.symbol, [])
        return convert.trades(listing, items, fetched.raw_key, fetched.feed)


def _print(rows: Sequence[Row]) -> None:
    for row in rows:
        print(json.dumps({"row": type(row).__name__, **as_dict(row)}, sort_keys=False))


def main(argv: Sequence[str], credentials: Credentials) -> int:
    parser = argparse.ArgumentParser(prog="make sample")
    parser.add_argument("--symbol", default="SPY", help="a US equity or ETF, as Alpaca spells it")
    parser.add_argument("--feed", choices=FEEDS, default=IEX, help="sip: where entitled")
    parser.add_argument("--days", type=int, default=7, help="calendar days of daily bars")
    parser.add_argument("--trades", type=int, default=5, help="recent trades")
    parser.add_argument("--raw-dir", type=Path, default=Path("recordings"))
    args = parser.parse_args(argv)

    listing = assets.listing(args.symbol)
    store = RawStore(storage_root(args.raw_dir))
    client = Alpaca(store, credentials)
    entitled = client.entitlements()
    session = Session(client, args.feed, entitled)
    print(
        f"# entitled: iex {entitled.iex}, sip history {entitled.sip_history}, sip live "
        f"{entitled.sip_live}; daily from {session.feed('daily')}, live from "
        f"{session.feed('live')}",
        file=sys.stderr,
    )
    if not entitled.answered:
        print(f"# Alpaca refused the key: {entitled.detail}", file=sys.stderr)
        return 1
    now = time.time_ns()
    closes, bars = session.daily(listing, args.days, now)
    _print(closes)
    _print(bars)
    latest = session.last(listing)
    _print([latest, session.quote(listing), *session.snapshot(listing)])
    at = latest.meta.valid_from_ns
    _print(session.trades(listing, at - 60 * NANOS_PER_SECOND, at + NANOS_PER_SECOND, args.trades))
    print(
        f"# {client.calls} calls, {client.rate_limited} rate-limited, raw records in {store.root}",
        file=sys.stderr,
    )
    return 0
