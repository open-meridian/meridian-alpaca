"""Alpaca, as the lake's half of the plugin asks it (lake.Source).

Daily bars from SIP's consolidated history, past a free account's
restricted 15 minutes; live prices from SIP where the key has it in real
time, else from IEX, the one exchange a free account sees, each row naming
the exchange it is from. Which feeds the key is entitled to is asked once,
and again when the key changes; the Connection page shows the answer.
"""

from __future__ import annotations

import datetime as dt
import threading
from collections.abc import Sequence

from . import convert
from .convert import DailyBar, Quoted
from .vendor import IEX, SIP, SYMBOLS_PER_CALL, Alpaca, Entitlements


class AlpacaSource:
    """lake.Source on Alpaca's Market Data API."""

    def __init__(self, alpaca: Alpaca, synthetic: bool = False) -> None:
        self.alpaca = alpaca
        self.synthetic = synthetic
        self._entitled: Entitlements | None = None
        self._lock = threading.Lock()

    def entitlements(self, again: bool = False) -> Entitlements:
        """The feeds the key answered for, asked once (synthetic: IEX and SIP's
        history, as a free account has)."""
        with self._lock:
            if self._entitled is None or again:
                if self.synthetic:
                    self._entitled = Entitlements(answered=True, iex=True, sip_history=True)
                else:
                    self._entitled = self.alpaca.entitlements()
            return self._entitled

    @property
    def known(self) -> Entitlements | None:
        return self._entitled

    def daily(self, symbols: Sequence[str], first: dt.date, last: dt.date) -> list[DailyBar]:
        bars: list[DailyBar] = []
        for start in range(0, len(symbols), SYMBOLS_PER_CALL):
            chunk = symbols[start : start + SYMBOLS_PER_CALL]
            for page in self.alpaca.daily_bars(chunk, first.isoformat(), last.isoformat(), SIP):
                bars.extend(convert.daily_bars(page))
        return bars

    def live(self, symbols: Sequence[str]) -> list[Quoted]:
        feed = self.entitlements().live_feed if not self.synthetic else IEX
        quoted: list[Quoted] = []
        for start in range(0, len(symbols), SYMBOLS_PER_CALL):
            chunk = symbols[start : start + SYMBOLS_PER_CALL]
            quoted.extend(convert.latest_trades(self.alpaca.latest_trades(chunk, feed)))
            quoted.extend(convert.latest_quotes(self.alpaca.latest_quotes(chunk, feed)))
        return quoted
