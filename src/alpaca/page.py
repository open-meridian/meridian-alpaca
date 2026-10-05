"""The plugin's one page: its connectivity, its datasets and its entitlements.

A `dgm` shows no market data (ruled 2026-10-03: "The data unit only cares
for connectivity and data subscriptions and entitlement resolution"). So
Manage (`admin`) sees whether Alpaca answers with the configured key --
whether each part of the key is set, never what it is -- which feeds the
key is entitled to, how the plugin spends its rate limit, and the datasets
it would serve, each with its feed and its licence's default terms; and no
price, bar, trade or quote. There is no page under Open or View: viewing
prices is for the reading roles.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import meridian

from . import convert, venue
from .settings import IEX, Config

TITLE = "Alpaca"

pages = meridian.Pages(TITLE, templates=Path(__file__).parent / "templates")


@dataclass
class Connectivity:
    """What the last probe of Alpaca found; the page shows it as it is."""

    state: str = "warn"
    detail: str = "Not asked yet."
    checked_at: str = ""
    key_id_set: bool = False
    secret_key_set: bool = False
    preferred: str = IEX
    entitlements: venue.Entitlements | None = None
    calls: int = 0
    rate_limited: int = 0
    raw_records: str = ""
    problems: list[str] = field(default_factory=list)

    def configured(self, config: Config) -> None:
        self.key_id_set, self.secret_key_set = config.key_id_set, config.secret_key_set
        self.preferred = config.feed


CONNECTIVITY = Connectivity()


def _yes(value: bool | None) -> str:
    return "not asked" if value is None else ("entitled" if value else "not entitled")


def datasets(status: Connectivity) -> list[dict[str, str]]:
    """Each dataset, the feed it is from, whether it is served, and its
    licence's default terms."""
    served = {
        convert.dataset(kind, venue.feed_for(kind, status.preferred, status.entitlements))
        for kind in ("daily", "live")
    }
    entitled = status.entitlements
    rows = []
    for dataset in convert.CATALOGUE:
        kind, _, feed = dataset.key.partition("-")
        if entitled is None or not entitled.answered:
            allowed = None
        elif feed == IEX:
            allowed = entitled.iex
        else:
            allowed = entitled.sip_history if kind == "daily" else entitled.sip_live
        rows.append(
            {
                "key": dataset.key,
                "vendor": dataset.vendor,
                "types": ", ".join(dataset.data_types),
                "day": f"{dataset.day.calendar}, ends {dataset.day.ends_at}" if dataset.day else "",
                "entitled": _yes(allowed),
                "served": "served" if dataset.key in served else "not served",
                "licence": convert.LICENCE_DEFAULT[kind],
            }
        )
    return rows


@pages.page("/", "Connectivity", levels="admin")
def connectivity(request: meridian.Request) -> str:
    identity = request.plugin.identity
    entitled = CONNECTIVITY.entitlements
    return pages.render(
        "connectivity.html",
        instance=identity.instance_id,
        roles=identity.roles,
        rest=venue.DATA,
        rate=f"at most {round(venue.RATE_PER_SECOND * 60)} calls a minute, one at a time",
        status=CONNECTIVITY,
        feeds=[
            ("IEX, real time", _yes(entitled.iex if entitled and entitled.answered else None)),
            (
                "SIP, past its latest 15 minutes",
                _yes(entitled.sip_history if entitled and entitled.answered else None),
            ),
            (
                "SIP, real time",
                _yes(entitled.sip_live if entitled and entitled.answered else None),
            ),
        ],
        datasets=datasets(CONNECTIVITY),
    )
