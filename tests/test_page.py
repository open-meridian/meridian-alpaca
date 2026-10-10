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
        config=Config(synthetic=True),
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
    assert "Synthetic" in page and "Key and feeds" in page and "100 a minute" in page
    answer = client.call_tool("read_connection")
    assert answer.status == 200 and answer.outcome == "unchanged"
    data = answer.data
    assert data["mode"] == "synthetic" and data["daily_feed"] == "sip"
    assert data["limit_per_minute"] == 100 and data["vendor_limit_per_minute"] == 200
    (session,) = data["sessions"]
    assert session["state"] == "streaming" and session["subscribed"] == 1
    assert session["reconnects"] == 2 and "1 listings on IEX" in page


def test_the_datasets_page_and_its_tool_read_one_source(client: PageClient) -> None:
    page = client.get(DATASETS, "admin").text
    assert "alpaca-1:daily" in page and "alpaca-1:live" in page and "SPY" in page
    data = client.call_tool("read_datasets").data
    daily = next(d for d in data["datasets"] if d["key"] == "daily")
    assert daily["recorded"] == 1 and daily["personal_use"] is True
    assert daily["kept_by_default"] is True and daily["modes"] == ["pull", "push"]
    live = next(d for d in data["datasets"] if d["key"] == "live")
    assert live["kept_by_default"] is False and live["modes"] == ["pull", "stream"]
    assert data["standing"][0]["symbols"] == ["SPY"] and data["listings"] == 1
    trades = next(d for d in data["datasets"] if d["key"] == "trades")
    assert trades["modes"] == ["pull", "stream"] and trades["kept_days"] == 2
    assert [w["asks"] for w in data["standing"]] == ["last, bid, ask", "trades"]
    assert all(w["streamed"] for w in data["standing"]) and data["streamed"] == 1
    # The standing wants grow: searched beside the kit's pager.
    assert '<om-pager search="Search standing wants">' in page and "kept 2 days" in page
    assert "/.meridian/ui/0.11.0/" in page


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
    assert client.call_tool("read_connection").data["key_id_set"] is True
    client.assert_no_account_data("573.78")


def test_with_no_key_the_page_says_where_to_enter_it(tmp_path: Path) -> None:
    use(State(config=Config()))
    client = PageClient(pages)
    text = client.get(CONNECTION, "admin").text
    assert "Enter the API key ID and secret key in Settings" in text
    assert client.call_tool("read_connection").data["mode"] == "no key"
    assert client.get(DATASETS, "admin").status == 200
