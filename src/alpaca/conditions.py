"""Alpaca's trade and quote condition codes, converted to the platform's trade
attributes at the edge (contract v19, W10.4; plans/the-lake-prices-the-book,
row 19).

Alpaca prints a US stock trade's conditions as the securities information
processors' own letters, and which processor's depends on the tape (`z`):
tapes A and B are the CTA's (the Consolidated Tape System), tape C the
UTP's. Most letters mean the same on both; a few do not (`B` is an average
price trade on the CTA's tape and a bunched trade on the UTP's), so each
letter is read on its own tape. What a letter means here is the plugin's
conversion (step 5's principle 3: how it converts its codes is the
plugin's), taken from the two processors' published sale-condition matrices:
which characteristic it says the trade had, and whether the trade may set
the high and low, the open, the close and the volume, of the consolidated
bar and of its market centre's.

A trade's codes combine: its characteristics are each code's, and it may set
a statistic only where every code allows it. A code this table does not hold
is kept as reported (`alpaca:trade.c`, with its tape), never guessed: the
trade's eligibilities are then not known, and the code is counted on the
Datasets page for a person to map.

No vendor's or processor's code reaches the lake as the meaning of anything:
only the platform's attributes, and the codes as reported beside them.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass

from meridian.edge import as_reported
from meridian.plugin.v1.operations_pb2 import AsReported

YES, NO = True, False
CTA_TAPES = frozenset({"A", "B"})
UTP_TAPES = frozenset({"C"})


@dataclass(frozen=True)
class Rule:
    """What one code means: its characteristics, and whether a trade carrying
    it may set each statistic -- (high and low, open, close, volume) -- of the
    consolidated bar and of its market centre's."""

    characteristics: tuple[str, ...]
    consolidated: tuple[bool, bool, bool, bool]
    market_centre: tuple[bool, bool, bool, bool]


def _rule(*characteristics: str, cons: str = "YYYY", centre: str | None = None) -> Rule:
    flags = tuple(c == "Y" for c in cons)
    held = tuple(c == "Y" for c in (centre or cons))
    return Rule(characteristics, flags, held)  # type: ignore[arg-type]


#: A regular trade: every statistic, both bars.
REGULAR = _rule()
#: Counted in the volume only.
VOLUME_ONLY = "NNNY"

#: The letters both processors give one meaning.
BOTH: dict[str, Rule] = {
    "@": REGULAR,
    " ": REGULAR,
    "C": _rule("non_regular_settlement", cons=VOLUME_ONLY),
    "F": _rule("intermarket_sweep"),
    "H": _rule(cons=VOLUME_ONLY),
    "I": _rule("odd_lot", cons=VOLUME_ONLY),
    "K": REGULAR,
    "L": _rule("late_report"),
    "M": _rule(cons="NNNN", centre="NNYN"),
    "N": _rule("non_regular_settlement", cons=VOLUME_ONLY),
    "O": _rule("opening_auction"),
    "P": _rule("prior_reference_price", cons="YNNY"),
    "Q": _rule(cons="NNNN", centre="NYNN"),
    "R": _rule("non_regular_settlement", cons=VOLUME_ONLY),
    "T": _rule("extended_hours", cons=VOLUME_ONLY),
    "U": _rule("extended_hours", "out_of_sequence", cons=VOLUME_ONLY),
    "V": _rule("contingent", cons=VOLUME_ONLY),
    "X": _rule("cross"),
    "Z": _rule("out_of_sequence", cons="YNNY"),
    "4": _rule("derivatively_priced", cons="YNNY"),
    "5": _rule("other_auction"),
    "6": _rule("closing_auction"),
    "7": _rule("contingent", cons=VOLUME_ONLY),
    "9": _rule(cons="NNYN", centre="NNNN"),
}

#: The letters only the CTA's tapes (A and B) give this meaning.
CTA: dict[str, Rule] = {
    **BOTH,
    "B": _rule("average_price", cons=VOLUME_ONLY),
    "E": REGULAR,
}

#: The letters only the UTP's tape (C) gives this meaning.
UTP: dict[str, Rule] = {
    **BOTH,
    "A": REGULAR,
    "B": REGULAR,
    "D": REGULAR,
    "G": _rule("late_report", cons="YNNY"),
    "S": REGULAR,
    "W": _rule("average_price", cons=VOLUME_ONLY),
    "Y": REGULAR,
    "1": REGULAR,
}

#: Quote conditions the plugin converts: a regular, firm quote says nothing;
#: a non-firm one is indicative. Any other is kept as reported.
QUOTE_FIRM = frozenset({"R", "O", " ", "@"})
QUOTE_INDICATIVE = frozenset({"N"})


def _table(tape: str) -> dict[str, Rule]:
    if tape in CTA_TAPES:
        return CTA
    if tape in UTP_TAPES:
        return UTP
    return BOTH


@dataclass(frozen=True)
class Attributes:
    """A trade's attributes as converted: each eligibility True, False, or
    None where a code did not convert (not known)."""

    characteristics: tuple[str, ...]
    consolidated: tuple[bool | None, bool | None, bool | None, bool | None]
    market_centre: tuple[bool | None, bool | None, bool | None, bool | None]
    unconverted: tuple[AsReported, ...] = ()


NOT_KNOWN: tuple[None, None, None, None] = (None, None, None, None)


def trade_attributes(codes: Iterable[object], tape: str) -> Attributes:
    """The platform's attributes for a trade's codes on its tape."""
    table = _table(tape)
    characteristics: list[str] = []
    consolidated = [YES] * 4
    centre = [YES] * 4
    unconverted: list[AsReported] = []
    for code in codes:
        letter = str(code)
        rule = table.get(letter)
        if rule is None:
            unconverted.append(as_reported("alpaca:trade.c", letter[:16], f"tape {tape or '?'}"))
            continue
        for characteristic in rule.characteristics:
            if characteristic not in characteristics:
                characteristics.append(characteristic)
        consolidated = [a and b for a, b in zip(consolidated, rule.consolidated, strict=True)]
        centre = [a and b for a, b in zip(centre, rule.market_centre, strict=True)]
    if unconverted:
        return Attributes(tuple(characteristics), NOT_KNOWN, NOT_KNOWN, tuple(unconverted))
    return Attributes(
        tuple(characteristics),
        tuple(consolidated),  # type: ignore[arg-type]
        tuple(centre),  # type: ignore[arg-type]
    )


def quote_characteristics(
    codes: Sequence[object],
) -> tuple[tuple[str, ...], tuple[AsReported, ...]]:
    """A quote's characteristics, and its codes kept as reported where none
    converts."""
    characteristics: list[str] = []
    unconverted: list[AsReported] = []
    for code in codes:
        letter = str(code)
        if letter in QUOTE_FIRM:
            continue
        if letter in QUOTE_INDICATIVE:
            if "indicative" not in characteristics:
                characteristics.append("indicative")
            continue
        unconverted.append(as_reported("alpaca:quote.c", letter[:16]))
    return tuple(characteristics), tuple(unconverted)
