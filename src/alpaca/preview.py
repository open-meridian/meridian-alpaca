"""The pages as a deployment admin would see them, each printed as one HTML
file: for looking at the pages without a deployment.

    python -m alpaca.preview datasets > preview/datasets.html

The page is `connection` (the default), `datasets`, or either with no key
(`connection-no-key`, `datasets-no-key`): the role's "not set up yet" states.
Each is served by its own view, as the sidecar would ask it, over the
built-in recorded exchange, on sample data: a week of closes recorded for the exchange's
listings, a cycle of the synthetic stream's trades and quotes, standing
wants kept current and streamed (enough of them to page and search), and a
venue not held. It links
the kit where the dashboard serves it, /.meridian/ui/<version>/, so serve it
beside the kit to see it styled (meridian-ui's `make serve` serves the kit
under that path); opened on its own it is the page without the kit,
unstyled, which must work too. There is no sidecar here: what would be
recorded is recorded by the SDK's suite `Recorder`, and nothing leaves.
"""

from __future__ import annotations

import asyncio
import datetime as dt
import sys
import tempfile
from pathlib import Path

from meridian.plugin.v1 import operations_pb2 as ops
from meridian.suites import Recorder
from meridian.testing import PageClient

from .convert import NEW_YORK
from .lake import LIVE, LIVE_KINDS, QUOTE, TRADE, TRADES, Lake, Products, Standing
from .page import CONNECTION, DATASETS, State, pages, use
from .raw import RawStore
from .settings import Config
from .source import AlpacaSource
from .stream import Streamer
from .synthetic import SyntheticFeed, SyntheticTransport, exchange
from .vendor import NS, Alpaca, RateLimiter

#: Saturday after the synthetic week, 09:00 New York.
NOW = int(dt.datetime(2026, 10, 10, 9, tzinfo=NEW_YORK).timestamp()) * NS


def synthetic_state(root: Path) -> State:
    recorder = Recorder("alpaca")
    alpaca = Alpaca(
        RawStore(root / "raw"),
        None,
        transport=SyntheticTransport(),
        limiter=RateLimiter(per_minute=6_000_000, sleep=lambda _: None),
        clock_ns=lambda: NOW,
        keyless=True,
    )
    source = AlpacaSource(alpaca, synthetic=True)
    source.entitlements()
    lake = Lake(recorder, source, Products(root / "products.json"), lambda: NOW)

    async def fill() -> None:
        await lake.seed(sorted(exchange()["bars"]))
        subjects = sorted(lake.products.symbols)
        await lake.daily(subjects, dt.date(2026, 10, 5), dt.date(2026, 10, 9))
        await lake.daily(
            subjects, dt.date(2026, 10, 5), dt.date(2026, 10, 9), data_type="meridian.v1.Bar"
        )
        recorder.answer("ResolveVenue", lambda _: ops.ResolveVenueResult(found=False))
        await lake.live(subjects[:2])
        asks = ((LIVE, "meridian.v1.Price"), (TRADES, TRADE), (LIVE, QUOTE))
        for n in range(9):
            chunk = tuple(subjects[n % len(subjects) :] or subjects)
            dataset, data_type = asks[n % 3]
            lake.standing[f"WNT-{n}"] = Standing(
                f"WNT-{n}",
                chunk,
                LIVE_KINDS if data_type.endswith("Price") else (),
                NOW,
                dataset=dataset,
                data_type=data_type,
            )
        lake.restream()
        feed = SyntheticFeed(RawStore(root / "raw"), clock_ns=lambda: NOW)
        await feed.open()
        await feed.subscribe(lake.streamed)
        await lake.heard(await feed.receive())
        await lake.flush(force=True)
        await feed.close()

    asyncio.run(fill())

    async def nothing(*_: object) -> None:
        pass

    streamer = Streamer(lambda: None, nothing, nothing)
    held = streamer.session
    held.state, held.feed, held.subscribed = "streaming", "iex", len(lake.streamed)
    held.opened, held.reconnects, held.messages, held.since_ns = 2, 1, 7, NOW
    return State(
        config=Config(sample_data=True), lake=lake, source=source, alpaca=alpaca, streamer=streamer
    )


def main(argv: list[str]) -> int:
    tab = argv[0] if argv else "connection"
    paths = {"connection": CONNECTION, "datasets": DATASETS}
    path = paths.get(tab.removesuffix("-no-key"))
    if path is None:
        print(f"no page {tab!r}: connection, datasets, or either -no-key", file=sys.stderr)
        return 2
    with tempfile.TemporaryDirectory() as held:
        use(State() if tab.endswith("-no-key") else synthetic_state(Path(held)))
        answer = PageClient(pages).get(path, "admin")
        sys.stdout.write(answer.text)
    return 0 if answer.status == 200 else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
