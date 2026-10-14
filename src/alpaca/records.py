"""What the pages' read tools answer (contract v12's parity: a page and its tool
read one source, `pages.answer`): the data role's common records
(`meridian.roles.data`, spec/plugin-pages-follow-their-role), each extended
with Alpaca's own fields beside the common ones. Configuration and the
plugin's own work only: never a price, which is the reading roles' to read
under their entitlement, and never a key, of which only whether each part
is set."""

from __future__ import annotations

from dataclasses import dataclass, field

from meridian.roles import data


@dataclass(frozen=True)
class FeedRead:
    """One of Alpaca's feeds, and whether the key answered for it."""

    feed: str
    what: str
    entitled: bool


@dataclass(frozen=True)
class SessionRead:
    """The stream: its state (idle, connecting, streaming, waiting, refused,
    no key), the feed, how many listings it streams of how many it may,
    the sessions opened and reopened, the messages heard, the trades caught
    up after a reconnect or a restart, and the last message and error.
    Never a key."""

    state: str
    feed: str = ""
    subscribed: int = 0
    limit: int | None = None
    opened: int = 0
    reconnects: int = 0
    messages: int = 0
    caught_up: int = 0
    since: str = ""
    last_message_at: str = ""
    last_error: str = ""


@dataclass(frozen=True)
class ConnectionRead(data.ConnectionRead):
    """Connection's common fields, then Alpaca's: what the mode means now,
    what the key is not entitled to, each feed and whether the key answered
    for it, the feed each dataset reads, the last call's HTTP status, and
    the stream's session."""

    detail: str = ""
    not_entitled: str = ""
    feeds: list[FeedRead] = field(default_factory=list)
    daily_feed: str = ""
    live_feed: str = ""
    last_status: int = 0
    stream: SessionRead | None = None


@dataclass(frozen=True)
class Served(data.Served):
    """One dataset as "Served" shows it, then Alpaca's: its key, the feed it
    reads, and what it restated, kept unchanged, could not convert, and
    caught up since the plugin started."""

    key: str = ""
    feed: str = ""
    restated: int = 0
    unchanged: int = 0
    unconverted: int = 0
    caught_up: int = 0


@dataclass(frozen=True)
class Want(data.Want):
    """One standing want as "Wants" shows it, then Alpaca's: its ID, the
    listings it names, and whether they are streamed."""

    want_id: str = ""
    listings: list[str] = field(default_factory=list)
    streamed: bool = False


@dataclass(frozen=True)
class DatasetsRead(data.DatasetsRead):
    """Datasets' common fields, then Alpaca's: the listings it knows, the
    days still forming, the listings streamed and those wanted past what
    one session may stream, and the subjects declined today because Alpaca
    has nothing for them (each once for the date, not each minute). Its
    rows are Alpaca's, each extending the role's."""

    served: list[Served] = field(default_factory=list)  # type: ignore[assignment]
    wants: list[Want] = field(default_factory=list)  # type: ignore[assignment]
    listings: int = 0
    forming: int = 0
    streamed: int = 0
    past_limit: int = 0
    declined_today: int = 0


# ── Connectivity's, from 0.7.0 ───────────────────────────────────────────


@dataclass(frozen=True)
class AccountTrading:
    """One account the key reaches, as connectivity trades it: its venue
    account code, whether trading is enabled on it (read-only until the
    connectivity role's admin enables it), the order types its catalogue
    offers, and whether its ems has mapped it on this instance."""

    venue_account_code: str
    trading: bool = False
    order_types: list[str] = field(default_factory=list)
    mapped: bool = False


@dataclass(frozen=True)
class TradingRead:
    """Trading, at Manage: whether the key is set (never its value), the
    environment the account is in (Paper, simulated money, or Live, said
    plainly, or sample data), each account and its trading, the placements
    working, held back by a pause or a stop, and of unknown outcome, the last
    read of the orders, the trade updates stream's state and the last error.
    Counts and codes only: no account's data."""

    credentials: list[data.Credential] = field(default_factory=list)
    environment: str = ""
    accounts: list[AccountTrading] = field(default_factory=list)
    working: int = 0
    held: int = 0
    unknown: int = 0
    last_poll: str = ""
    updates: str = ""
    error: str = ""


@dataclass(frozen=True)
class TradingAsked:
    """An account to enable trading on, by its venue account code."""

    venue_account_code: str


@dataclass(frozen=True)
class TradingEnabled:
    """What enabling trading came to, or would."""

    venue_account_code: str
    said: str = ""


# ── Custody's, from 0.8.0 ────────────────────────────────────────────────


@dataclass(frozen=True)
class LinkAsked:
    """A link asked for on Account links, in the kit's account map's fields
    (`intent`: link, create or unlink): the external account, and the
    account granted it is linked to, or a new account's name, custodian and
    type for a deployment admin to create and link in one step."""

    external_account_id: str
    intent: str = "link"
    account_id: str = ""
    new_account_name: str = ""
    new_account_custodian: str = ""
    new_account_type: str = ""
