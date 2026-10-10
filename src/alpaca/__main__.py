"""The Alpaca plugin: Alpaca's US equity and ETF market data in an Open Meridian
deployment's lake, as a `dgm` (contract v18; plans/the-lake-prices-the-book,
the retail pair).

It connects to the sidecar it is launched beside, declaring its catalogue,
its settings and its storage (declaration.py) and its pages (page.py); on
each delivery of its settings it makes its Alpaca client again with the key
(or, in synthetic mode, the built-in exchange) and asks which feeds the key
is entitled to; then it hears what the lake wants of it and records against
each want or declines it per subject (lake.py), and keeps its timetable:
standing wants kept current, days still forming read again, a final close
pushed.

It holds nothing it cannot lose: what each subject's symbol is, and every
raw response, are in the storage the deployment grants it (decisions/028);
a restart reads them back and hears the lake again. The key arrives as two
secret settings, never logged, shown or sent anywhere but Alpaca.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import signal
import tempfile
from collections.abc import Callable, Coroutine
from pathlib import Path
from typing import Any

import meridian
from meridian import edge

from .declaration import DECLARATION
from .lake import Lake, Products
from .page import TITLE, State, pages, serve
from .raw import RawStore, storage_root
from .settings import DECLARED, Config, config_from
from .source import AlpacaSource
from .synthetic import SyntheticTransport, exchange
from .vendor import Alpaca, NoKey, VendorError, urllib_transport

log = logging.getLogger("alpaca")

#: How often the timetable is looked at.
TICK_SECONDS = 15


def source_for(config: Config, store: RawStore) -> tuple[Alpaca | None, AlpacaSource | None]:
    """The client and the source the settings give: synthetic, the key's, or
    none while the key is incomplete."""
    if config.synthetic:
        alpaca = Alpaca(store, None, transport=SyntheticTransport(), keyless=True)
        return alpaca, AlpacaSource(alpaca, synthetic=True)
    if config.credentials is None:
        return None, None
    alpaca = Alpaca(store, config.credentials, transport=urllib_transport)
    return alpaca, AlpacaSource(alpaca)


async def health(plugin: meridian.Plugin, state: State) -> None:
    """Say how the source is, on the heartbeat: a missing key or a refused one
    is the plugin's health, never a ticket."""
    config = state.config
    if config.synthetic:
        await plugin.report(healthy=True, detail="synthetic: the built-in exchange")
        return
    if state.source is None:
        await plugin.report(healthy=False, detail="no Alpaca key: enter it in Settings")
        return
    try:
        entitled = await asyncio.to_thread(state.source.entitlements, True)
    except (NoKey, VendorError, OSError) as failed:
        await plugin.report(healthy=False, detail=f"Alpaca did not answer: {failed}"[:200])
        return
    if not entitled.answered:
        await plugin.report(healthy=False, detail=f"Alpaca refused the key: {entitled.detail}")
        return
    live = "SIP" if entitled.sip_live else "IEX"
    await plugin.report(healthy=True, detail=f"answering; daily from SIP, live from {live}")


async def watch_settings(
    plugin: meridian.Plugin, state: State, store: RawStore, configured: asyncio.Event
) -> None:
    async for delivered in plugin.settings():
        state.config = config_from(delivered.values)
        state.alpaca, state.source = source_for(state.config, store)
        if state.lake is not None:
            state.lake.source = state.source
            if state.config.synthetic:
                # The built-in exchange's listings, as a source's own list.
                await state.lake.seed(sorted(exchange()["bars"]))
        configured.set()
        await health(plugin, state)


async def timetable(lake: Lake) -> None:
    while True:
        try:
            await lake.tick()
        except meridian.MeridianError as failed:
            log.warning("the timetable stopped this time: %s", failed)
        await asyncio.sleep(TICK_SECONDS)


async def again(work: Callable[[], Coroutine[Any, Any, None]], what: str) -> None:
    """A stream that stops (the deployment still starting, a sidecar
    restarted) is said and opened again, a little later each time; never
    lost with its task."""
    wait = 1.0
    while True:
        try:
            await work()
            return
        except asyncio.CancelledError:
            raise
        except Exception as failed:  # noqa: BLE001 - said, and opened again
            log.warning("%s stopped (%s); again in %.0fs", what, failed, wait)
        await asyncio.sleep(wait)
        wait = min(wait * 2, 30.0)


async def run() -> None:
    stopped = asyncio.Event()
    loop = asyncio.get_running_loop()
    for stop in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(stop, stopped.set)

    port = int(os.environ.get("ALPACA_PAGE_PORT", "8000"))
    granted = edge.storage_dir()
    store = RawStore(storage_root(Path(tempfile.mkdtemp(prefix="alpaca-raw-"))))
    async with await meridian.connect(
        interface=meridian.Interface(port=port, title=TITLE, pages=pages),
        settings=DECLARED,
        declaration=DECLARATION,
    ) as plugin:
        log.info(
            "registered as %s, roles %s",
            plugin.identity.instance_id,
            ", ".join(plugin.identity.roles) or "none",
        )
        lake = Lake(plugin, None, Products(granted / "products.json" if granted else None))
        state = State(lake=lake)
        configured = asyncio.Event()
        page = serve(plugin, state, port)
        tasks = [
            asyncio.create_task(
                again(lambda: watch_settings(plugin, state, store, configured), "the settings")
            )
        ]
        await configured.wait()
        tasks.append(
            asyncio.create_task(
                again(
                    lambda: plugin.receive(
                        observations_wanted=lake.on_want, want_withdrawn=lake.on_withdrawn
                    ),
                    "hearing the lake",
                )
            )
        )
        tasks.append(asyncio.create_task(again(lambda: timetable(lake), "the timetable")))
        log.info("serving its pages on 127.0.0.1:%d, hearing the lake's wants", port)
        await stopped.wait()
        log.info("stopping")
        for task in tasks:
            task.cancel()
        for task in tasks:
            with contextlib.suppress(asyncio.CancelledError):
                await task
        page.shutdown()


def main() -> None:
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
    )
    asyncio.run(run())


if __name__ == "__main__":
    main()
