"""What the plugin needs from the deployment, declared as settings (W4.7, W6.11).

Alpaca's Market Data API takes an account's key: its key ID and its secret
key, which a free account issues (a paper account's key suffices) and which
do not expire. Both are **secret settings**: an admin of the plugin enters
them in the dashboard's settings form, and they are never read back,
displayed, logged, reported or kept. They reach this process in
`meridian.Settings` and nowhere else. `Credentials` keeps them out of its
repr; vendor.py sends them as Alpaca's two headers and keeps neither in any
raw record; the Connection page says whether each is set, never what it is.

The data role's common settings, in its order
(spec/plugin-pages-follow-their-role): no `environment`, since Alpaca's
market data has one (a paper account's key reads the same service); the two
credentials, in Alpaca's words; and `sample_data`, a developer's setting
(`meridian.sample_data`, shown only on a development deployment), serving
the built-in recorded exchange (synthetic.py) instead of calling Alpaca,
with no key: what the e2e and a first look use.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field

import meridian

KEY_ID = "alpaca_api_key_id"
SECRET_KEY = "alpaca_api_secret_key"
SAMPLE_DATA = "sample_data"


@meridian.settings
class Declared:
    """The settings as declared, in the data role's order: one typed class,
    the SDK's form, which `connect` and the declaration register."""

    alpaca_api_key_id: str = meridian.setting(
        "API key ID",
        required=True,
        secret=True,
        description=(
            "From Alpaca's dashboard, under API keys. A free account's key, or a paper "
            "account's, suffices; it does not expire."
        ),
    )
    alpaca_api_secret_key: str = meridian.setting(
        "API secret key",
        required=True,
        secret=True,
        description="Shown once, when Alpaca issues the key.",
    )
    sample_data: bool = meridian.sample_data()


DECLARED = Declared


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

    def secrets(self) -> tuple[str, ...]:
        """The values never to be kept anywhere, for raw records to look for."""
        return (self.key_id, self.secret_key)


@dataclass(frozen=True)
class Config:
    """The settings as the plugin uses them: the key, if both parts are
    given, which parts are set (never their values), and sample data."""

    credentials: Credentials | None = None
    key_id_set: bool = False
    secret_key_set: bool = False
    sample_data: bool = False

    @property
    def ready(self) -> bool:
        """Whether there is something to ask: sample data, or the whole key."""
        return self.sample_data or self.credentials is not None


def config_from(values: Mapping[str, object]) -> Config:
    """The settings as delivered, typed."""
    key_id, secret_key = str(values.get(KEY_ID) or ""), str(values.get(SECRET_KEY) or "")
    return Config(
        credentials=Credentials(key_id, secret_key) if key_id and secret_key else None,
        key_id_set=bool(key_id),
        secret_key_set=bool(secret_key),
        sample_data=values.get(SAMPLE_DATA) is True,
    )
