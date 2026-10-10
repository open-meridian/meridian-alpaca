"""The live check, run by the account holder on their own machine with their own
key, never in CI and never by an agent: `make live`.

    make live            # asks for the key ID and secret key, typed unseen

The key is read from standard input, two lines, which `make live` fills from
a prompt that does not echo; it is never an argument, an environment
variable or a file, and is never printed. It asks Alpaca which feeds the key
is entitled to, reads a week of SPY's and BRK.B's daily bars from SIP and
their latest trade and quote, converts them as the plugin does, listens to
Alpaca's stream for them for up to twenty seconds, and prints what it found:
booleans, counts and converted values, never the key. It
then checks that no raw record holds either part of the key.

What Alpaca answered is kept under `recordings/` (git-ignored: real responses
are the account holder's, for their personal use), each response as a raw
record, and one `exchange.json` shaped as the synthetic exchange, which
`make e2e-recorded RECORDED=recordings/<when>` replays through the e2e.
"""

from __future__ import annotations

import asyncio
import datetime as dt
import json
import sys
import time
from decimal import Decimal
from pathlib import Path
from typing import Any

from . import convert
from .convert import SourceQuote, SourceTrade
from .raw import RawStore
from .settings import Credentials
from .stream import AlpacaFeed
from .vendor import IEX, SIP, Alpaca, VendorError

SYMBOLS = ("SPY", "BRK.B")
#: How long the stream is listened to, at most, and how many messages.
STREAM_SECONDS, STREAM_MESSAGES = 20.0, 200


async def listen(key: Credentials, feed: str, store: RawStore) -> dict[str, Any]:
    """A short session of Alpaca's stream for SYMBOLS: whether it
    authenticated, what it stated, counted, and the condition codes seen
    with how many converted."""
    from .conditions import trade_attributes

    stream = AlpacaFeed(key, feed, store)
    heard: dict[str, Any] = {"feed": feed, "authenticated": False, "trades": 0, "quotes": 0}
    codes: set[str] = set()
    unconverted: set[str] = set()
    try:
        await stream.open()
        heard["authenticated"] = True
        await stream.subscribe(list(SYMBOLS))
        until = time.monotonic() + STREAM_SECONDS
        messages = 0
        while messages < STREAM_MESSAGES and time.monotonic() < until:
            try:
                stated = await asyncio.wait_for(stream.receive(), until - time.monotonic())
            except TimeoutError:
                break
            messages += len(stated)
            for each in stated:
                if isinstance(each, SourceTrade):
                    heard["trades"] += 1
                    codes.update(f"{each.tape}:{c}" for c in each.codes)
                    said = trade_attributes(each.codes, each.tape)
                    unconverted.update(f"{each.tape}:{u.code}" for u in said.unconverted)
                elif isinstance(each, SourceQuote):
                    heard["quotes"] += 1
    except Exception as failed:  # noqa: BLE001 - said, for the account holder
        heard["error"] = f"{type(failed).__name__}: {failed}"[:200]
    finally:
        await stream.close()
    heard["codes"], heard["unconverted_codes"] = sorted(codes), sorted(unconverted)
    return heard


def _text(value: Any) -> Any:
    """A parsed body with each number as its text, as exchange.json holds it."""
    if isinstance(value, dict):
        return {k: _text(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_text(v) for v in value]
    if isinstance(value, Decimal | int) and not isinstance(value, bool):
        return str(value)
    return value


def main(argv: list[str]) -> int:
    out = Path(argv[0] if argv else "recordings") / time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    lines = sys.stdin.read().splitlines()
    if len(lines) < 2 or not lines[0].strip() or not lines[1].strip():
        print("live: give the key ID and the secret key, one a line, on standard input")
        return 2
    key = Credentials(lines[0].strip(), lines[1].strip())
    alpaca = Alpaca(RawStore(out / "raw"), key)
    said: dict[str, Any] = {}
    try:
        entitled = alpaca.entitlements()
    except VendorError as failed:
        print(f"live: Alpaca did not answer: {failed.status} {failed.detail}")
        return 1
    said["answered"] = entitled.answered
    said["iex"], said["sip_history"], said["sip_live"] = (
        entitled.iex,
        entitled.sip_history,
        entitled.sip_live,
    )
    if not entitled.answered:
        print(json.dumps({**said, "detail": entitled.detail}, indent=1))
        return 1
    today = dt.datetime.now(dt.UTC).date()
    first = min(dt.date(2026, 10, 5), today - dt.timedelta(days=10))
    pages = alpaca.daily_bars(list(SYMBOLS), first.isoformat(), today.isoformat(), SIP)
    bars = [bar for page in pages for bar in convert.daily_bars(page)]
    feed = entitled.live_feed
    trades = alpaca.latest_trades(list(SYMBOLS), feed)
    quotes = alpaca.latest_quotes(list(SYMBOLS), feed)
    live = [*convert.latest_trades(trades), *convert.latest_quotes(quotes)]
    said["daily_bars"] = {s: sum(1 for b in bars if b.symbol == s) for s in SYMBOLS}
    said["closes"] = [
        {"symbol": b.symbol, "business_date": b.business_date.isoformat(), "close": str(b.close)}
        for b in bars[-4:]
    ]
    said["live_feed"] = feed
    said["live"] = [
        {"symbol": q.symbol, "kind": q.kind, "price": str(q.amount), "venue_code": q.venue_code}
        for q in live
    ]
    said["unconverted"] = sum(len(b.unconverted) for b in bars) + sum(
        len(q.unconverted) for q in live
    )
    said["stream"] = asyncio.run(listen(key, feed, RawStore(out / "raw")))
    said["calls"], said["rate_limited"] = alpaca.usage.calls, alpaca.usage.rate_limited
    kept = "".join(p.read_text() for p in (out / "raw").rglob("*.json*"))
    said["key_in_raw_records"] = any(secret in kept for secret in key.secrets())
    exchange = {
        "_about": "Recorded from Alpaca by `make live`, for the account holder's personal use.",
        "bars": {s: _text(b) for page in pages for s, b in (page.value.get("bars") or {}).items()},
        "trades": _text(trades.value.get("trades") or {}),
        "quotes": _text(quotes.value.get("quotes") or {}),
    }
    (out / "exchange.json").write_text(json.dumps(exchange, indent=1))
    said["recorded_in"] = str(out)
    print(json.dumps(said, indent=1))
    return 1 if said["key_in_raw_records"] or feed not in (IEX, SIP) else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
