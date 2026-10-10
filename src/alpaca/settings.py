"""What the plugin needs from the deployment, declared as settings (W4.7, W6.11).

Alpaca's Market Data API takes an account's key: its key ID and its secret
key, which a free account issues (a paper account's key suffices) and which
do not expire. Both are **secret settings**: an admin of the plugin enters
them in the dashboard's settings form, and they are never read back,
displayed, logged, reported or kept. They reach this process in
`meridian.Settings` and nowhere else. `Credentials` keeps them out of its
repr; vendor.py sends them as Alpaca's two headers and keeps neither in any
raw record; the Connection page says whether each is set, never what it is.

`synthetic`, a developer's setting (shown only on a development deployment),
serves the built-in synthetic exchange (synthetic.py) instead of calling
Alpaca, with no key: what the e2e and a first look use.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field

import meridian

KEY_ID = "alpaca_api_key_id"
SECRET_KEY = "alpaca_api_secret_key"
SYNTHETIC = "synthetic"

DECLARED: tuple[meridian.Setting, ...] = (
    meridian.Setting(
        KEY_ID,
        str,
        required=True,
        secret=True,
        label="API key ID",
        description=(
            "From Alpaca's dashboard, under API keys. A free account's key, or a paper "
            "account's, suffices; it does not expire."
        ),
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
        SYNTHETIC,
        bool,
        default=False,
        developer=True,
        label="Synthetic",
        description=(
            "Serve the built-in synthetic Alpaca responses instead of calling Alpaca, "
            "with no key. For development and tests only."
        ),
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

    def secrets(self) -> tuple[str, ...]:
        """The values never to be kept anywhere, for raw records to look for."""
        return (self.key_id, self.secret_key)


@dataclass(frozen=True)
class Config:
    """The settings as the plugin uses them: the key, if both parts are
    given, which parts are set (never their values), and synthetic mode."""

    credentials: Credentials | None = None
    key_id_set: bool = False
    secret_key_set: bool = False
    synthetic: bool = False

    @property
    def ready(self) -> bool:
        """Whether there is something to ask: synthetic, or the whole key."""
        return self.synthetic or self.credentials is not None


def config_from(values: Mapping[str, object]) -> Config:
    """The settings as delivered, typed."""
    key_id, secret_key = str(values.get(KEY_ID) or ""), str(values.get(SECRET_KEY) or "")
    return Config(
        credentials=Credentials(key_id, secret_key) if key_id and secret_key else None,
        key_id_set=bool(key_id),
        secret_key_set=bool(secret_key),
        synthetic=values.get(SYNTHETIC) is True,
    )
