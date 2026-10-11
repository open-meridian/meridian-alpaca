"""The pages, Connection and Datasets, under Manage alone, each the same answer
as its read tool (`read_connection`, `read_datasets`): asked in-process with
the SDK's PageClient as the sidecar would ask them."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from meridian.suites import Recorder
from meridian.testing import PageClient

from alpaca.lake import LIVE_KINDS, TRADES, Standing
from alpaca.page import CONNECTION, DATASETS, State, pages, use
from alpaca.settings import Config, Credentials
from alpaca.stream import Streamer
from alpaca.vendor import Entitlements

from conftest import RECORDS, SPY, Clock, answer_records, at, synthetic_lake

KEY = Credentials("PKTESTKEYID0000000002", "page-test-secret-0000000000000000000002")


def synthetic_state(tmp: Path) -> State:
    recorder = Recorder("alpaca-1")
    answer_records(recorder, RECORDS)
    lake = synthetic_lake(recorder, Clock(at("2026-10-10", 9)), tmp)
    asyncio.run(lake.daily([SPY], *(__import__("datetime").date(2026, 10, 8),) * 2))
    lake.standing["WNT-1"] = Standing("WNT-1", (SPY,), LIVE_KINDS, at("2026-10-10", 9))
    lake.standing["WNT-2"] = Standing(
        "WNT-2", (SPY,), (), 0, dataset=TRADES, data_type="meridian.v1.Trade"
    )
    lake.restream()
    assert lake.source is not None

    async def nothing(*_: object) -> None:
        pass

    streamer = Streamer(lambda: None, nothing, nothing)
    streamer.session.state, streamer.session.feed = "streaming", "iex"
    streamer.session.subscribed, streamer.session.reconnects = 1, 2
    return State(
        config=Config(sample_data=True),
        lake=lake,
        source=lake.source,  # type: ignore[arg-type]
        alpaca=lake.source.alpaca,  # type: ignore[attr-defined]
        streamer=streamer,
    )


@pytest.fixture
def client(tmp_path: Path) -> PageClient:
    use(synthetic_state(tmp_path))
    return PageClient(pages)


def test_each_page_serves_manage_alone(client: PageClient) -> None:
    for path in (CONNECTION, DATASETS):
        assert client.get(path, "admin").status == 200
        assert client.get(path, "write").status == 403
        assert client.get(path, "read").status == 403


def test_the_connection_page_and_its_tool_read_one_source(client: PageClient) -> None:
    page = client.get(CONNECTION, "admin").text
    # The data role's sections, in its order, with Alpaca's rows at its blocks.
    assert page.index("<h2>Key</h2>") < page.index("<h2>Limits and use</h2>")
    assert page.index("<h2>Limits and use</h2>") < page.index("<h2>Stream</h2>")
    assert "Sample data" in page and "100 a minute" in page and "Not entitled" in page
    answer = client.call_tool("read_connection")
    assert answer.status == 200 and answer.outcome == "unchanged"
    data = answer.data
    assert data["mode"] == "Sample data" and data["daily_feed"] == "sip"
    assert [c["label"] for c in data["credentials"]] == ["API key ID", "API secret key"]
    assert data["limit"].startswith("100 a minute") and data["sessions"] == 1
    assert data["stream"]["state"] == "streaming" and data["stream"]["subscribed"] == 1
    assert data["stream"]["reconnects"] == 2 and "1 listings, since" in page
    assert "SIP" in data["not_entitled"] and "IEX" in data["entitled_at_vendor"]


def test_the_datasets_page_and_its_tool_read_one_source(client: PageClient) -> None:
    page = client.get(DATASETS, "admin").text
    assert "alpaca-1:daily" in page and "alpaca-1:live" in page
    assert page.index("<h2>Served</h2>") < page.index("<h2>Wants</h2>")
    assert page.index("<h2>Wants</h2>") < page.index("<h2>Subjects</h2>")
    assert page.index("<h2>Subjects</h2>") < page.index("<h2>Kept current</h2>")
    data = client.call_tool("read_datasets").data
    daily = next(d for d in data["served"] if d["key"] == "daily")
    assert daily["recorded"] == 1 and daily["arrives"] == "pull, push"
    assert daily["default_terms"] == "kept; personal use" and daily["feed"] == "SIP"
    assert daily["licensed"] is False and "Not licensed yet" in page
    live = next(d for d in data["served"] if d["key"] == "live")
    assert live["default_terms"] == "served, not kept; personal use"
    trades = next(d for d in data["served"] if d["key"] == "trades")
    assert trades["arrives"] == "pull, stream" and trades["default_terms"].startswith("kept 2")
    # The latest want first, each with what it asks and its listings.
    assert [w["asked"] for w in data["wants"]] == ["trades", "last, bid, ask"]
    assert data["wants"][0]["listings"] == ["SPY"] and data["wants"][0]["streamed"]
    assert data["listings"] == 1 and data["streamed"] == 1 and data["reads_instruments"]
    assert "/.meridian/ui/0.12.0/" in page


def test_a_licensed_dataset_is_said_so(tmp_path: Path) -> None:
    state = synthetic_state(tmp_path)
    state.licensed = frozenset({"alpaca-1:daily"})
    use(state)
    served = PageClient(pages).call_tool("read_datasets").data["served"]
    assert [s["licensed"] for s in served] == [True, False, False]


def test_a_browser_is_drawn_the_latest_wants_and_a_tool_every_one(tmp_path: Path) -> None:
    state = synthetic_state(tmp_path)
    assert state.lake is not None
    for n in range(3, 12):
        state.lake.standing[f"WNT-{n}"] = Standing(f"WNT-{n}", (SPY,), LIVE_KINDS, 0)
    use(state)
    client = PageClient(pages)
    assert len(client.call_tool("read_datasets").data["wants"]) == 11
    page = client.get(DATASETS, "admin").text
    assert page.count("<tr><td><code>alpaca-1:live</code></td><td>last, bid, ask") == 3
    assert "The latest 3 of 11 standing wants are shown." in page


def test_the_figures_are_the_data_roles_in_its_order(tmp_path: Path) -> None:
    from alpaca.page import figures

    said = figures(synthetic_state(tmp_path))
    assert [f.label for f in said] == [
        "Connection",
        "Entitled at the vendor",
        "Stream",
        "Last recorded",
        "Rate use",
    ]
    assert said[0].value == "Sample data" and said[2].value == "1 listings"


def test_no_key_value_and_no_price_is_ever_shown(tmp_path: Path) -> None:
    state = synthetic_state(tmp_path)
    state.config = Config(credentials=KEY, key_id_set=True, secret_key_set=True)
    state.source._entitled = Entitlements(answered=True, iex=True, sip_history=True)  # type: ignore[union-attr]
    use(state)
    client = PageClient(pages)
    for path in (CONNECTION, DATASETS):
        text = client.get(path, "admin").text
        assert KEY.key_id not in text and KEY.secret_key not in text
        assert "573.78" not in text  # the close it recorded: a reader's to read
    for tool in ("read_connection", "read_datasets"):
        said = str(client.call_tool(tool).json)
        assert KEY.key_id not in said and KEY.secret_key not in said and "573.78" not in said
    assert client.call_tool("read_connection").data["credentials"][0]["set"] is True
    client.assert_no_account_data("573.78")


def test_with_no_key_the_page_says_where_to_enter_it(tmp_path: Path) -> None:
    use(State(config=Config()))
    client = PageClient(pages)
    text = client.get(CONNECTION, "admin").text
    # The role's "not set up yet" state, linking to core's Settings tab.
    assert "No key yet" in text and 'data-om-opens="settings"' in text
    assert client.call_tool("read_connection").data["mode"] == "No key"
    datasets = client.get(DATASETS, "admin").text
    assert "No wants yet" in datasets
