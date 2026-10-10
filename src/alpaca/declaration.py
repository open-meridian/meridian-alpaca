"""What this version declares beside its role (W8.1, W4.1): its catalogue, its
settings and the storage it asks for.

Built from the plugin's code and used twice: `__main__` registers with it,
and `meridian plugin upload` reads the same one from the built image
(pyproject.toml's `[tool.meridian] declaration`).

**The catalogue** (contract v18), two datasets apart, as the lake plan's Q21
has a source's daily and live data:

- `daily`: closes and daily bars, from Alpaca's consolidated (SIP) history;
  its day New York's, ending at the 16:00 close; pulled for a read and
  pushed once a session's close is final; about ten years of history (since
  2016). Not one venue's: the consolidated view.
- `live`: the latest trade's price and the best bid and ask, from SIP where
  the key has it in real time, else IEX; pulled for a read and kept current
  while a standing want stands, once a minute. Each row names the exchange
  it is from, resolved to the venue master's ID; the catalogue, written in
  code before any venue is resolved, names none.

**The terms Alpaca's standard terms impose** (`licence_default`), which the
deployment's admin confirms or replaces on the Data sources page: a
non-professional subscriber receives market data "solely for their personal,
non-business use", nothing redistributed without Alpaca's written consent.
So each dataset's terms are one person's (`personal_use`), shown and used
by its holder (`display`, `derived_use`); the daily one kept, the live one
served and not kept. A summary, not legal advice; the README says the same.

**The storage**, at the edge (decisions/028): Alpaca's raw responses, every
row's raw record, and the product map; a year kept.
"""

from __future__ import annotations

from meridian import DatasetDeclaration, DatasetLicence
from meridian.declaration import Declaration, Storage

from .convert import DAY_END_MINUTE
from .lake import DAILY, LIVE, LIVE_CADENCE_SECONDS
from .settings import DECLARED

VENDOR = "Alpaca"
#: How long a raw response is kept: the reach of a re-conversion.
RETENTION_DAYS = 365
#: Alpaca's consolidated history reaches back to 2016.
HISTORY_DAYS = 3650

CATALOGUE = (
    DatasetDeclaration(
        key=DAILY,
        vendor=VENDOR,
        data_types=[
            "meridian.v1.Price",
            "meridian.v1.Bar",
            "meridian.v1.Bar.vwap",
            "meridian.v1.Bar.trade_count",
        ],
        modes=["pull", "push"],
        cadence=86_400,
        history=HISTORY_DAYS,
        licence_default=DatasetLicence(
            kept=True, derived_use=True, display=True, personal_use=True
        ),
        day_time_zone="America/New_York",
        day_end_minute=DAY_END_MINUTE,
    ),
    DatasetDeclaration(
        key=LIVE,
        vendor=VENDOR,
        data_types=["meridian.v1.Price"],
        modes=["pull", "stream"],
        cadence=LIVE_CADENCE_SECONDS,
        licence_default=DatasetLicence(
            kept=False, derived_use=True, display=True, personal_use=True
        ),
        day_time_zone="America/New_York",
        day_end_minute=DAY_END_MINUTE,
    ),
)

DECLARATION = Declaration(
    settings=DECLARED,
    storage=Storage(retention_days=RETENTION_DAYS),
    catalogue=CATALOGUE,
)
