"""The Connectivity page, against a stand-in for the sidecar.

`meridian plugin check --run-tests` runs these. A `dgm` shows no market data,
so its one page is at Manage alone: it is refused under Open and View, and
shows whether Alpaca answers with the key -- each part set or not, never its
value -- the feeds it is entitled to and the datasets it would serve, never
a price.
"""

from __future__ import annotations

import asyncio
import urllib.error
import urllib.request
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import meridian
import pytest
from meridian.operations import Operations
from meridian.testing import PageClient

from alpaca import __main__ as plugin
from alpaca.page import CONNECTIVITY, Connectivity, pages
from alpaca.settings import config_from

from conftest import KEY, client, kept_text

GIVEN = {"alpaca_api_key_id": KEY.key_id, "alpaca_api_secret_key": KEY.secret_key}


class Sidecar(Operations):
    """Who the plugin was launched as; it sends no operation from its page."""

    identity = meridian.Identity("alpaca-1", roles=("dgm",))
    grants = meridian.Grants()

    async def _operate(self, method: Any, params: Any) -> Any:
        raise AssertionError(f"the page sent {method}, which a dgm's page never sends")


def page_client() -> PageClient:
    return PageClient(pages, Sidecar())


@pytest.fixture(autouse=True)
def fresh() -> Iterator[None]:
    yield
    CONNECTIVITY.__dict__.update(Connectivity().__dict__)


def test_the_page_is_served_under_manage_alone() -> None:
    served = {(r.page.path, r.level): r.response.status for r in page_client().every_page()}
    assert served == {("/", "admin"): 200, ("/", "write"): 403, ("/", "read"): 403}


def test_it_shows_the_key_set_the_feeds_and_the_datasets_and_no_market_data(
    tmp_path: Path,
) -> None:
    alpaca, _ = client(
        tmp_path, refuse={"/v2/stocks/quotes/latest sip": [(403, "forbidden-sip.json")]}
    )
    plugin.probe(alpaca, config_from({**GIVEN, "feed": "sip"}))
    page = page_client().get("/", "admin").text
    assert "Alpaca answers with the key." in page and 'state="ok"' in page
    assert "<dt>API key ID</dt><dd>set</dd>" in page
    assert "<dt>API secret key</dt><dd>set</dd>" in page
    # Never the key's value, anywhere it is shown or kept.
    for secret in KEY.secrets():
        assert secret not in page and secret not in kept_text(tmp_path)
    assert "<td>SIP, real time</td><td>not entitled</td>" in page
    assert "<td>SIP, past its latest 15 minutes</td><td>entitled</td>" in page
    # The free account's SIP history serves the daily dataset; IEX the live one.
    for key, served in (
        ("daily-iex", "not served"),
        ("daily-sip", "served"),
        ("live-iex", "served"),
        ("live-sip", "not served"),
    ):
        assert f"<td><code>{key}</code></td>" in page
        row = page.split(f"<td><code>{key}</code></td>")[1].split("</tr>")[0]
        assert f"<td>{served}</td>" in row, key
    assert "America/New_York, ends 16:00" in page and "served, not kept" in page
    # No price from any synthetic exchange reaches the page.
    for price in ("62.79", "62.81", "62.795", "62.12"):
        assert price not in page


def test_without_a_key_it_says_which_part_is_missing_and_asks_nothing(tmp_path: Path) -> None:
    alpaca, replay = client(tmp_path, credentials=None)
    plugin.probe(alpaca, config_from({"alpaca_api_key_id": KEY.key_id}))
    page = page_client().get("/", "admin").text
    assert "Waiting for the API secret key." in page and 'state="warn"' in page
    assert "<dt>API key ID</dt><dd>set</dd>" in page
    assert "<dt>API secret key</dt><dd>not set</dd>" in page
    assert KEY.key_id not in page and replay.asked == []


def test_a_refused_key_is_said_on_the_page(tmp_path: Path) -> None:
    alpaca, _ = client(
        tmp_path, refuse={"/v2/stocks/quotes/latest iex": [(401, "unauthorized.json")]}
    )
    plugin.probe(alpaca, config_from(GIVEN))
    page = page_client().get("/", "admin").text
    assert "Alpaca refused the key." in page and 'state="bad"' in page
    assert "request is not authorized" in page


def test_alpaca_not_answering_is_said_on_the_page(tmp_path: Path) -> None:
    alpaca, _ = client(
        tmp_path, refuse={"/v2/stocks/quotes/latest iex": [(503, "unauthorized.json")]}
    )
    plugin.probe(alpaca, config_from(GIVEN))
    page = page_client().get("/", "admin").text
    assert "Alpaca did not answer." in page and 'state="bad"' in page


def _status(address: str) -> int:
    try:
        with urllib.request.urlopen(address, timeout=10) as answer:
            return int(answer.status)
    except urllib.error.HTTPError as answer:
        return answer.code


async def test_a_request_the_sidecar_did_not_forward_is_turned_away() -> None:
    server = pages.serve(Sidecar(), 0)  # type: ignore[arg-type]
    try:
        address = f"http://127.0.0.1:{server.server_address[1]}/"
        assert await asyncio.to_thread(_status, address) == 401
    finally:
        server.shutdown()
        server.server_close()
