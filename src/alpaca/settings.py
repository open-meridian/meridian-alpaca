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

From 0.8.0 the plugin holds three roles (data, custody, connectivity), and
every setting names the roles it serves:

- `environment` first (`meridian.environment`): `live`, Alpaca's live
  Trading API (api.alpaca.markets), or `sandbox`, Alpaca's paper trading API
  (paper-api.alpaca.markets), the same API with simulated money. It decides
  which account custody reads and connectivity trades. Market data is one service whichever
  it is, and either account's key reads it.
- the key's two parts, for whichever environment is chosen: a live
  account's key on `live`, a paper account's on `sandbox`. One key, as
  before, so a value saved under 0.6.x carries over unchanged.
- custody's "Read every" and "Stale after" (`meridian.roles.custody`), from
  0.8.0.
- `sample_data`, a developer's setting (`meridian.sample_data`, shown only
  on a development deployment): the built-in recorded exchange for market
  data and a simulated brokerage for the account and its orders
  (synthetic.py, synthetic_broker.py), with no key and nothing sent to Alpaca. The e2e and
  a first look use it.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import timedelta

import meridian
from meridian.roles import custody

ENVIRONMENT = "environment"
LIVE, SANDBOX = "live", "sandbox"
KEY_ID = "alpaca_api_key_id"
SECRET_KEY = "alpaca_api_secret_key"
POLL_SECONDS = "poll_seconds"
STALE_AFTER_HOURS = "stale_after_hours"
SAMPLE_DATA = "sample_data"

#: The roles a setting serves: the key and the environment are every role's.
EVERY_ROLE = ("data", "custody", "connectivity")
CUSTODY_ONLY = ("custody",)

DEFAULT_POLL_SECONDS = 300
LEAST_POLL_SECONDS = 60
DEFAULT_STALE_AFTER_HOURS = 36

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
    poll_seconds: int = custody.poll_seconds(
        roles=CUSTODY_ONLY,
        default=DEFAULT_POLL_SECONDS,
        description=f"How often to read the account at Alpaca. At least {LEAST_POLL_SECONDS}.",
    )
    stale_after_hours: int = custody.stale_after_hours(
        roles=CUSTODY_ONLY,
        default=DEFAULT_STALE_AFTER_HOURS,
        description=(
            "How old the last read Alpaca answered may be, while it does not answer, before "
            "the account is reported stale."
        ),
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
    parts are given, which parts are set (never their values), custody's
    cadence, and sample data."""

    credentials: Credentials | None = None
    key_id_set: bool = False
    secret_key_set: bool = False
    sample_data: bool = False
    environment: str = LIVE
    poll_seconds: int = DEFAULT_POLL_SECONDS
    stale_after: timedelta = timedelta(hours=DEFAULT_STALE_AFTER_HOURS)

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
    poll, stale = values.get(POLL_SECONDS), values.get(STALE_AFTER_HOURS)
    return Config(
        credentials=Credentials(key_id, secret_key) if key_id and secret_key else None,
        key_id_set=bool(key_id),
        secret_key_set=bool(secret_key),
        sample_data=values.get(SAMPLE_DATA) is True,
        environment=SANDBOX if values.get(ENVIRONMENT) == SANDBOX else LIVE,
        poll_seconds=(
            max(poll, LEAST_POLL_SECONDS)
            if isinstance(poll, int) and not isinstance(poll, bool)
            else DEFAULT_POLL_SECONDS
        ),
        stale_after=timedelta(
            hours=(
                stale
                if isinstance(stale, int) and not isinstance(stale, bool) and stale > 0
                else DEFAULT_STALE_AFTER_HOURS
            )
        ),
    )
