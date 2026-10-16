"""The custody suite (contract v23): every case Alpaca presents, from Alpaca's
own shapes.

Each case is mapped to what Alpaca's Trading API answers for it -- its
account (`/v2/account`), positions (`/v2/positions`) and activities
(`/v2/account/activities`), as JSON in Alpaca's own shapes, served by a
stand-in for its transport (`Answers`) -- and run through this plugin's own
client (broker.py), reads and conversion (custody.py) and pages
(custody_pages.py), against the runner's recorder, as a read does. Only the
sidecar and the transport are stood in for. SIMULATED ONLY: nothing reaches
Alpaca.

Every case passes but those Alpaca never presents (`NOT_PRESENTED`), each a
closed-list or `about` case, with why in Alpaca's terms: a connection late
by design, holdings not served, a cash or a retirement account (its account
states no type; its margin classification is margin, always), a money
market fund counted in cash, a currency not stated (Alpaca states every
amount's currency by its account's, as the product owner read it,
2026-10-13), a reinvestment, a plan's own fund code, and a calendar's days.
"""

from __future__ import annotations

import asyncio
import json
import tempfile
import urllib.parse
import zlib
from collections.abc import Awaitable, Callable, Mapping
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

import meridian
import pytest
from meridian.operations import as_decimal
from meridian.plugin.v1 import operations_pb2 as ops
from meridian.suites import ANSWERED_BY_SDK, Recorder, run, suite
from meridian.testing import PageClient

from alpaca import custody, page
from alpaca.broker import PAPER_BASE, Broker
from alpaca.custody import Syncer
from alpaca.kinds import Store
from alpaca.page import State, pages
from alpaca.settings import Config
from alpaca.vendor import RateLimiter, Response

Producer = Callable[[Recorder], Awaitable[None]]
Json = dict[str, Any]

NUMBER = "SYN-ALP-2002"
#: Monday 2026-10-12, 15:00 New York: the read's day.
NOW = datetime(2026, 10, 12, 19, 0, tzinfo=UTC)
TODAY = "2026-10-12"
TOMORROW = "2026-10-13"


def account(**changes: Any) -> Json:
    """Alpaca's account object, as `/v2/account` answers it."""
    base: Json = {
        "id": "00000000-0000-4000-8000-0000000a2002",
        "account_number": NUMBER,
        "status": "ACTIVE",
        "crypto_status": "ACTIVE",
        "currency": "USD",
        "cash": "0",
        "buying_power": "0",
        "multiplier": "2",
        "trading_blocked": False,
        "account_blocked": False,
        "transfers_blocked": False,
        "created_at": "2024-10-07T14:11:09.000000Z",
    }
    return {**base, **changes}


def position(symbol: str, qty: str, **changes: Any) -> Json:
    """One of Alpaca's position objects, as `/v2/positions` lists it."""
    base: Json = {
        "asset_id": "00000000-0000-4000-8000-0000000b0001",
        "symbol": symbol,
        "exchange": "NASDAQ",
        "asset_class": "us_equity",
        "asset_marginable": True,
        "qty": qty,
        "qty_available": qty,
        "avg_entry_price": "212.40",
        "side": "long",
        "market_value": str(Decimal(qty) * Decimal("227.41")),
        "cost_basis": str(Decimal(qty) * Decimal("212.40")),
        "current_price": "227.41",
    }
    return {**base, **changes}


def uid(*parts: str) -> str:
    """An ID in Alpaca's activity IDs' shape, the same for the same parts."""
    return f"00000000-0000-4000-8000-{zlib.crc32('|'.join(parts).encode()):012d}"


def fill(symbol: str, qty: str, side: str, price: str, at: str = f"{TODAY}T14:31:01Z") -> Json:
    """A `FILL`, as `/v2/account/activities` lists one."""
    return {
        "id": f"20261012143101000::{uid(symbol, side)}",
        "activity_type": "FILL",
        "transaction_time": at,
        "type": "fill",
        "price": price,
        "qty": qty,
        "side": side,
        "symbol": symbol,
        "leaves_qty": "0",
        "order_id": "00000000-0000-4000-8000-0000000c0001",
        "cum_qty": qty,
        "order_status": "filled",
    }


def nta(kind: str, symbol: str = "", **changes: Any) -> Json:
    """A non-trade activity of Alpaca's type `kind`, as its activities list
    one."""
    base: Json = {
        "id": f"20260915000000000::{uid(kind, symbol)}",
        "activity_type": kind,
        "date": "2026-09-15",
        "net_amount": "0",
        "description": f"{kind} {symbol}".strip(),
        "status": "executed",
    }
    if symbol:
        base["symbol"] = symbol
    return {**base, **changes}


class Answers:
    """A stand-in for Alpaca's transport (broker.Send), answering as its
    Trading API does, from JSON in its shapes; `status` answers every
    request with that status instead, and `lost` never answers."""

    def __init__(
        self,
        account_: Json,
        positions: list[Json],
        activities: list[Json],
        status: int = 200,
        lost: bool = False,
    ) -> None:
        self.account, self.positions, self.activities = account_, positions, activities
        self.status, self.lost = status, lost

    def __call__(
        self, method: str, url: str, headers: Mapping[str, str], body: bytes | None
    ) -> Response:
        if self.lost:
            raise ConnectionResetError("no answer")
        if self.status != 200:
            said = {"code": 40110000, "message": "request is not authorized"}
            return Response(self.status, {}, json.dumps(said).encode())
        path = urllib.parse.urlparse(url).path
        value: Any = {
            "/v2/account": self.account,
            "/v2/positions": self.positions,
            "/v2/account/activities": self.activities,
        }[path]
        return Response(200, {"content-type": "application/json"}, json.dumps(value).encode())


class Read:
    """A Syncer over the recorder and a stand-in transport, the account
    linked; a read is `once()`."""

    def __init__(self, recorder: Recorder, answers: Answers) -> None:
        self.answers = answers
        self.broker = Broker(
            Store(Path(tempfile.mkdtemp())),
            None,
            PAPER_BASE,
            send=answers,
            limiter=RateLimiter(per_minute=6_000_000, sleep=lambda _: None),
            now=lambda: NOW,
            sleep=lambda _: None,
            keyless=True,
            synthetic=True,
        )
        self.syncer = Syncer(
            recorder,
            lambda: self.broker,
            paper=lambda: False,
            stale_after=lambda: timedelta(hours=36),
        )

    async def once(self) -> None:
        await self.syncer.run_once()


def reading(
    account_: Json | None = None,
    positions: list[Json] | None = None,
    activities: list[Json] | None = None,
    settles: str = "",
) -> Producer:
    """One read of an account holding `positions`, its activities these; the
    date read answering each fill's settlement date `settles` where given."""

    async def produce(recorder: Recorder) -> None:
        if settles:
            recorder.answer("ComputeDate", lambda _: settled(settles))
        await Read(
            recorder, Answers(account_ or account(), positions or [], activities or [])
        ).once()

    return produce


def settled(day: str) -> ops.ComputeDateResult:
    return ops.ComputeDateResult(
        date=day,
        outcome=ops.DATE_OUTCOME_COMPUTED,
        basis=ops.DateBasis(rule=ops.DateRuleRef(subject="XNAS", version=1)),
        business_day=True,
    )


def then_answering(**failure: Any) -> Producer:
    """A read Alpaca answered, then one it did not: refused (`status`) or
    lost, past Stale after."""

    async def produce(recorder: Recorder) -> None:
        read = Read(recorder, Answers(account(), [], []))
        await read.once()
        read.syncer.status.last_good = NOW - timedelta(days=3)
        read.answers.status = failure.get("status", 200)
        read.answers.lost = failure.get("lost", False)
        await read.once()

    return produce


def ambiguous(recorder: Recorder) -> Awaitable[None]:
    recorder.answer(
        "ResolveIdentifier",
        lambda params: ops.ResolveIdentifierResult(
            found=False, miss_reason=ops.MISS_REASON_AMBIGUOUS
        ),
    )
    return reading(positions=[position("ABC", "3")])(recorder)


def thin(recorder: Recorder) -> Awaitable[None]:
    """A holding Alpaca names by its symbol alone, stating no description of
    the security, which the platform does not know (contract v24): recorded
    unresolved, naming what it was given, never guessed."""
    recorder.answer(
        "ResolveIdentifier",
        lambda _: ops.ResolveIdentifierResult(found=False, miss_reason=ops.MISS_REASON_NOT_FOUND),
    )
    return reading(positions=[position("ZZTOP", "5")])(recorder)


def paged(**caller: Any) -> Callable[[Recorder], PageClient]:
    def client(recorder: Recorder) -> PageClient:
        syncer = Syncer(
            recorder, lambda: None, paper=lambda: False, stale_after=lambda: timedelta(hours=36)
        )
        syncer.status.number = NUMBER
        page.use(State(config=Config(sample_data=True), syncer=syncer))
        return PageClient(pages, recorder, roles=("data", "custody", "connectivity"), **caller)

    return client


async def read_for_linking(recorder: Recorder) -> None:
    # The page client runs its own loop: off this one.
    answer = await asyncio.to_thread(paged()(recorder).get, "/links", "admin")
    assert answer.status == 200, answer.text


async def link_a_new_account(recorder: Recorder) -> None:
    """Account links' create and link, as a deployment admin's form sends it
    (the kit's account map): its custodian and type pre-filled."""
    answer = await asyncio.to_thread(
        paged(deployment_admin=True)(recorder).call_tool,
        "link_account",
        {"external_account_id": NUMBER, "intent": "create", "new_account_name": "Alpaca margin"},
        level="admin",
    )
    assert answer.status == 200, answer.json


#: The cases Alpaca never presents, each with why, in Alpaca's terms.
NOT_PRESENTED = {
    "sync-delayed-by-design": (
        "Alpaca answers each read with the account as it stands: nothing it serves is late "
        "by design"
    ),
    "sync-holdings-unavailable": "Alpaca's Trading API serves every account's positions",
    "account-kind-cash": (
        "Alpaca's account states no type: its margin classification (multiplier 1, 2 or 4) "
        "is a margin account's, never a cash account's"
    ),
    "account-kind-retirement": (
        "Alpaca's account states no type: its Trading API states no retirement account"
    ),
    "cash-net-of-a-fund-counted-in-cash": (
        "Alpaca lists no money market fund it counts in the account's cash"
    ),
    "fund-larger-than-cash": "Alpaca lists no money market fund it counts in the account's cash",
    "currency-not-stated": (
        "Alpaca states the currency of every holding and amount by its account's (`currency`)"
    ),
    "activity-reinvestment": "Alpaca reinvests no income: it lists no reinvestment",
    "activity-plan-code-not-linked": (
        "Alpaca's activities name a security by its symbol, never by a plan's own fund code"
    ),
    "activity-plan-code-linked-by-a-person": (
        "Alpaca's activities name a security by its symbol, never by a plan's own fund code"
    ),
    "activity-re-resolved-when-a-plan-code-is-linked-later": (
        "Alpaca's activities name a security by its symbol, never by a plan's own fund code"
    ),
    "reads-a-calendar": (
        "asks the date read for each fill's settlement date (ComputeDate); it reads no "
        "calendar's days"
    ),
}

HELD = [position("AAPL", "12.5")]
PENDING = reading(
    account(cash="1523.45"),
    [position("AAPL", "30")],
    [fill("AAPL", "5", "buy", "227.41")],
    settles=TOMORROW,
)


def activity(*listed: Json) -> Producer:
    return reading(account(cash="1523.45"), HELD, list(listed))


PRODUCERS: dict[str, Producer] = {
    "sync-current": reading(),
    "sync-stale": then_answering(lost=True),
    "sync-needs-sign-in": then_answering(status=401),
    "sync-disabled": reading(account(status="ACCOUNT_CLOSED")),
    "account-kind-margin": reading(account(multiplier="2")),
    # A margin classification none of Alpaca's three: not known, as reported.
    "account-kind-not-known": reading(account(multiplier="3")),
    "read-accounts-for-linking": read_for_linking,
    "link-a-new-account": link_a_new_account,
    # The account's cash 0: the one row is the security's.
    "a-security-holding": reading(account(cash="0"), HELD),
    "records-a-thinly-described-holding-unresolved": thin,
    "institution-not-stated": reading(account(), HELD),
    "settled-quantity-not-stated": reading(account(), HELD),
    "pending-by-value-date": PENDING,
    "resolve-gives-the-currency-with-its-provenance": reading(
        account(),
        [position("AAPL261218C00250000", "2", asset_class="us_option", avg_entry_price="4.10")],
    ),
    "kind-not-converted-on-an-ambiguous-resolve": ambiguous,
    # Contract v14: each of Alpaca's activity types, converted to its kind.
    "activity-purchase": activity(fill("AAPL", "2.5", "buy", "234.50")),
    "activity-sale": activity(fill("AAPL", "2.5", "sell", "225.00")),
    "activity-dividend": activity(nta("DIV", "AAPL", net_amount="3.25", qty="12.5")),
    "activity-interest": activity(nta("INT", net_amount="0.42")),
    "activity-fee": activity(nta("FEE", net_amount="-0.03", description="TAF fee")),
    "activity-tax": activity(nta("DIVNRA", "AAPL", net_amount="-0.49")),
    "activity-split": activity(nta("SPLIT", "AAPL", qty="12.5")),
    "activity-corporate-action": activity(nta("MA", "AAPL", qty="-12.5")),
    "activity-transfer-in": activity(nta("ACATS", "AAPL", qty="10")),
    "activity-transfer-out": activity(nta("ACATS", "AAPL", qty="-10")),
    "activity-contribution": activity(nta("CSD", net_amount="5000.00")),
    "activity-withdrawal": activity(nta("CSW", net_amount="-500.00")),
    "activity-journal": activity(nta("JNLC", net_amount="-200.00")),
    "activity-kind-not-converted": activity(
        nta("OPEXP", "AAPL261218C00250000", qty="-1", description="Option expiration")
    ),
    "activity-signs": activity(fill("AAPL", "2.5", "sell", "225.00")),
    "activity-moves-no-cash": activity(nta("SPLIT", "AAPL", qty="12.5", net_amount="0")),
    "history-from-stated": reading(),
    # Contract v22: each fill's settlement date asked of the date read.
    "asks-for-a-settlement-date": PENDING,
}


def test_every_case_is_mapped_or_said_not_presented() -> None:
    every = {case.name for case in suite("custody").cases}
    # Contract v24: the two cases every suite holds, which the SDK answers.
    assert every == set(PRODUCERS) | set(NOT_PRESENTED) | set(ANSWERED_BY_SDK)
    assert not set(PRODUCERS) & set(NOT_PRESENTED)


def test_every_case_of_the_custody_suite_passes_but_those_alpaca_never_presents() -> None:
    report = run("custody", PRODUCERS, not_presented=NOT_PRESENTED, instance_id="alpaca")
    assert report.passed, report.failures
    assert report.not_presented == NOT_PRESENTED
    assert set(report.passed_cases) == set(PRODUCERS) | set(ANSWERED_BY_SDK)


def recorded(producer: Producer) -> Recorder:
    recorder = Recorder("alpaca")

    async def produce() -> None:
        await producer(recorder)

    asyncio.run(produce())
    return recorder


def test_the_settled_quantity_and_the_cash_are_less_what_is_pending() -> None:
    recorder = recorded(PENDING)
    rows = {
        str(row.instrument_id): (as_decimal(row.quantity), as_decimal(row.settle_date_quantity))
        for row in recorder.on("RecordHolding")
    }
    assert sorted(rows.values()) == [
        (Decimal("30"), Decimal("25")),
        (Decimal("1523.45"), Decimal("1523.45") + Decimal(5) * Decimal("227.41")),
    ]


def test_a_fills_amount_is_derived_and_says_its_rule() -> None:
    recorder = recorded(PRODUCERS["activity-signs"])
    (sent,) = recorder.on("RecordActivity")
    assert as_decimal(sent.activity.amount.amount) == Decimal("562.50")
    assert [(p.field, p.rule) for p in sent.activity.provenance] == [
        ("amount", custody.RULE_FILL_AMOUNT)
    ]


def test_needing_sign_in_says_the_rule_it_was_derived_by() -> None:
    recorder = recorded(PRODUCERS["sync-needs-sign-in"])
    sent = recorder.on("ReportSyncStatus")[-1]
    assert sent.state == meridian.SyncState.SYNC_STATE_NEEDS_SIGN_IN
    assert not sent.connection_healthy
    assert f'derived by the rule "{custody.RULE_SIGN_IN}"' in sent.status_detail


def test_every_amount_is_in_the_accounts_currency_with_no_provenance_for_it() -> None:
    recorder = recorded(PENDING)
    for row in recorder.on("RecordHolding"):
        assert row.market_value.currency_code == "USD"
        assert not [p for p in row.provenance if p.field.endswith("currency_code")]
    resolved = [r for r in recorder.on("ResolveIdentifier") if r.identifiers[0].scheme == "symbol"]
    assert resolved and all(r.stated_currency == "USD" and not r.currency for r in resolved)
    # Reported from the account's raw record, which states it, not the
    # position's (ResolveIdentifierRequest.provenance, contract v23).
    for r in resolved:
        (entry,) = r.provenance
        assert entry.field == "stated_currency" and entry.raw_record.key.startswith("responses/")
        assert entry.kind == meridian.ProvenanceKind.PROVENANCE_KIND_REPORTED


def test_a_read_after_the_first_asks_only_the_recent_activities() -> None:
    recorder = Recorder("alpaca")
    asked: list[str] = []
    answers = Answers(account(), HELD, [])
    read = Read(recorder, answers)

    def noting(method: str, url: str, headers: Mapping[str, str], body: bytes | None) -> Response:
        asked.append(url)
        return answers(method, url, headers, body)

    read.broker.send = noting
    asyncio.run(read.once())
    asyncio.run(read.once())
    activities = [url for url in asked if "/v2/account/activities" in url]
    assert "after=" not in activities[0] and "after=2026-10-02" in activities[1]


@pytest.mark.parametrize("multiplier", ["1", "2", "4"])
def test_every_margin_classification_is_margin(multiplier: str) -> None:
    kind, reported = custody.account_kind(account(multiplier=multiplier))
    assert kind == meridian.AccountKind.ACCOUNT_KIND_MARGIN and reported is None


def test_a_statement_waits_while_a_fill_has_no_settlement_date() -> None:
    """A pending quantity names its date: while the date read gives an
    unsettled fill none, the statement is withheld, and sent once it does."""
    withheld = recorded(
        reading(account(cash="1523.45"), [position("AAPL", "30")], [fill("AAPL", "5", "buy", "1")])
    )
    assert withheld.on("RecordHoldingsStatement") == [] and withheld.on("RecordHolding") == []
    assert recorded(PENDING).on("RecordHoldingsStatement")
