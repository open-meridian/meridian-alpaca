"""Alpaca's US equity and ETF market data as a `dgm`, a connectivity spike.

It connects to its sidecar declaring its one page, its settings (the key's
two parts, secret, and the feed) and its storage. On each delivery of its
settings it asks Alpaca once which feeds the key is entitled to (each
response kept as a raw record in the storage the deployment grants), reports
what it found as its health, and serves the Connectivity page until stopped.
Without a key it asks nothing and says which part is missing.

It records nothing: the lake's contract does not exist yet, so there is no
operation to record a price with. rows.py is the seam where the lake's types
replace the spike's.

`make sample` and `make live` are the developer's, with a key from the
environment (tests/keys.py); this module never reads the environment for a
key.
"""

from __future__ import annotations

import asyncio
import logging
import os
import signal
import tempfile
import time
from pathlib import Path

import meridian

from .declaration import DECLARATION
from .page import CONNECTIVITY, TITLE, pages
from .raw import RawStore, storage_root
from .settings import DECLARED, Config, config_from
from .venue import Alpaca, NoKey, VenueError

log = logging.getLogger("alpaca")


def probe(client: Alpaca, config: Config) -> None:
    """Ask Alpaca once which feeds the key is entitled to, and say what it found."""
    CONNECTIVITY.configured(config)
    CONNECTIVITY.checked_at = time.strftime("%Y-%m-%d %H:%M:%S UTC", time.gmtime())
    CONNECTIVITY.raw_records = str(client.store.root)
    try:
        entitled = client.entitlements()
    except NoKey:
        missing = [
            label
            for label, given in (
                ("API key ID", config.key_id_set),
                ("API secret key", config.secret_key_set),
            )
            if not given
        ]
        CONNECTIVITY.state = "warn"
        CONNECTIVITY.detail = "Waiting for the " + " and the ".join(missing) + "."
        CONNECTIVITY.entitlements, CONNECTIVITY.problems = None, []
    except (VenueError, OSError) as failed:
        CONNECTIVITY.state, CONNECTIVITY.detail = "bad", "Alpaca did not answer."
        CONNECTIVITY.entitlements, CONNECTIVITY.problems = None, [str(failed)]
    else:
        CONNECTIVITY.entitlements = entitled
        if entitled.answered:
            CONNECTIVITY.state, CONNECTIVITY.detail = "ok", "Alpaca answers with the key."
            CONNECTIVITY.problems = []
        else:
            CONNECTIVITY.state, CONNECTIVITY.detail = "bad", "Alpaca refused the key."
            CONNECTIVITY.problems = [entitled.detail]
    CONNECTIVITY.calls, CONNECTIVITY.rate_limited = client.calls, client.rate_limited


async def watch_settings(plugin: meridian.Plugin, store: RawStore) -> None:
    """Probe Alpaca on each delivery of the settings, and report what it found."""
    async for delivered in plugin.settings():
        config = config_from(delivered.values)
        await asyncio.to_thread(probe, Alpaca(store, config.credentials), config)
        await plugin.report(healthy=CONNECTIVITY.state == "ok", detail=CONNECTIVITY.detail)


async def run() -> None:
    stopped = asyncio.Event()
    loop = asyncio.get_running_loop()
    for stop in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(stop, stopped.set)

    port = int(os.environ.get("REFERENCE_PAGE_PORT", "8000"))
    async with await meridian.connect(
        interface=meridian.Interface(port=port, title=TITLE, pages=pages),
        settings=DECLARED,
        declaration=DECLARATION,
    ) as plugin:
        log.info("registered as %s, roles %s", plugin.identity.instance_id, plugin.identity.roles)
        served = pages.serve(plugin, port)
        store = RawStore(storage_root(Path(tempfile.mkdtemp(prefix="alpaca-raw-"))))
        watching = asyncio.create_task(watch_settings(plugin, store))
        await stopped.wait()
        log.info("stopping")
        watching.cancel()
        served.shutdown()


def main() -> None:
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
    )
    asyncio.run(run())


if __name__ == "__main__":
    main()
