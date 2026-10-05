"""A US equity or ETF symbol, as the subject of a row.

Alpaca's stock market data names a listing by its symbol (`TQQQ`, `SPY`,
`BRK.B`) and nothing else: no FIGI, no CUSIP, no ISIN, and nothing that says
whether it is a share or a fund. So, as decisions/030 has it, a row's
subject is **the deployment's instrument by the venue's symbol, namespaced
to the venue** -- `symbol` `BRK.B`, source `alpaca` -- which W3.1 resolves
to the deployment's own instrument or W3.2 reports as missing for a person
to complete. There is no global ID, and none is guessed from a ticker; the
asset class (equity or fund) is the instrument record's, completed there,
not this plugin's to say. A FIGI would go beside the symbol only if Alpaca
stated one, and its market data states none.

Every price is in US dollars, the cash instrument of ISO 4217's `USD`:
Alpaca's stock data is priced in USD unless asked otherwise, and this
plugin never asks otherwise. A response that names another currency is
refused, never converted.

A share class is spelled with a dot (`BRK.B`), as Alpaca spells it; a
symbol in another venue's spelling (`BRK/B`, `BRK-B`) is refused rather
than rewritten, so one listing has one spelling on this venue.

The lake plan's "Licensed identifiers are fetched per subject, never in
bulk": nothing here reads Alpaca's asset list.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from decimal import Decimal

from .rows import Identifier, Money, SubjectRef, currency, symbol

VENUE = "alpaca"
USD = "USD"

#: A US listing as Alpaca spells it: a root of capitals (and digits after the
#: first), and a class or series after a dot.
_SYMBOL = re.compile(r"[A-Z][A-Z0-9]{0,5}(?:\.[A-Z0-9]{1,4})?")


@dataclass(frozen=True)
class Listing:
    """A US equity or ETF on Alpaca: its symbol, namespaced to the venue,
    and the currency its prices are in. Never a global identifier."""

    code: Identifier
    priced_in: Identifier

    def subject(self) -> SubjectRef:
        return SubjectRef(self.code)

    def money(self, amount: Decimal) -> Money:
        return Money(amount, self.priced_in)

    @property
    def symbol(self) -> str:
        return self.code.value


def listing(code: str, priced_in: str = USD) -> Listing:
    """The listing Alpaca calls `code`; ValueError for anything Alpaca would
    not spell so, or priced in anything but US dollars."""
    if not _SYMBOL.fullmatch(code):
        raise ValueError(
            f"{code!r} is not a symbol as Alpaca spells one (capitals, a class after a dot: BRK.B)"
        )
    if priced_in != USD:
        raise ValueError(
            f"Alpaca priced {code} in {priced_in!r}; this plugin reads US dollars only"
        )
    return Listing(symbol(code, VENUE), currency(USD))
