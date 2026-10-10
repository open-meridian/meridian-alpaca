"""Alpaca's raw responses, kept as the plugin's raw records (decisions/028).

Every response Alpaca sends -- a refusal, a rate limit and a forbidden feed
as much as a bar -- is kept before anything is converted from it, exactly as
its bytes arrived, with the request that asked for it (its URL, and its
headers less Alpaca's two authentication headers, which are never kept) and
the moment it was received. A row converted from it carries a
`RawRecordRef` whose key is the record's key, then `#` and where in the
record the row came from (`#SPY/0`, a symbol's first bar; `#SPY/bid`, a
quote's side), so a person auditing a row follows it here and finds exactly
what Alpaca said.

Where the records live: `meridian.edge.storage_dir()` when the deployment
grants the instance storage (a `dgm` is an edge role); a temporary directory
in the tests; `recordings/`, git-ignored, for `make live`, the account
holder's live check (real recordings stay theirs: the lake plan's Q11). No
other plugin reads them.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import meridian.edge

_UNSAFE = re.compile(r"[^A-Za-z0-9._-]")


def storage_root(fallback: Path) -> Path:
    """Where raw records go: the instance's granted storage, else `fallback`."""
    granted = meridian.edge.storage_dir()
    return (granted / "raw") if granted is not None else fallback


class RawStore:
    """Raw records as JSON files under one directory, by key."""

    def __init__(self, root: Path) -> None:
        self.root = root

    def _path(self, key: str) -> Path:
        parts = [_UNSAFE.sub("_", part) or "_" for part in key.split("/")]
        return self.root.joinpath(*parts).with_suffix(".json")

    def keep(self, key: str, record: dict[str, Any]) -> str:
        """Keep one record under `key`, and say the key it was kept under: a key
        already kept is never overwritten."""
        taken, n = key, 0
        while True:
            path = self._path(taken)
            path.parent.mkdir(parents=True, exist_ok=True)
            try:
                with path.open("x", encoding="utf-8") as out:
                    json.dump(record, out, indent=1, sort_keys=True)
                return taken
            except FileExistsError:
                # Another process kept one under the same moment: never
                # overwritten, the next free key instead.
                n += 1
                taken = f"{key}.{n}"

    def append(self, key: str, line: dict[str, Any]) -> int:
        """Add one message to a stream's record; its index in the record."""
        path = self._path(key).with_suffix(".jsonl")
        path.parent.mkdir(parents=True, exist_ok=True)
        index = sum(1 for _ in path.open(encoding="utf-8")) if path.exists() else 0
        with path.open("a", encoding="utf-8") as out:
            out.write(json.dumps(line, sort_keys=True) + "\n")
        return index

    def get(self, ref_key: str) -> dict[str, Any] | None:
        """The record a row's reference names, and where in it: for a stream,
        the one message; else the whole record. None once it is gone."""
        key, _, where = ref_key.partition("#")
        path = self._path(key)
        if path.exists():
            record: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
            return record
        stream = path.with_suffix(".jsonl")
        if stream.exists() and where.isdigit():
            lines = stream.read_text(encoding="utf-8").splitlines()
            if int(where) < len(lines):
                message: dict[str, Any] = json.loads(lines[int(where)])
                return message
        return None

    def records(self) -> Iterator[str]:
        for path in sorted(self.root.rglob("*.json*")):
            yield str(path.relative_to(self.root).with_suffix(""))


class StreamLog:
    """One stream session's messages, kept as a raw record a line each as they
    arrive (the record `RawStore.get` reads by `#index`). The file is opened
    once and the lines counted once, so keeping a message costs one write."""

    def __init__(self, store: RawStore, key: str) -> None:
        self.store = store
        self.key = key
        self.path = store._path(key).with_suffix(".jsonl")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.count = sum(1 for _ in self.path.open(encoding="utf-8")) if self.path.exists() else 0
        self._out = self.path.open("a", encoding="utf-8")

    def keep(self, line: dict[str, Any]) -> str:
        """Keep one message; the key a row converted from it names."""
        self._out.write(json.dumps(line, sort_keys=True) + "\n")
        self._out.flush()
        index = self.count
        self.count += 1
        return f"{self.key}#{index}"

    def close(self) -> None:
        self._out.close()
