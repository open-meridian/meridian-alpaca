"""The plugin's pages, under Manage alone: Connection and Datasets, each a read
tool on the deployment's MCP surface answering what the page shows
(`read_connection`, with the stream's session, and `read_datasets`; the lake
plan's "Every new page's tools"). A `dgm` shows no market data: prices,
trades and quotes are the reading roles' to read, under their entitlement,
and an admin page shows no account's data.

Each page fits one screen at 1440x900 and 390x844: one-line rows, and the
standing wants, which grow, searched and paged (the kit's `search` on an
`om-pager`, since kit 0.11.0); without the kit, every row is there.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import meridian
from meridian.plugin.v1 import operations_pb2 as ops

from .convert import NEW_YORK
from .declaration import CATALOGUE
from .lake import DAILY, LIVE, PRICE, QUOTE, TRADES, Lake
from .records import (
    ConnectionRead,
    DatasetRead,
    DatasetsRead,
    FeedRead,
    SessionRead,
    StandingRead,
)
from .settings import Config
from .source import AlpacaSource
from .stream import Streamer
from .vendor import IEX, LIMIT_PER_MINUTE, NS, SIP, Alpaca, Usage

TITLE = "Alpaca"
CONNECTION = "/"
DATASETS = "/datasets"
#: Alpaca's own limit on the Basic plan, of which the client spends half.
VENDOR_LIMIT_PER_MINUTE = 200
#: The kit the pages are built on: 0.11.0's search beside the pager.
KIT = "0.11.0"

pages = meridian.Pages(TITLE, templates=Path(__file__).parent / "templates", kit=KIT)


@dataclass
class State:
    """What the pages read: the settings, the lake's half and the source."""

    config: Config = field(default_factory=Config)
    lake: Lake | None = None
    source: AlpacaSource | None = None
    alpaca: Alpaca | None = None
    streamer: Streamer | None = None


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


def _shown(at: str) -> str:
    """A moment as the page shows it: New York's time, to the minute."""
    if not at:
        return "never"
    moment = dt.datetime.fromisoformat(at.replace("Z", "+00:00")).astimezone(NEW_YORK)
    return moment.strftime("%b %d %H:%M ET")


def connection_read(state: State) -> ConnectionRead:
    config = state.config
    usage = state.alpaca.usage if state.alpaca is not None else Usage()
    known = state.source.known if state.source is not None else None
    if config.synthetic:
        mode, detail = "synthetic", "Serving the built-in synthetic exchange; Alpaca is not called."
    elif config.credentials is None:
        mode, detail = "no key", "Enter the API key ID and secret key in Settings."
    elif known is None:
        mode, detail = "live", "Not asked yet: Alpaca is asked when the key is saved."
    else:
        mode, detail = "live", known.detail or ("Answered." if known.answered else "")
    answered = bool(known and known.answered)
    feeds = [
        FeedRead("iex", "IEX in real time", bool(known and known.iex)),
        FeedRead(
            "sip", "Every US exchange (SIP), past 15 minutes", bool(known and known.sip_history)
        ),
        FeedRead("sip", "Every US exchange (SIP), in real time", bool(known and known.sip_live)),
    ]
    live_feed = known.live_feed if known is not None and not config.synthetic else IEX
    return ConnectionRead(
        mode=mode,
        key_id_set=config.key_id_set,
        secret_key_set=config.secret_key_set,
        answered=answered or config.synthetic,
        detail=detail,
        feeds=feeds,
        daily_feed=SIP,
        live_feed=live_feed,
        limit_per_minute=LIMIT_PER_MINUTE,
        vendor_limit_per_minute=VENDOR_LIMIT_PER_MINUTE,
        calls=usage.calls,
        rate_limited=usage.rate_limited,
        last_call_at=_when(usage.last_call_at_ns),
        last_status=usage.last_status,
        last_error=usage.last_error or (state.lake.last_error if state.lake else ""),
        sessions=[session_read(state)] if state.streamer is not None else [],
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


def datasets_read(state: State) -> DatasetsRead:
    lake = state.lake
    connection = connection_read(state)
    entitled = {f.what: f.entitled for f in connection.feeds}
    rows: list[DatasetRead] = []
    for declared in CATALOGUE:
        counts = lake.counts[declared.key] if lake is not None else None
        daily = declared.key == DAILY
        feed = connection.daily_feed if daily else connection.live_feed
        rows.append(
            DatasetRead(
                dataset=lake.dataset(declared.key) if lake is not None else declared.key,
                key=declared.key,
                serves=SERVES.get(declared.key, declared.key),
                feed="SIP (consolidated)" if feed == SIP else "IEX",
                modes=[_mode(m) for m in declared.modes],
                cadence_seconds=declared.cadence,
                kept_by_default=bool(declared.licence_default and declared.licence_default.kept),
                kept_days=declared.licence_default.retention_days
                if declared.licence_default
                else 0,
                personal_use=bool(
                    declared.licence_default and declared.licence_default.personal_use
                ),
                entitled=connection.mode == "synthetic"
                or (
                    entitled.get("Every US exchange (SIP), past 15 minutes", False)
                    if daily
                    else connection.answered
                ),
                recorded=counts.recorded if counts else 0,
                restated=counts.restated if counts else 0,
                unchanged=counts.unchanged if counts else 0,
                declined=counts.declined if counts else 0,
                unconverted=counts.unconverted if counts else 0,
                wants=counts.wants if counts else 0,
                withdrawn=counts.withdrawn if counts else 0,
                caught_up=counts.caught_up if counts else 0,
                last_recorded_at=_when(counts.last_recorded_at_ns) if counts else "",
            )
        )
    standing = []
    if lake is not None:
        streamed = set(lake.streamed)
        for want in lake.standing.values():
            symbols = [lake.products.symbols.get(s, s) for s in want.subjects]
            standing.append(
                StandingRead(
                    want_id=want.want_id,
                    dataset=lake.dataset(want.dataset),
                    subjects=len(want.subjects),
                    symbols=symbols,
                    kinds=list(want.kinds),
                    asks=_asks(want.data_type, want.kinds),
                    streamed=bool(symbols) and all(s in streamed for s in symbols),
                    last_kept_at=_when(want.last_at_ns) if want.data_type == PRICE else "",
                )
            )
    return DatasetsRead(
        datasets=rows,
        standing=standing,
        listings=len(lake.products.symbols) if lake is not None else 0,
        forming=len(lake.forming) if lake is not None else 0,
        reads_instruments=lake.reads_instruments if lake is not None else True,
        streamed=len(lake.streamed) if lake is not None else 0,
        past_limit=len(lake.past_limit) if lake is not None else 0,
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


def _stream_row(sessions: list[SessionRead]) -> dict[str, str]:
    """The stream, one line: its state, what it streams, and its reconnects."""
    if not sessions:
        return {"what": "Stream", "value": "Not started", "note": ""}
    held = sessions[0]
    limit = f" of {held.limit}" if held.limit is not None else ""
    if held.state == "streaming":
        value = (
            f"{held.subscribed}{limit} listings on {held.feed.upper()}, since {_shown(held.since)}"
        )
    elif held.state == "idle":
        value = "Idle: no standing want"
    else:
        value = held.state.capitalize()
    notes = [f"{held.reconnects} reconnects", f"{held.caught_up} trades caught up"]
    if held.last_error:
        notes.append(held.last_error)
    return {"what": "Stream", "value": value, "note": "; ".join(notes)}


def _dot(read: ConnectionRead) -> dict[str, str]:
    if read.mode == "synthetic":
        return {"state": "ok", "label": "Synthetic", "text": "Synthetic"}
    if read.mode == "no key":
        return {"state": "warn", "label": "No key", "text": "No key"}
    if not read.answered:
        return {"state": "error", "label": "Not answering", "text": "Not answering"}
    if read.last_error:
        return {"state": "warn", "label": "Errors", "text": "Errors"}
    return {"state": "ok", "label": "Connected", "text": "Connected"}


def _yes(flag: bool) -> dict[str, str]:
    return {"label": "Yes", "tone": "good"} if flag else {"label": "No", "tone": "warn"}


@pages.page(
    CONNECTION,
    "Connection",
    levels="admin",
    answers=ConnectionRead,
    name="read_connection",
    description=(
        "The plugin's connection to Alpaca: whether each part of the key is set (never "
        "its value) and whether Alpaca answers it, the feeds the key is entitled to and "
        "which each dataset uses, Alpaca's rate limit and what this process has spent of "
        "it, the last error, and the stream's session (its state, the listings streamed, "
        "reconnects and trades caught up). No market data."
    ),
)
async def connection(request: meridian.Request) -> meridian.Response:
    read = connection_read(_state)
    entitled = [f.what for f in read.feeds if f.entitled]
    refused = [f.what for f in read.feeds if not f.entitled]
    key_rows = [
        {"what": "Mode", "value": read.mode.capitalize(), "note": read.detail},
        {
            "what": "Key",
            "value": ", ".join(
                [
                    "ID " + ("set" if read.key_id_set else "not set"),
                    "secret " + ("set" if read.secret_key_set else "not set"),
                ]
            ),
            "note": "Secret settings: never shown",
        },
        {"what": "Entitled", "value": "; ".join(entitled) or "Nothing yet", "note": ""},
        {"what": "Not entitled", "value": "; ".join(refused) or "Nothing", "note": ""},
    ]
    use_rows = [
        {
            "what": "Rate limit",
            "value": f"{read.limit_per_minute} a minute",
            "note": f"half of Alpaca's {read.vendor_limit_per_minute} on the Basic plan",
        },
        {
            "what": "Calls",
            "value": f"{read.calls} since start",
            "note": f"{read.rate_limited} answered 429 and waited out",
        },
        {
            "what": "Last call",
            "value": _shown(read.last_call_at),
            "note": f"HTTP {read.last_status}" if read.last_status else "",
        },
        {"what": "Last error", "value": read.last_error or "None", "note": ""},
        _stream_row(read.sessions),
    ]
    return pages.answer(
        "connection.html",
        read,
        dot=_dot(read),
        key_rows=key_rows,
        use_rows=use_rows,
        feeds={"daily": read.daily_feed.upper(), "live": read.live_feed.upper()},
    )


@pages.page(
    DATASETS,
    "Datasets",
    levels="admin",
    answers=DatasetsRead,
    name="read_datasets",
    description=(
        "The datasets the plugin serves the lake: each one's catalogue entry (what it "
        "serves, from which feed, how rows arrive, its default terms), whether the key is "
        "entitled to it at Alpaca, and what it recorded, restated, withdrew and declined "
        "since the plugin started; the standing wants it keeps current and streams; the "
        "listings it has served. No prices, trades or quotes."
    ),
)
async def datasets(request: meridian.Request) -> meridian.Response:
    read = datasets_read(_state)
    grid_rows = [
        {
            "id": d.key,
            "dataset": d.dataset,
            "serves": d.serves,
            "feed": "SIP" if d.feed.startswith("SIP") else d.feed,
            "modes": ", ".join(d.modes),
            "terms": (
                (f"kept {d.kept_days} days" if d.kept_days else "kept")
                if d.kept_by_default
                else "served"
            )
            + ("; personal" if d.personal_use else ""),
            "entitled": "Yes" if d.entitled else "No",
            "entitled_tone": "good" if d.entitled else "warn",
            "recorded": str(d.recorded),
            "restated": str(d.restated),
            "declined": str(d.declined),
            "unconverted": str(d.unconverted),
            "last": _shown(d.last_recorded_at),
        }
        for d in read.datasets
    ]
    columns = [
        {"key": "dataset", "label": "Dataset", "strong": True, "priority": 1, "width": "7.5rem"},
        {"key": "feed", "label": "Feed", "priority": 3, "width": "4.5rem"},
        {"key": "modes", "label": "Arrives", "priority": 3, "width": "7rem"},
        {"key": "terms", "label": "Default terms", "priority": 3, "width": "9rem"},
        {
            "key": "entitled",
            "label": "Entitled",
            "type": "badge",
            "tone": {"field": "entitled_tone"},
            "priority": 1,
            "width": "6rem",
        },
        {"key": "recorded", "label": "Recorded", "type": "number", "priority": 1},
        {"key": "restated", "label": "Restated", "type": "number", "priority": 3},
        {"key": "declined", "label": "Declined", "type": "number", "priority": 2},
        {"key": "unconverted", "label": "Unconverted", "type": "number", "priority": 3},
        {"key": "last", "label": "Last recorded", "priority": 3, "width": "8rem"},
    ]
    standing_rows = [
        {
            "id": s.want_id,
            "symbols": ", ".join(s.symbols[:8])
            + (f" and {len(s.symbols) - 8} more" if len(s.symbols) > 8 else ""),
            "dataset": s.dataset.rpartition(":")[2],
            "asks": s.asks,
            "subjects": str(s.subjects),
            "streamed": "Yes" if s.streamed else "No",
            "streamed_tone": "good" if s.streamed else "warn",
            "kept": _shown(s.last_kept_at) if s.last_kept_at else "streamed",
        }
        for s in read.standing
    ]
    standing_columns = [
        {"key": "symbols", "label": "Listings", "strong": True, "priority": 1},
        {"key": "dataset", "label": "Dataset", "priority": 2, "width": "5rem"},
        {"key": "asks", "label": "Asks", "priority": 3},
        {"key": "subjects", "label": "Subjects", "type": "number", "priority": 3},
        {
            "key": "streamed",
            "label": "Streamed",
            "type": "badge",
            "tone": {"field": "streamed_tone"},
            "priority": 1,
            "width": "6rem",
        },
        {"key": "kept", "label": "Last kept", "priority": 3},
    ]
    return pages.answer(
        "datasets.html",
        read,
        grid={"columns": columns, "rows": grid_rows},
        grid_rows=grid_rows,
        standing_grid={"columns": standing_columns, "rows": standing_rows},
        standing_rows=standing_rows,
    )


def serve(plugin: meridian.Plugin, state: State, port: int) -> Any:
    """Serve the pages on loopback beside the sidecar, reading `state`."""
    use(state)
    return pages.serve(plugin, port)
