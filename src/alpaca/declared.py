"""What this version declares beside its role (W8.1, W4.1): its catalogue, its
settings and the storage it asks for.

Built from the plugin's code and used twice: `__main__` registers with it,
and `meridian plugin upload` reads the same one from the built image
(pyproject.toml's `[tool.meridian] declaration`).

**The catalogue** (contracts v18 and v19), three datasets apart, as the lake
plan's Q21 and Q40 have a source's daily and live data and its trades:

- `daily`: closes and daily bars, from Alpaca's consolidated (SIP) history;
  its day New York's, ending at the 16:00 close; pulled for a read and
  pushed once a session's close is final; about ten years of history (since
  2016). Not one venue's: the consolidated view.
- `live`: the latest trade's price and the best bid and ask, and from
  contract v19 the best bid and offer as a quote with its sizes, from SIP
  where the key has it in real time, else IEX; pulled for a read; while a
  standing want stands its prices kept current once a minute and its quotes
  streamed. Each row names the exchange it is from, resolved to the venue
  master's ID; the catalogue, written in code before any venue is resolved,
  names none.
- `trades` (contract v19): each trade with its attributes, from the same
  feed, streamed for the listings under a standing want and pulled for a
  range; apart from the live prices (the plan's Q40), so an admin licenses
  and keeps them apart.

**The terms Alpaca's standard terms impose** (`licence_default`), which the
deployment's admin confirms or replaces on the Data sources page: a
non-professional subscriber receives market data "solely for their personal,
non-business use", nothing redistributed without Alpaca's written consent.
So each dataset's terms are one person's (`personal_use`), shown and used
by its holder (`display`, `derived_use`); the daily one kept, the live one
served and not kept, the trades kept two days (the plan's Q40: a reader that
lost some catches up from the lake). A summary, not legal advice; the README
says the same.

**The storage**, at the edge (decisions/028): Alpaca's raw responses, every
row's raw record, and the product map. The account side keeps three kinds
of raw record (kinds.py), each with its own window an admin of the plugin
sets: `responses` (each read of the account, its positions and its orders,
30 days), `activity` (from 0.8.0, custody's: each page of the account's
activities, seven years) and `trading` (each exchange with the Trading API
that places, replaces or cancels, seven years), all archivable. The retention is the
longest window, the data role's raw responses kept within it.
"""

from __future__ import annotations

from meridian import DatasetDeclaration, DatasetLicence

from .convert import DAY_END_MINUTE
from .kinds import KINDS
from .lake import DAILY, LIVE, LIVE_CADENCE_SECONDS, TRADES

VENDOR = "Alpaca"
#: How long a raw record is kept at the most: the longest kind's window.
RETENTION_DAYS = max(kind.window_days for kind in KINDS)
#: Alpaca's consolidated history reaches back to 2016.
HISTORY_DAYS = 3650
#: How long the lake keeps a trade by default: two days (the plan's Q40).
TRADES_KEPT_DAYS = 2

#: The platform's US equity venues (NYSE markets) calendar, read from the
#: platform on 2026-10-14 (the v24 plan's O10): the daily dataset's business
#: dates are the days it says US equity markets are open, so the lake flags a
#: close dated on a closed day (contract v24's `calendar_id`, which waited for
#: this plugin's move off the v23 pins).
US_EQUITY_CALENDAR = "CAL-01M4VF0F4XGP3RATCCSKDPZSBP"

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
        calendar_id=US_EQUITY_CALENDAR,
    ),
    DatasetDeclaration(
        key=LIVE,
        vendor=VENDOR,
        data_types=["meridian.v1.Price", "meridian.v1.Quote"],
        modes=["pull", "stream"],
        cadence=LIVE_CADENCE_SECONDS,
        licence_default=DatasetLicence(
            kept=False, derived_use=True, display=True, personal_use=True
        ),
        day_time_zone="America/New_York",
        day_end_minute=DAY_END_MINUTE,
    ),
    DatasetDeclaration(
        key=TRADES,
        vendor=VENDOR,
        data_types=["meridian.v1.Trade"],
        modes=["pull", "stream"],
        licence_default=DatasetLicence(
            kept=True,
            retention_days=TRADES_KEPT_DAYS,
            derived_use=True,
            display=True,
            personal_use=True,
        ),
        day_time_zone="America/New_York",
        day_end_minute=DAY_END_MINUTE,
    ),
)
