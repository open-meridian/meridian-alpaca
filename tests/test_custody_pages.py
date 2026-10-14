"""Custody's pages (from 0.8.0): Account links under Manage, Statements and
History under Open and View, each the same answer as its tool, and an
account shown only to a person who may read the account it is linked to."""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal

import meridian
from meridian.suites import Recorder
from meridian.testing import PageClient

from alpaca import page
from alpaca.custody import Converted, Syncer
from alpaca.custody_pages import HISTORY, LINKS, STATEMENTS
from alpaca.page import State, pages
from alpaca.settings import Config

NUMBER = "SYN-ALP-1001"
ACCOUNT = "ACC-01JAE000000000000000000001"
ROLES = ("data", "custody", "connectivity")


def client(linked: bool = True, **caller: object) -> PageClient:
    recorder = Recorder("alpaca")
    syncer = Syncer(
        recorder, lambda: None, paper=lambda: False, stale_after=lambda: timedelta(hours=36)
    )
    syncer.status.number = NUMBER
    syncer.status.state = "Current"
    syncer.status.statement_as_of = "2026-10-12"
    syncer.status.statement_state = "Closed"
    syncer.status.activities = [
        (
            Converted(
                external_activity_id="act-1",
                type="DIV",
                kind=meridian.ActivityKind.ACTIVITY_KIND_DIVIDEND,
                symbol="AAPL",
                trade_date="2026-09-15",
                units=None,
                price=None,
                amount=meridian.Money(Decimal("0.75"), "USD"),
                description="Cash DIV @ 0.25, Pos QTY: 3",
            ),
            "INS-AAPL",
        )
    ]
    scope = meridian.AccountScope(
        links=(meridian.LinkedExternalAccount(NUMBER, ACCOUNT, "E2E Orders"),) if linked else ()
    )
    page.use(State(config=Config(sample_data=True), syncer=syncer, scope=scope))
    return PageClient(pages, recorder, roles=ROLES, **caller)  # type: ignore[arg-type]


def test_account_links_is_managed_alone_and_lists_the_account() -> None:
    pages_ = client()
    assert pages_.get(LINKS, "admin").status == 200
    assert pages_.get(LINKS, "write").status == 403
    said = pages_.call_tool("read_account_links", level="admin").data
    assert [a["external_account_id"] for a in said["external_accounts"]] == [NUMBER]
    assert said["links"][0]["account_id"] == ACCOUNT


def test_statements_and_history_show_a_linked_account_to_its_readers_alone() -> None:
    reader = client(read=[ACCOUNT])
    statements = reader.call_tool("read_statements", level="read").data["statements"]
    assert statements == [
        {
            "account": "E2E Orders",
            "custodian": "Alpaca",
            "as_of": "2026-10-12",
            "state": "Closed (Current)",
        }
    ]
    history = reader.call_tool("read_account_activities", level="read").data["activities"]
    assert [(a["kind"], a["amount"]) for a in history] == [("dividend", "0.75")]
    stranger = client(read=["ACC-SOMEONE-ELSE"])
    assert stranger.call_tool("read_statements", level="read").data["statements"] == []
    assert stranger.call_tool("read_account_activities", level="read").data["activities"] == []
    assert stranger.get(STATEMENTS, "read").status == 200
    assert stranger.get(HISTORY, "read").status == 200


def test_only_a_deployment_admin_creates_an_account_at_link() -> None:
    refused = client().call_tool(
        "check_link_account",
        {"external_account_id": NUMBER, "intent": "create", "new_account_name": "New"},
        level="admin",
    )
    assert refused.outcome == "refused"
    allowed = client(deployment_admin=True).call_tool(
        "check_link_account",
        {"external_account_id": NUMBER, "intent": "create", "new_account_name": "New"},
        level="admin",
    )
    assert allowed.status == 200 and allowed.outcome != "refused"


def test_a_link_to_an_account_the_key_does_not_reach_is_refused() -> None:
    answer = client().call_tool(
        "check_link_account",
        {"external_account_id": "SYN-OTHER", "account_id": ACCOUNT},
        level="admin",
    )
    assert answer.outcome == "refused"
