"""What the pages' read tools answer (contract v12's parity: a page and its tool
read one source, `pages.answer`). Configuration and the plugin's own work
only: never a price, which is the reading roles' to read under their
entitlement, and never a key, of which only whether each part is set."""

from __future__ import annotations

from dataclasses import dataclass, field


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
    up after a reconnect, and the last message and error. Never a key."""

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
class ConnectionRead:
    """The plugin's connection to Alpaca: whether the key is set and answers,
    the feeds it is entitled to and which each dataset uses, the vendor's
    limits and what this process has spent of them, and its stream's
    session."""

    mode: str
    key_id_set: bool
    secret_key_set: bool
    answered: bool
    detail: str
    feeds: list[FeedRead] = field(default_factory=list)
    daily_feed: str = ""
    live_feed: str = ""
    limit_per_minute: int = 0
    vendor_limit_per_minute: int = 0
    calls: int = 0
    rate_limited: int = 0
    last_call_at: str = ""
    last_status: int = 0
    last_error: str = ""
    sessions: list[SessionRead] = field(default_factory=list)


@dataclass(frozen=True)
class DatasetRead:
    """One dataset the plugin serves, its catalogue entry and its work."""

    dataset: str
    key: str
    serves: str
    feed: str
    modes: list[str] = field(default_factory=list)
    cadence_seconds: int = 0
    kept_by_default: bool = False
    kept_days: int = 0
    personal_use: bool = True
    entitled: bool = False
    recorded: int = 0
    restated: int = 0
    unchanged: int = 0
    declined: int = 0
    unconverted: int = 0
    wants: int = 0
    withdrawn: int = 0
    caught_up: int = 0
    last_recorded_at: str = ""


@dataclass(frozen=True)
class StandingRead:
    """A standing want the plugin keeps current: what it asks (prices of
    some kinds, quotes or trades), its listings, whether they are streamed,
    and when its prices were last kept."""

    want_id: str
    dataset: str
    subjects: int
    symbols: list[str] = field(default_factory=list)
    kinds: list[str] = field(default_factory=list)
    asks: str = ""
    streamed: bool = False
    last_kept_at: str = ""


@dataclass(frozen=True)
class DatasetsRead:
    """What the plugin serves: each dataset, the standing wants it keeps
    current, how many listings it knows, the days still forming, and
    whether the deployment lets it read an instrument's record (W3.6), without
    which it serves only the listings it resolved itself; and how many
    listings are streamed, and how many more were wanted than one session
    may stream."""

    datasets: list[DatasetRead] = field(default_factory=list)
    standing: list[StandingRead] = field(default_factory=list)
    listings: int = 0
    forming: int = 0
    reads_instruments: bool = True
    streamed: int = 0
    past_limit: int = 0
