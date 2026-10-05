"""What the plugin needs from the deployment, declared as settings (W4.7).

Alpaca's Market Data API takes an account's key: its key ID and its secret
key, which a free account (a paper one suffices) issues and which do not
expire. Both are **secret settings**: an admin of the plugin gives them in
the dashboard's settings form, and they are never read back, displayed,
logged, reported or bundled. They reach this process in `meridian.Settings`
and nowhere else. `Credentials` keeps them out of its repr; venue.py sends
them as Alpaca's two headers and keeps neither in any raw record; the
Connectivity page says whether each is set, never what it is.

The developer's commands (`make sample`, `make live`) take the same two
values from the environment, as Alpaca's own tools name them
(`APCA_API_KEY_ID`, `APCA_API_SECRET_KEY`), read in tests/keys.py and
handed in: nothing in this package reads the environment.

`feed` says which of Alpaca's feeds to serve: IEX, the free one, by
default; or SIP, every US exchange, used for a dataset only where the
account is entitled to it (venue.Entitlements), IEX otherwise, and the page
says which.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field

import meridian

KEY_ID = "alpaca_api_key_id"
SECRET_KEY = "alpaca_api_secret_key"
FEED = "feed"

IEX = "iex"
SIP = "sip"
FEEDS = (IEX, SIP)

DECLARED: tuple[meridian.Setting, ...] = (
    meridian.Setting(
        KEY_ID,
        str,
        required=True,
        secret=True,
        label="API key ID",
        description="From Alpaca's dashboard, under API keys. A paper account's key suffices.",
    ),
    meridian.Setting(
        SECRET_KEY,
        str,
        required=True,
        secret=True,
        label="API secret key",
        description="Shown once, when Alpaca issues the key.",
    ),
    meridian.Setting(
        FEED,
        str,
        label="Feed",
        default=IEX,
        choices=(
            meridian.Choice(IEX, "IEX", "Free with every Alpaca account: one exchange, IEX."),
            meridian.Choice(
                SIP,
                "SIP, where entitled",
                "Every US exchange, consolidated. Used for each dataset only where the "
                "account is entitled to it; IEX otherwise.",
            ),
        ),
        description="Which of Alpaca's feeds the datasets are served from.",
    ),
)


@dataclass(frozen=True)
class Credentials:
    """What Alpaca is called with. Never in a repr, a log line, a page or a
    raw record."""

    key_id: str = field(repr=False)
    secret_key: str = field(repr=False)

    def __post_init__(self) -> None:
        if not self.key_id or not self.secret_key:
            raise ValueError("both parts of an Alpaca key are needed")

    def headers(self) -> dict[str, str]:
        """Alpaca's authentication headers, for the request alone."""
        return {"APCA-API-KEY-ID": self.key_id, "APCA-API-SECRET-KEY": self.secret_key}

    def secrets(self) -> tuple[str, str]:
        """The values never to be kept anywhere, for raw records to look for."""
        return (self.key_id, self.secret_key)


@dataclass(frozen=True)
class Config:
    """The settings as the plugin uses them: the key, if both parts are
    given, which parts are set (never their values), and the feed."""

    credentials: Credentials | None
    key_id_set: bool
    secret_key_set: bool
    feed: str = IEX


def config_from(values: Mapping[str, str | int | bool]) -> Config:
    """The settings as delivered, typed; an unknown feed is IEX's."""
    key_id, secret_key = str(values.get(KEY_ID) or ""), str(values.get(SECRET_KEY) or "")
    feed = str(values.get(FEED) or IEX)
    return Config(
        credentials=Credentials(key_id, secret_key) if key_id and secret_key else None,
        key_id_set=bool(key_id),
        secret_key_set=bool(secret_key),
        feed=feed if feed in FEEDS else IEX,
    )
