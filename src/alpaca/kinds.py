"""The kinds of raw record connectivity keeps, and their windows (contract
v16; meridian-design spec/an-edge-plugins-older-records-move-to-the-
archive), from 0.7.0.

Connectivity keeps what Alpaca's Trading API said, as its bytes arrived,
before anything is converted from it (decisions/028), in two kinds, each in
units the plugin can find again:

- `responses` ("Raw responses"): each read of the account and its orders,
  in a unit per UTC day (`responses/2026-10-13/`); 30 days by default.
- `trading` ("Trading", contract v22): each exchange with the Trading API
  that places, replaces or cancels an order, every trade update heard, every
  rejection this plugin made with nothing sent, and each account trading was
  enabled on, in a unit per month (`trading/2026-10/`); seven years by
  default (2555 days), as SnapTrade's.

A record's key is its path, `<kind>/<unit>/<name>`, which a row's
`RawRecordRef` names, then `#` and where in the record it points.

Past its kind's window a unit is archived, kept or deleted as the kind's
`<kind>_past_window` setting says, through the SDK (`tend`), which records
each move before anything is removed; a unit still being written (today's,
this month's) is never moved. What storage holds of each kind is said on
every heartbeat (`plugin.stored`). The data role's raw responses stay where
0.6.0 kept them (raw.py), apart from these.

Where the records live: `meridian.edge.storage_dir()` where the deployment
grants storage; a temporary directory otherwise, which a restart loses.
"""

from __future__ import annotations

import json
import logging
import re
import tempfile
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

import meridian
from meridian import RecordKind, edge
from meridian.v1 import sidecar_pb2

log = logging.getLogger("alpaca.kinds")

RESPONSES, TRADING = "responses", "trading"
#: How long each kind stays in storage by default, in days.
DEFAULT_WINDOWS = {RESPONSES: 30, TRADING: 2555}
#: Which kinds are kept in a unit per month; the rest per day.
MONTHLY = frozenset({TRADING})
KINDS = (
    RecordKind(RESPONSES, "Raw responses", window_days=DEFAULT_WINDOWS[RESPONSES]),
    RecordKind(TRADING, "Trading", window_days=DEFAULT_WINDOWS[TRADING]),
)
#: What is done past a window: the SDK's three choices.
ARCHIVED, KEPT, DELETED = "archived", "kept", "deleted"

_UNSAFE = re.compile(r"[^A-Za-z0-9._:-]")
_DAY = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_MONTH = re.compile(r"^\d{4}-\d{2}$")


def root() -> Path:
    """The instance's granted storage, else a temporary directory."""
    granted = edge.storage_dir()
    return granted if granted is not None else Path(tempfile.mkdtemp(prefix="alpaca-kinds-"))


def unit_of(kind: str, at: datetime) -> str:
    """The unit a record received at `at` goes in: its UTC month or day."""
    moment = at.astimezone(UTC)
    return moment.strftime("%Y-%m") if kind in MONTHLY else moment.strftime("%Y-%m-%d")


@dataclass(frozen=True)
class Unit:
    """One unit in storage: its path, its kind, the day it ended, how many
    records it holds, and when the first and last were received."""

    path: str
    kind: str
    ends: date
    records: int
    first_ns: int
    last_ns: int


@dataclass(frozen=True)
class Window:
    days: int
    past: str


def windows_from(values: Mapping[str, object]) -> dict[str, Window]:
    """Each kind's window and what is done past it, from the settings the SDK
    declares for it (`<kind>_window_days`, `<kind>_past_window`); kept where
    the settings say nothing."""
    out: dict[str, Window] = {}
    for kind in KINDS:
        days = values.get(f"{kind.name}_window_days")
        past = values.get(f"{kind.name}_past_window")
        out[kind.name] = Window(
            days
            if isinstance(days, int) and not isinstance(days, bool) and days > 0
            else kind.window_days,
            past if past in (ARCHIVED, KEPT, DELETED) else KEPT,
        )
    return out


class Store:
    """The kinds' records as JSON files under one root, by key."""

    def __init__(self, base: Path) -> None:
        self.base = base

    def _path(self, key: str) -> Path:
        parts = [_UNSAFE.sub("_", part) or "_" for part in key.split("/")]
        return self.base.joinpath(*parts[:-1], parts[-1] + ".json")

    def keep(self, kind: str, name: str, record: dict[str, Any], at: datetime) -> str:
        """Keep one record of `kind`, received at `at`, and say its key; a key
        already kept is never overwritten."""
        if kind not in DEFAULT_WINDOWS:
            raise ValueError(f"no kind {kind!r}")
        key = f"{kind}/{unit_of(kind, at)}/{name}-{_ns(at)}"
        taken, n = key, 0
        record = {**record, "received_at_ns": _ns(at)}
        while True:
            path = self._path(taken)
            path.parent.mkdir(parents=True, exist_ok=True)
            try:
                with path.open("x", encoding="utf-8") as out:
                    json.dump(record, out, indent=1, sort_keys=True, default=str)
                return taken
            except FileExistsError:
                n += 1
                taken = f"{key}.{n}"

    def get(self, ref: str) -> dict[str, Any] | None:
        """The record a reference names (`#` and what follows set aside), or
        None where it is not in storage."""
        key = ref.partition("#")[0]
        path = self._path(key)
        if not path.exists():
            return None
        held: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
        return held

    def units(self, kind: str) -> list[Unit]:
        """Each unit of `kind` in storage, oldest first."""
        directory = self.base / kind
        if not directory.is_dir():
            return []
        found: list[Unit] = []
        pattern = _MONTH if kind in MONTHLY else _DAY
        for held in sorted(directory.iterdir()):
            if not held.is_dir() or not pattern.match(held.name):
                continue
            stamps: list[int] = []
            for path in held.rglob("*.json"):
                try:
                    stamps.append(int(json.loads(path.read_text("utf-8"))["received_at_ns"]))
                except (OSError, ValueError, KeyError, TypeError):
                    continue
            if not stamps:
                continue
            found.append(
                Unit(
                    f"{kind}/{held.name}",
                    kind,
                    _ends(kind, held.name),
                    len(stamps),
                    min(stamps),
                    max(stamps),
                )
            )
        return found


def _ends(kind: str, name: str) -> date:
    """The last day a unit takes records on."""
    if kind not in MONTHLY:
        return date.fromisoformat(name)
    first = date.fromisoformat(f"{name}-01")
    following = (first.replace(day=28) + timedelta(days=4)).replace(day=1)
    return following - timedelta(days=1)


def _ns(at: datetime) -> int:
    return int(at.timestamp()) * 1_000_000_000 + at.microsecond * 1000


async def tend(
    plugin: meridian.Plugin, store: Store, windows: Mapping[str, Window], today: date
) -> None:
    """Move each unit whose last day is past its kind's window as the setting
    says -- archived or deleted through the SDK, kept left -- never one still
    being written; then say what storage holds of each kind. A move refused
    (no archive, a deletion inside the deployment's hold) keeps the unit,
    and the next pass tries again."""
    spans: list[sidecar_pb2.StoredSpan] = []
    for kind in KINDS:
        window = windows.get(kind.name, Window(kind.window_days, KEPT))
        cutoff = today - timedelta(days=window.days)
        for unit in store.units(kind.name):
            if unit.ends >= cutoff or unit.ends >= today or window.past == KEPT:
                continue
            try:
                if window.past == ARCHIVED:
                    await plugin.archive_unit(
                        kind.name,
                        unit.path,
                        record_count=unit.records,
                        first_received_ns=unit.first_ns,
                        last_received_ns=unit.last_ns,
                    )
                else:
                    await plugin.delete_unit(
                        kind.name,
                        unit.path,
                        record_count=unit.records,
                        first_received_ns=unit.first_ns,
                        last_received_ns=unit.last_ns,
                    )
            except (meridian.MeridianError, ValueError, RuntimeError, OSError) as refused:
                log.warning("%s kept, its move refused: %s", unit.path, refused)
        held = store.units(kind.name)
        if held:
            spans.append(
                sidecar_pb2.StoredSpan(
                    record_kind=kind.name,
                    record_count=sum(u.records for u in held),
                    first_received_ns=min(u.first_ns for u in held),
                    last_received_ns=max(u.last_ns for u in held),
                )
            )
    plugin.stored = spans
