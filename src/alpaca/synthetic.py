"""Synthetic Alpaca: answers the plugin's calls from the built-in exchange,
with no key and no network (the `synthetic` setting, for development, the
tests and the e2e).

`exchanges/exchange.json` is shaped on Alpaca's documented and recorded
responses with every value invented (the lake plan's Q11: real responses
stay in a developer's own storage, never committed): daily bars for SPY,
BRK.B and TQQQ over 2026-10-05 to 2026-10-09, SPY's last VWAP with eighteen
decimal places, BRK.B's bars with no VWAP, and the latest trade and quote of
SPY and BRK.B on IEX, BRK.B's bid side empty. Its numbers are text in the
file and go out as JSON numbers, exactly as written, as Alpaca sends them.
Alpaca leaves out a symbol it has nothing for, and so does this.
"""

from __future__ import annotations

import json
import re
import urllib.parse
from collections.abc import Mapping
from decimal import Decimal, InvalidOperation
from importlib import resources
from pathlib import Path
from typing import Any

from .convert import new_york_date, parse_time
from .vendor import Response

#: The fields Alpaca sends as text, never as numbers.
TEXT_FIELDS = frozenset({"t", "x", "z", "ax", "bx"})


def exchange(path: Path | None = None) -> dict[str, Any]:
    """The built-in exchange, or the one at `path` (a developer's recording)."""
    if path is not None:
        loaded: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
        return loaded
    text = resources.files("alpaca").joinpath("exchanges/exchange.json").read_text("utf-8")
    built: dict[str, Any] = json.loads(text)
    return built


def dumps(value: Any, key: str = "") -> str:
    """JSON with each number written as its text, so nothing passes through a
    float on the way out either."""
    if isinstance(value, dict):
        inner = ",".join(f"{json.dumps(k)}:{dumps(v, k)}" for k, v in value.items())
        return "{" + inner + "}"
    if isinstance(value, list):
        return "[" + ",".join(dumps(v) for v in value) + "]"
    if isinstance(value, str) and key and key not in TEXT_FIELDS:
        try:
            Decimal(value)
        except InvalidOperation:
            return json.dumps(value)
        if re.fullmatch(r"-?\d+(\.\d+)?", value):
            return value
    return json.dumps(value)


class SyntheticTransport:
    """vendor.Transport on the built-in exchange: `/v2/stocks/bars`,
    `/v2/stocks/trades/latest` and `/v2/stocks/quotes/latest`."""

    def __init__(self, held: Mapping[str, Any] | None = None) -> None:
        self.held = held if held is not None else exchange()
        self.asked: list[str] = []

    def __call__(self, url: str, headers: Mapping[str, str]) -> Response:
        self.asked.append(url)
        parsed = urllib.parse.urlparse(url)
        params = {k: v[0] for k, v in urllib.parse.parse_qs(parsed.query).items()}
        symbols = [s for s in params.get("symbols", "").split(",") if s]
        if parsed.path == "/v2/stocks/bars":
            first = params.get("start", "")[:10]
            last = new_york_date(parse_time(params["end"])).isoformat() if "end" in params else ""
            bars = {
                symbol: [
                    bar
                    for bar in self.held.get("bars", {}).get(symbol, [])
                    if (not first or new_york_date(parse_time(bar["t"])).isoformat() >= first)
                    and (not last or new_york_date(parse_time(bar["t"])).isoformat() <= last)
                ]
                for symbol in symbols
            }
            body = {"bars": {s: b for s, b in bars.items() if b}, "next_page_token": None}
            return self._ok({**body, "currency": "USD"})
        if parsed.path in ("/v2/stocks/trades/latest", "/v2/stocks/quotes/latest"):
            kind = "trades" if "trades" in parsed.path else "quotes"
            held = self.held.get(kind, {})
            return self._ok({kind: {s: held[s] for s in symbols if s in held}, "currency": "USD"})
        return Response(404, {"content-type": "application/json"}, b'{"message":"Not Found"}')

    @staticmethod
    def _ok(body: Any) -> Response:
        return Response(200, {"content-type": "application/json"}, dumps(body).encode())
