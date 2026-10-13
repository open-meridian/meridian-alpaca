"""What the plugin needs from the deployment, declared as settings (W4.7, W6.11).

Alpaca's APIs take an account's key: its key ID and its secret key. A live
account and a paper account each issue their own, and neither expires. Both
parts are **secret settings**: an admin of the plugin enters them in the
dashboard's settings form, and they are never read back, displayed, logged,
reported or kept. They reach this process in `meridian.Settings` and nowhere
else. `Credentials` keeps them out of its repr; vendor.py and broker.py send
them as Alpaca's two headers, the trade updates stream in its auth message,
and keep neither in any raw record; the pages say whether each is set, never
what it is.

From 0.7.0 the plugin holds two roles (data, connectivity), and every
setting names the roles it serves:

- `environment` first (`meridian.environment`): `live`, Alpaca's live
  Trading API (api.alpaca.markets), or `sandbox`, Alpaca's paper trading API
  (paper-api.alpaca.markets), the same API with simulated money. It decides
  which account connectivity trades. Market data is one service whichever
  it is, and either account's key reads it.
- the key's two parts, for whichever environment is chosen: a live
  account's key on `live`, a paper account's on `sandbox`. One key, as
  before, so a value saved under 0.6.x carries over unchanged.
- `sample_data`, a developer's setting (`meridian.sample_data`, shown only
  on a development deployment): the built-in recorded exchange for market
  data and a simulated brokerage for the account's orders (synthetic.py,
  synthetic_broker.py), with no key and nothing sent to Alpaca. The e2e and
  a first look use it.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field

import meridian

ENVIRONMENT = "environment"
LIVE, SANDBOX = "live", "sandbox"
KEY_ID = "alpaca_api_key_id"
SECRET_KEY = "alpaca_api_secret_key"
SAMPLE_DATA = "sample_data"

#: The roles each setting serves: both of them.
EVERY_ROLE = ("data", "connectivity")

#: What the plugin says of each environment, wherever it says which.
SAID = {
    LIVE: "Live: Alpaca's live Trading API",
    SANDBOX: "Paper: Alpaca's paper trading API, simulated money",
}


@meridian.settings
class Declared:
    """The settings as declared, in the roles' order: one typed class, the
    SDK's form, which `connect` and the declaration register."""

    environment: str = meridian.environment(
        description=(
            "Live: Alpaca's live Trading API, with a live account's key. Sandbox: Alpaca's "
            "paper trading API, simulated money, with a paper account's key. Market data "
            "reads the same either way."
        ),
        roles=EVERY_ROLE,
    )
    alpaca_api_key_id: str = meridian.setting(
        "API key ID",
        required=True,
        secret=True,
        roles=EVERY_ROLE,
        description=(
            "From Alpaca's dashboard, under API keys: the live account's for Live, the paper "
            "account's for Sandbox. It must match Environment."
        ),
    )
    alpaca_api_secret_key: str = meridian.setting(
        "API secret key",
        required=True,
        secret=True,
        roles=EVERY_ROLE,
        description="Shown once, when Alpaca issues the key.",
    )
    sample_data: bool = meridian.sample_data(
        roles=EVERY_ROLE,
        description=(
            "Answer from built-in made-up data instead of Alpaca, and trade with a simulated "
            "brokerage, changing nothing else the plugin does. Development deployments only."
        ),
    )


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
    """The settings as the plugin uses them: the environment, the key, if both
    parts are given, which parts are set (never their values), and sample
    data."""

    credentials: Credentials | None = None
    key_id_set: bool = False
    secret_key_set: bool = False
    sample_data: bool = False
    environment: str = LIVE

    @property
    def ready(self) -> bool:
        """Whether there is something to ask: sample data, or the whole key."""
        return self.sample_data or self.credentials is not None

    @property
    def paper(self) -> bool:
        """Whether the account is Alpaca's paper account (simulated money)."""
        return self.environment == SANDBOX


def config_from(values: Mapping[str, object]) -> Config:
    """The settings as delivered, typed."""
    key_id, secret_key = str(values.get(KEY_ID) or ""), str(values.get(SECRET_KEY) or "")
    return Config(
        credentials=Credentials(key_id, secret_key) if key_id and secret_key else None,
        key_id_set=bool(key_id),
        secret_key_set=bool(secret_key),
        sample_data=values.get(SAMPLE_DATA) is True,
        environment=SANDBOX if values.get(ENVIRONMENT) == SANDBOX else LIVE,
    )
