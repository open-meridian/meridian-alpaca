"""The Alpaca plugin: Alpaca's US equity and ETF market data in an Open Meridian
deployment's lake, as a data plugin (contract v20; plans/the-lake-prices-the-book,
the retail pair).

It connects to the sidecar it is launched beside, declaring its catalogue,
its settings and its storage (declaration.py) and its pages (page.py); on
each delivery of its settings it makes its Alpaca client again with the key
(or, with sample data, the built-in exchange) and asks which feeds the key
is entitled to; it reads which of its datasets a deployment admin has
licensed (contract v20), and reports the data role's figures; then it
hears what the lake wants of it and records against each want or declines
it per subject (lake.py), and keeps its timetable:
standing wants kept current, days still forming read again, a final close
pushed. While any standing want stands, it streams Alpaca's trades and
quotes for the listings wanted (stream.py), reconnecting when the stream
breaks and catching up the gap; and, the first time a listing's trades are
wanted after a restart, backfilling what it missed while stopped from the
last trade it recorded (lake.py, `backfill_restart`).

From 0.7.0 it is also the account's connection for orders, where the
deployment launched it with `connectivity`: it hears each placement naming
its instance and places it at Alpaca's Trading API in the environment
chosen (paper or live), hears Alpaca's trade updates and reads the orders,
honouring the firm's pauses and stops, and from 0.8.0 reports the fills of
orders placed in Alpaca's own app on no placement (trading.py, updates.py).
From 0.8.0, where launched with `custody`, it is the account's custodian
too: it reads the account every Read every, reporting its positions, cash
and activity (custody.py). Sample data trades with, and reads, a simulated
brokerage (synthetic_broker.py), never Alpaca.

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
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import meridian
from meridian import edge

from .broker import Broker, base_for, stream_for
from .custody import Syncer
from .declaration import DECLARATION
from .kinds import Store, tend, windows_from
from .kinds import root as kinds_root
from .lake import Lake, Products
from .page import TITLE, State, figures, pages, serve
from .raw import RawStore, storage_root
from .settings import DECLARED, Config, config_from
from .source import AlpacaSource
from .stream import AlpacaFeed, Feed, Streamer, symbol_limit
from .synthetic import SyntheticFeed, SyntheticTransport, exchange
from .synthetic_broker import SyntheticBroker
from .trading import Noted, Trader, Watched, trading_files
from .updates import AlpacaUpdates, SyntheticUpdates
from .vendor import IEX, Alpaca, NoKey, VendorError, urllib_transport
from .venue import AlpacaVenue

log = logging.getLogger("alpaca")

#: How often the timetable is looked at.
TICK_SECONDS = 15
#: How often the datasets' licences are read again.
LICENCES_SECONDS = 60
#: How often connectivity reads the orders at Alpaca, beside its trade
#: updates; and the simulated brokerage, which steps an order on each read.
POLL_SECONDS = 15
SYNTHETIC_POLL_SECONDS = 1
#: How often custody reads the simulated brokerage, in place of Read every.
SYNTHETIC_READ_SECONDS = 5
#: How often the kinds' windows are tended between deliveries of settings.
TEND_SECONDS = 3600


class Account:
    """Connectivity's client for the settings as they stand: the Trading API
    of the environment chosen, or the simulated brokerage."""

    def __init__(self, store: Store, granted: Path | None) -> None:
        self.store, self.granted = store, granted
        self.broker: Broker | None = None
        self.venue: AlpacaVenue | None = None
        self.updates: AlpacaUpdates | SyntheticUpdates | None = None
        self._synthetic: SyntheticBroker | None = None
        self._said: tuple[object, ...] = ()

    def use(self, config: Config) -> bool:
        """Make the client again where the settings changed what it is; True
        where they did."""
        said = (config.sample_data, config.environment, config.credentials)
        if said == self._said:
            return False
        self._said = said
        if config.sample_data:
            if self._synthetic is None:
                state = self.granted / "synthetic-venue" / "orders.json" if self.granted else None
                self._synthetic = SyntheticBroker(lambda: datetime.now(UTC), state)
            self.broker = Broker(
                self.store,
                None,
                base_for(config.environment),
                send=self._synthetic,
                keyless=True,
                synthetic=True,
            )
            self.updates = SyntheticUpdates(self._synthetic)
        elif config.credentials is not None:
            self.broker = Broker(self.store, config.credentials, base_for(config.environment))
            self.updates = AlpacaUpdates(config.credentials, stream_for(config.environment))
        else:
            self.broker, self.updates = None, None
        self.venue = AlpacaVenue(self.broker) if self.broker is not None else None
        return True


def source_for(config: Config, store: RawStore) -> tuple[Alpaca | None, AlpacaSource | None]:
    """The client and the source the settings give: sample data, the key's,
    or none while the key is incomplete."""
    if config.sample_data:
        alpaca = Alpaca(store, None, transport=SyntheticTransport(), keyless=True)
        return alpaca, AlpacaSource(alpaca, synthetic=True)
    if config.credentials is None:
        return None, None
    alpaca = Alpaca(store, config.credentials, transport=urllib_transport)
    return alpaca, AlpacaSource(alpaca)


def feed_for(state: State, store: RawStore) -> Feed | None:
    """The stream the settings give: sample data, Alpaca's on the feed the
    key has in real time, or none while the key is incomplete."""
    config = state.config
    if config.sample_data:
        return SyntheticFeed(store)
    if config.credentials is None:
        return None
    known = state.source.known if state.source is not None else None
    feed = known.live_feed if known is not None and known.answered else IEX
    return AlpacaFeed(config.credentials, feed, store)


def stream_limit(state: State) -> int | None:
    """How many listings the key may stream at once."""
    if state.config.sample_data:
        return None
    known = state.source.known if state.source is not None else None
    return symbol_limit(known.live_feed if known is not None else IEX)


async def health(plugin: meridian.Plugin, state: State) -> None:
    """Say how the source is, on the heartbeat: a missing key or a refused one
    is the plugin's health, never a ticket."""
    config = state.config
    if config.sample_data:
        await say(plugin, state, True, "sample data: the built-in exchange")
        return
    if state.source is None:
        await say(plugin, state, False, "no Alpaca key: enter it in Settings")
        return
    try:
        entitled = await asyncio.to_thread(state.source.entitlements, True)
    except (NoKey, VendorError, OSError) as failed:
        await say(plugin, state, False, f"Alpaca did not answer: {failed}")
        return
    if not entitled.answered:
        await say(plugin, state, False, f"Alpaca refused the key: {entitled.detail}")
        return
    live = "SIP" if entitled.sip_live else "IEX"
    await say(plugin, state, True, f"answering; daily from SIP, live from {live}")


async def say(plugin: meridian.Plugin, state: State, healthy: bool, detail: str) -> None:
    """The plugin's health, with the data role's figures as they stand."""
    await plugin.report(healthy=healthy, detail=detail[:200], figures=figures(state))


async def read_licences(plugin: Any, state: State) -> None:
    """Which of its own declared datasets a deployment admin has licensed
    (contract v20, W10.9), for the Datasets page: a dataset with none is
    served by its catalogue's default terms until one is set."""
    answered = await plugin.list_datasets()
    state.licensed = frozenset(licence.dataset for licence in answered.licences)


async def licences(plugin: meridian.Plugin, state: State) -> None:
    """The licences, read on start and every minute."""
    while True:
        try:
            await read_licences(plugin, state)
        except meridian.MeridianError as failed:
            log.warning("the datasets' licences were not read this time: %s", failed)
        await asyncio.sleep(LICENCES_SECONDS)


async def watch_settings(
    plugin: meridian.Plugin,
    state: State,
    store: RawStore,
    configured: asyncio.Event,
    account: Account | None = None,
    woken: asyncio.Event | None = None,
) -> None:
    async for delivered in plugin.settings():
        state.config = config_from(delivered.values)
        state.values = dict(delivered.values)
        state.alpaca, state.source = source_for(state.config, store)
        if state.lake is not None:
            state.lake.source = state.source
            if state.config.sample_data:
                # The built-in exchange's listings, as a source's own list.
                await state.lake.seed(sorted(exchange()["bars"]))
        if account is not None and account.use(state.config):
            state.updates = account.updates
            if woken is not None:
                woken.set()
        configured.set()
        await health(plugin, state)
        if state.lake is not None:
            state.lake.stream_limit = stream_limit(state)
            state.lake.restream()
        if state.streamer is not None:
            state.streamer.restart()
        if account is not None:
            await tend_now(plugin, account.store, delivered.values)


async def tend_now(plugin: meridian.Plugin, kinds: Store, values: Any) -> None:
    """Each kind's units past its window moved as the settings say."""
    try:
        await tend(plugin, kinds, windows_from(values), datetime.now(UTC).date())
    except (meridian.MeridianError, OSError) as failed:
        log.warning("the raw records were not tended this time: %s", failed)


async def follow_scope(plugin: meridian.Plugin, state: State, woken: asyncio.Event) -> None:
    """The account scope, its links (W4.11) and its ems's venue account map
    (W11.22), on start and on every change: the account side and its pages
    hold it from here, and custody reads again at once."""
    async for scope in plugin.account_scope():
        state.scope = scope
        woken.set()


async def custody_reads(state: State, syncer: Syncer, woken: asyncio.Event) -> None:
    """Read Alpaca now, then every Read every, or sooner when the settings or
    the links change."""
    while True:
        woken.clear()
        try:
            await syncer.run_once()
        except meridian.MeridianError as failed:
            log.warning("this read of the account stopped: %s", failed)
        wait = SYNTHETIC_READ_SECONDS if state.config.sample_data else state.config.poll_seconds
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(woken.wait(), timeout=wait)


async def trade(state: State, trader: Trader, hear: bool) -> None:
    """Connectivity's work; a refusal it meets is said, and the rest goes on."""
    await trader.run(
        lambda: SYNTHETIC_POLL_SECONDS if state.config.sample_data else POLL_SECONDS,
        hear=hear,
    )


async def hear_updates(account: Account, trader: Trader) -> None:
    """The trade updates of the client as the settings stand, heard again
    whenever the client is made again."""
    while True:
        updates = account.updates
        if updates is None:
            await asyncio.sleep(1)
            continue
        task = asyncio.create_task(updates.run(trader, account.store))
        while account.updates is updates and not task.done():
            await asyncio.sleep(1)
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError, Exception):
            await task


async def tend_hourly(plugin: meridian.Plugin, state: State, kinds: Store) -> None:
    while True:
        await asyncio.sleep(TEND_SECONDS)
        await tend_now(plugin, kinds, state.values)


async def timetable(plugin: meridian.Plugin, state: State, lake: Lake) -> None:
    while True:
        try:
            await lake.tick()
        except meridian.MeridianError as failed:
            log.warning("the timetable stopped this time: %s", failed)
        # The figures go on the next heartbeat.
        plugin.figures = figures(state)
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
    kinds = Store(kinds_root())
    async with await meridian.connect(
        interface=meridian.Interface(port=port, title=TITLE, pages=pages),
        settings=DECLARED,
        declaration=DECLARATION,
        # Custody names the account by Alpaca's number, which an admin of the
        # plugin links to one of the deployment's accounts (W6.4).
        reads_external_accounts=True,
    ) as plugin:
        roles = set(plugin.identity.roles)
        log.info(
            "registered as %s, roles %s",
            plugin.identity.instance_id,
            ", ".join(sorted(roles)) or "none",
        )
        lake = Lake(plugin, None, Products(granted / "products.json" if granted else None))
        state = State(lake=lake, store=kinds)
        streamer = Streamer(
            lambda: feed_for(state, store), lake.heard, lake.flush, caught_up=lake.caught_up
        )
        lake.streamer = state.streamer = streamer
        account = Account(kinds, granted)
        woken = asyncio.Event()
        if "custody" in roles:
            state.syncer = Syncer(
                plugin,
                lambda: account.broker,
                paper=lambda: state.config.paper and not state.config.sample_data,
                stale_after=lambda: state.config.stale_after,
                linked=lambda number: state.scope.link_of(number) is not None,
            )
        if "connectivity" in roles:
            sent, enabled, originated = trading_files(granted)
            state.trader = Trader(
                plugin,
                lambda: account.venue,
                now=lambda: datetime.now(UTC),
                sent=Noted(sent),
                enabled=Noted(enabled),
                # The venue account codes its ems mapped on it (W11.22).
                scope=lambda: state.scope,
                watched=Watched(originated),
            )
        configured = asyncio.Event()
        page = serve(plugin, state, port)
        tasks = [
            asyncio.create_task(
                again(
                    lambda: watch_settings(plugin, state, store, configured, account, woken),
                    "the settings",
                )
            )
        ]
        await configured.wait()
        if roles & {"custody", "connectivity"}:
            tasks.append(
                asyncio.create_task(again(lambda: follow_scope(plugin, state, woken), "the scope"))
            )
            tasks.append(asyncio.create_task(tend_hourly(plugin, state, kinds)))
        if state.syncer is not None:
            syncer = state.syncer
            tasks.append(
                asyncio.create_task(
                    again(lambda: custody_reads(state, syncer, woken), "custody's reads")
                )
            )
        if state.trader is not None:
            trader = state.trader
            # With the data role too, its placements are heard on the one
            # stream that hears the lake's wants (below).
            hears = "data" not in roles
            tasks.append(asyncio.create_task(again(lambda: trade(state, trader, hears), "trading")))
            tasks.append(
                asyncio.create_task(again(lambda: hear_updates(account, trader), "trade updates"))
            )
        if "data" in roles:
            # With connectivity too, its placements and instructions are heard
            # on the one stream that hears the lake's wants.
            hearing: dict[str, Any] = {
                "observations_wanted": lake.on_want,
                "want_withdrawn": lake.on_withdrawn,
            }
            if state.trader is not None:
                hearing["placement_updated"] = state.trader.on_placement
                hearing["trading_instruction_changed"] = state.trader.on_instruction
                hearing["execution_recorded"] = state.trader.on_execution
            tasks.append(
                asyncio.create_task(
                    again(
                        lambda: plugin.receive(**hearing),
                        "hearing the lake",
                    )
                )
            )
            tasks.append(
                asyncio.create_task(again(lambda: licences(plugin, state), "the licences"))
            )
            tasks.append(
                asyncio.create_task(again(lambda: timetable(plugin, state, lake), "the timetable"))
            )
            tasks.append(asyncio.create_task(again(streamer.run, "the stream")))
        log.info("serving its pages on 127.0.0.1:%d", port)
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
