"""The plugin's pages, under Manage alone: the data role's common pages
(spec/plugin-pages-follow-their-role), Connection and Datasets, in its
order, each a read tool on the deployment's MCP surface answering what the
page shows under the role's names (`read_connection`, `read_datasets`).
Each record is the role's (`meridian.roles.data`), extended with Alpaca's
fields; each template is the role's, with Alpaca's nuance at its blocks
only: its feeds under "Key", its stream's session under "Stream", the feed
each dataset reads as a column of "Served", and a section of what it keeps
current. A data plugin shows no market data: prices, trades and quotes are
the reading roles' to read, under their entitlement, and an admin page shows
no account's data.

Each page fits one screen at 1440x900 and 390x844: one-line rows, the
details a click away, and the standing wants, which grow, drawn the latest
few with how many there are (the tool answers every one).
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

import meridian
from meridian.plugin.v1 import operations_pb2 as ops
from meridian.roles import data

from .convert import NEW_YORK
from .custody import Syncer
from .declared import CATALOGUE
from .kinds import Store
from .lake import DAILY, LIVE, PRICE, QUOTE, TRADES, Lake
from .records import (
    ConnectionRead,
    DatasetsRead,
    FeedRead,
    Served,
    SessionRead,
    Want,
)
from .settings import Config
from .source import AlpacaSource
from .stream import Streamer
from .trading import Trader
from .vendor import IEX, LIMIT_PER_MINUTE, NS, SIP, Alpaca, Usage

TITLE = "Alpaca"
CONNECTION = "/"
DATASETS = "/datasets"
#: Alpaca's own limit on the Basic plan, of which the client spends half.
VENDOR_LIMIT_PER_MINUTE = 200
#: The kit the pages are built on: 0.12.0's "Facts" and "Next step".
KIT = "0.12.0"
#: The standing wants a browser is shown, the latest; the tool answers all.
SHOWN_WANTS = 3

pages = meridian.Pages(TITLE, templates=Path(__file__).parent / "templates", kit=KIT)


def current() -> State:
    """The state the pages read now."""
    return _state


@dataclass
class State:
    """What the pages read: the settings, the lake's half and the source."""

    config: Config = field(default_factory=Config)
    lake: Lake | None = None
    source: AlpacaSource | None = None
    alpaca: Alpaca | None = None
    streamer: Streamer | None = None
    #: The datasets a deployment admin has licensed (contract v20), as read.
    licensed: frozenset[str] = frozenset()
    #: The account side: custody's reads (from 0.8.0), connectivity's
    #: trader and the trade updates (from 0.7.0), the kinds' store, the
    #: account scope with its links and its ems's map, and the settings as
    #: delivered (for each kind's window).
    syncer: Syncer | None = None
    trader: Trader | None = None
    updates: Any = None
    store: Store | None = None
    scope: meridian.AccountScope = field(default_factory=meridian.AccountScope)
    values: dict[str, object] = field(default_factory=dict)


_state = State()


def use(state: State) -> None:
    """The state the pages read from now on."""
    global _state
    _state = state


def _when(at_ns: int) -> str:
    if not at_ns:
        return ""
    moment = dt.datetime.fromtimestamp(at_ns / NS, dt.UTC)
    return moment.isoformat(timespec="seconds").replace("+00:00", "Z")


def shown(at: str) -> str:
    """A moment as the page shows it: New York's time, to the minute."""
    if not at:
        return ""
    moment = dt.datetime.fromisoformat(at.replace("Z", "+00:00")).astimezone(NEW_YORK)
    return moment.strftime("%b %d %H:%M ET")


pages.environment.filters["shown"] = shown

#: The feeds, in Alpaca's words, as the Key section names them.
FEEDS = (
    ("iex", "IEX in real time"),
    ("sip", "every US exchange (SIP), past 15 minutes"),
    ("sip", "every US exchange (SIP), in real time"),
)


def connection_read(state: State) -> ConnectionRead:
    config = state.config
    usage = state.alpaca.usage if state.alpaca is not None else Usage()
    known = state.source.known if state.source is not None else None
    if config.sample_data:
        mode, detail = "Sample data", "Recorded responses; Alpaca is not called."
    elif config.credentials is None:
        mode, detail = "No key", "Enter the API key ID and secret key in Settings."
    elif known is None:
        mode, detail = "Live", "Not asked yet: Alpaca is asked when the key is saved."
    elif not known.answered:
        mode, detail = "Live", f"Alpaca refused the key: {known.detail}"
    else:
        mode, detail = "Live", "Alpaca answers the key."
    has = (
        (True, True, False)
        if config.sample_data
        else (
            (bool(known.iex), bool(known.sip_history), bool(known.sip_live))
            if known is not None
            else (False, False, False)
        )
    )
    feeds = [FeedRead(feed, what, held) for (feed, what), held in zip(FEEDS, has, strict=True)]
    asked = config.sample_data or (known is not None and known.answered)
    entitled = "; ".join(f.what for f in feeds if f.entitled)
    refused = "; ".join(f.what for f in feeds if not f.entitled)
    live_feed = known.live_feed if known is not None and not config.sample_data else IEX
    stream = session_read(state) if state.streamer is not None else None
    return ConnectionRead(
        mode=mode,
        credentials=[
            data.Credential("API key ID", config.key_id_set),
            data.Credential("API secret key", config.secret_key_set),
        ],
        entitled_at_vendor=(entitled[:1].upper() + entitled[1:]) if asked else "",
        limit=(
            f"{LIMIT_PER_MINUTE} a minute, half of Alpaca's {VENDOR_LIMIT_PER_MINUTE} "
            "on the Basic plan"
        ),
        calls=usage.calls,
        rate_limited=usage.rate_limited,
        last_call_at=_when(usage.last_call_at_ns),
        last_error=(
            usage.last_error
            or (state.lake.last_error if state.lake else "")
            or (stream.last_error if stream is not None else "")
        ),
        sessions=(1 if stream is not None and stream.state == "streaming" else 0),
        detail=detail,
        not_entitled=(refused[:1].upper() + refused[1:]) if asked else "",
        feeds=feeds,
        daily_feed=SIP,
        live_feed=live_feed,
        last_status=usage.last_status,
        stream=stream,
    )


def session_read(state: State) -> SessionRead:
    assert state.streamer is not None
    held = state.streamer.session
    return SessionRead(
        state=held.state,
        feed=held.feed,
        subscribed=held.subscribed,
        limit=state.lake.stream_limit if state.lake is not None else None,
        opened=held.opened,
        reconnects=held.reconnects,
        messages=held.messages,
        caught_up=held.caught_up,
        since=_when(held.since_ns),
        last_message_at=_when(held.last_message_at_ns),
        last_error=held.last_error,
    )


SERVES = {
    DAILY: "closes and daily bars",
    LIVE: "last, bid and ask; quotes",
    TRADES: "trades",
}


def _terms(declared: Any) -> str:
    licence = declared.licence_default
    if licence is None or not licence.kept:
        kept = "served, not kept"
    else:
        kept = f"kept {licence.retention_days} days" if licence.retention_days else "kept"
    return kept + ("; personal use" if licence is not None and licence.personal_use else "")


def datasets_read(state: State) -> DatasetsRead:
    lake = state.lake
    connection = connection_read(state)
    entitled = {f.what: f.entitled for f in connection.feeds}
    served: list[Served] = []
    for declared in CATALOGUE:
        counts = lake.counts[declared.key] if lake is not None else None
        daily = declared.key == DAILY
        feed = connection.daily_feed if daily else connection.live_feed
        if connection.mode == "Sample data":
            at_vendor = "Yes"
        elif connection.mode == "No key" or not connection.entitled_at_vendor:
            at_vendor = "Not asked yet"
        elif daily:
            ok = entitled.get(FEEDS[1][1], False)
            at_vendor = "Yes" if ok else "No: SIP's history"
        else:
            at_vendor = "Yes, from IEX" if feed == IEX else "Yes, from SIP"
        dataset = lake.dataset(declared.key) if lake is not None else declared.key
        served.append(
            Served(
                dataset=dataset,
                data=SERVES.get(declared.key, declared.key),
                arrives=", ".join(_mode(m) for m in declared.modes),
                day="New York's, to 16:00",
                default_terms=_terms(declared),
                entitled_at_vendor=at_vendor,
                recorded=counts.recorded if counts else 0,
                declined=counts.declined if counts else 0,
                last_recorded=_when(counts.last_recorded_at_ns) if counts else "",
                licensed=dataset in state.licensed,
                key=declared.key,
                feed="SIP" if feed == SIP else "IEX",
                restated=counts.restated if counts else 0,
                unchanged=counts.unchanged if counts else 0,
                unconverted=counts.unconverted if counts else 0,
                caught_up=counts.caught_up if counts else 0,
            )
        )
    wants: list[Want] = []
    if lake is not None:
        streamed = set(lake.streamed)
        for standing in reversed(list(lake.standing.values())):
            listings = [lake.products.symbols.get(s, s) for s in standing.subjects]
            on_stream = bool(listings) and all(s in streamed for s in listings)
            answered = lake.by_want.get(standing.want_id, [0, 0])
            if standing.data_type == PRICE and standing.dataset == LIVE:
                state_word = f"Kept current, {shown(_when(standing.last_at_ns))}"
            else:
                state_word = "Streamed" if on_stream else "Past the stream's limit"
            wants.append(
                Want(
                    dataset=lake.dataset(standing.dataset),
                    asked=_asks(standing.data_type, standing.kinds),
                    subjects=len(standing.subjects),
                    recorded=answered[0],
                    declined=answered[1],
                    state=state_word,
                    want_id=standing.want_id,
                    listings=listings,
                    streamed=on_stream,
                )
            )
    today = lake.today() if lake is not None else ""
    return DatasetsRead(
        served=served,
        wants=wants,
        reads_instruments=lake.reads_instruments if lake is not None else True,
        listings=len(lake.products.symbols) if lake is not None else 0,
        forming=len(lake.forming) if lake is not None else 0,
        streamed=len(lake.streamed) if lake is not None else 0,
        past_limit=len(lake.past_limit) if lake is not None else 0,
        declined_today=(
            len({k[1] for k in lake.declined if k[2] == today}) if lake is not None else 0
        ),
    )


def _asks(data_type: str, kinds: tuple[str, ...]) -> str:
    if data_type == PRICE:
        return ", ".join(kinds) or "prices"
    return "quotes" if data_type == QUOTE else "trades"


def _mode(mode: object) -> str:
    """A catalogue's mode as the word the SDK sends (`pull`)."""
    if isinstance(mode, int):
        return str(ops.ObservationMode.Name(mode)).removeprefix("OBSERVATION_MODE_").lower()
    return str(mode).removeprefix("OBSERVATION_MODE_").lower()


def status(read: ConnectionRead) -> dict[str, str]:
    """The head's status, the role's Connection words."""
    if read.mode == "Sample data":
        return {"state": "ok", "label": "Sample data"}
    if read.mode == "No key":
        return {"state": "warn", "label": "No key"}
    if not read.entitled_at_vendor:
        return {"state": "error", "label": "Not answering"}
    return {"state": "warn" if read.last_error else "ok", "label": "Answering"}


def figures(state: State) -> list[meridian.Figure]:
    """The data role's figures, in its order (`meridian.roles.data`); Alpaca
    reports none of its own."""
    read = connection_read(state)
    word = status(read)["label"]
    connection: Any = word
    if word == "Not answering":
        connection = meridian.Figure("", word, state="error", why=read.detail[:200])
    elif word == "Answering" and read.last_error:
        connection = meridian.Figure("", word, state="warn", why=read.last_error[:200])
    served = datasets_read(state).served
    ok = [s for s in served if s.entitled_at_vendor.startswith("Yes")]
    entitled: Any = None
    if read.mode != "No key":
        missing = [s.key for s in served if s not in ok]
        entitled = meridian.Figure(
            "",
            f"{len(ok)} of {len(served)} datasets",
            state="warn" if missing else None,
            why=("Not served: " + ", ".join(missing)) if missing else "",
        )
    stream = read.stream
    if stream is None or stream.state in ("idle", ""):
        streaming: Any = "Off"
    elif stream.state == "streaming":
        limit = f" of {stream.limit}" if stream.limit is not None else ""
        streaming = f"{stream.subscribed}{limit} listings"
    else:
        streaming = meridian.Figure(
            "", stream.state.capitalize(), state="warn", why=stream.last_error[:200]
        )
    last = max((s.last_recorded for s in served), default="")
    used = state.alpaca.usage if state.alpaca is not None else Usage()
    now = state.alpaca.clock_ns() if state.alpaca is not None else 0
    spent = used.last_minute(now) if now else 0
    return data.figures(
        connection=connection,
        entitled_at_vendor=entitled,
        stream=streaming,
        last_recorded=(dt.datetime.fromisoformat(last.replace("Z", "+00:00")) if last else None),
        rate_use=meridian.Figure(
            "",
            f"{spent} of {LIMIT_PER_MINUTE} a minute",
            state="warn" if spent * 5 >= LIMIT_PER_MINUTE * 4 else None,
            why=f"{used.rate_limited} answered 429 and waited out" if used.rate_limited else "",
        ),
    )


@pages.page(
    CONNECTION,
    "Connection",
    levels="admin",
    roles="data",
    answers=ConnectionRead,
    name="read_connection",
    description=(
        "The plugin's connection to Alpaca, as every data plugin's Connection answers it: "
        "the mode (Live, Sample data or No key), each credential and whether it is set "
        "(never its value), what Alpaca serves the key, the limit and this process's "
        "calls, the last call and error, and the streaming sessions; then Alpaca's own: "
        "the feeds and which each dataset reads, and the stream's session (its state, "
        "listings, reconnects and trades caught up). No market data."
    ),
)
async def connection(request: meridian.Request) -> meridian.Response:
    read = connection_read(_state)
    return pages.answer("connection.html", read, head=status(read))


@pages.page(
    DATASETS,
    "Datasets",
    levels="admin",
    roles="data",
    answers=DatasetsRead,
    name="read_datasets",
    description=(
        "The datasets the plugin serves the lake, as every data plugin's Datasets answers "
        "them: each one as its catalogue declares it, whether a deployment admin has "
        "licensed it, whether Alpaca serves the key it, and what it recorded and declined "
        "since the plugin started; the standing wants it keeps current; whether it may "
        "read an instrument's record. Then Alpaca's own: the feed each dataset reads, the "
        "listings known and streamed, and the subjects declined today. No prices, trades "
        "or quotes."
    ),
)
async def datasets(request: meridian.Request) -> meridian.Response:
    read = datasets_read(_state)
    # A tool answers every want; a browser draws the latest few, and says
    # how many there are.
    drawn = read if request.tool_name else replace(read, wants=read.wants[:SHOWN_WANTS])
    return pages.answer("datasets.html", drawn, wants=len(read.wants))


# The account side's pages, declared after the data role's: connectivity's,
# then custody's.
from . import custody_pages, trading_page  # noqa: E402,F401,I001


def serve(plugin: meridian.Plugin, state: State, port: int) -> Any:
    """Serve the pages on loopback beside the sidecar, reading `state`."""
    use(state)
    return pages.serve(plugin, port)
