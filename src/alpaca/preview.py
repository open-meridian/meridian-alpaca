"""The pages as a deployment admin would see them, each printed as one HTML
file: for looking at the pages without a deployment.

    python -m alpaca.preview datasets > preview/datasets.html

The page is `connection` (the default), `datasets` or `connection-no-key`.
Each is served by its own view, as the sidecar would ask it, over the
built-in synthetic exchange: a week of closes recorded for the exchange's
listings, two standing wants kept current, and a venue not held. It links
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
from .lake import LIVE_KINDS, Lake, Products, Standing
from .page import CONNECTION, DATASETS, State, pages, use
from .raw import RawStore
from .settings import Config
from .source import AlpacaSource
from .synthetic import SyntheticTransport, exchange
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
        for n, chunk in enumerate((subjects[:2], subjects[2:])):
            lake.standing[f"WNT-{n}"] = Standing(f"WNT-{n}", tuple(chunk), LIVE_KINDS, NOW)

    asyncio.run(fill())
    return State(config=Config(synthetic=True), lake=lake, source=source, alpaca=alpaca)


def main(argv: list[str]) -> int:
    tab = argv[0] if argv else "connection"
    with tempfile.TemporaryDirectory() as held:
        path: str | None
        if tab == "connection-no-key":
            use(State())
            path = CONNECTION
        else:
            use(synthetic_state(Path(held)))
            path = {"connection": CONNECTION, "datasets": DATASETS}.get(tab)
            if path is None:
                print(
                    f"no page {tab!r}: connection, datasets or connection-no-key", file=sys.stderr
                )
                return 2
        assert path is not None
        answer = PageClient(pages).get(path, "admin")
        sys.stdout.write(answer.text)
    return 0 if answer.status == 200 else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
