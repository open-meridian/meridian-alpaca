"""The custody role (contract v11, v14; W2.1 to W2.4, W2.8, W2.10), from
0.8.0: Alpaca's view of the account, which the deployment's operations
reconcile the book against, in SnapTrade 0.16.0's shape and the custody
template's (`meridian plugin new --role custody`).

Each read asks Alpaca's Trading API (broker.py) for the account
(`/v2/account`), its positions (`/v2/positions`) and its activities
(`/v2/account/activities`), keeps each answer as a raw record before
anything is converted from it, and reports, in the contract's words:

- **the account** the key reaches (W2.8), by its number, which is also the
  venue account code connectivity trades it under. Alpaca's account states
  no type: its `multiplier` is its margin classification, 1 (a limited
  margin account), 2 (Reg T margin) or 4 (pattern day trader), each a
  margin account, so its kind is margin; a multiplier none of those is not
  known, sent as reported (`alpaca:multiplier`).
- **its sync state** (W2.1): current where Alpaca answered; needs sign-in
  where Alpaca refused the key (401 or 403), derived by the rule "Alpaca
  refused the key": a person signs in at Alpaca and enters a new key before
  anything new is served; disabled where Alpaca says the account is closed,
  inactive or blocked, derived by its rule; stale where Alpaca has not
  answered for longer than Stale after, the last read standing, derived by
  its rule. Each rule is said in the detail, since the sync status carries
  no provenance. How far back the account's history reaches
  (`history_from`) is the day Alpaca says the account was created.
- **a statement** per read (W2.2, W2.3), as of the read's day in New York,
  naming Alpaca, which the account does not name, as its custodian, derived
  from the connection: a row per position, and the cash where it is not
  zero. Each position names its security by Alpaca's symbol, resolved
  stating what Alpaca says of it: its asset class where it converts
  (`us_option` a derivative, `crypto` a crypto asset; `us_equity` names
  stocks and ETFs alike, so no one class, and is kept as reported), its
  description where it gives one, and its currency. Its quantity and side,
  its market value and its average cost are as Alpaca states them.
- **the currency**: Alpaca states the currency of the account's amounts by
  the account's own `currency` (USD), as OANDA's v20 account does (the
  product owner's reading, 2026-10-13): every holding's and activity's
  amount is in it, read, not derived. A holding's resolve offers it with
  its provenance reported from the account's raw record
  (`ResolveIdentifierRequest.provenance`, contract v23), the record that
  states it, not the position's.
- **settled and pending** (the custody role's, contract v11): Alpaca states
  neither. Each of the account's fills traded on or before the as-of day is
  given its settlement date by the deployment's date read (ComputeDate), and
  one settling after the as-of day is a pending quantity on that date (on
  the cash too, the cost or proceeds), the date read asked as of the read.
  While it gives one of them no date, a pending quantity naming none, the
  statement is withheld, and read again on the next read. The settled
  quantity is the quantity less them, derived by that rule.
- **its activities** (W2.10, contract v14), as Alpaca lists them: a fill a
  purchase or a sale; `DIV` and its kinds a dividend, `INT` interest, `FEE`
  and `CFEE` a fee, a withholding (`DIVNRA`, `DIVTW`, `INTNRA`, `INTTW`)
  tax, `SPLIT` a split, a merger, spin-off or reorganisation a corporate
  action, `ACATS` securities moved in or out by the sign of their quantity,
  `CSD` and an incoming `ACATC` a contribution, `CSW` and an outgoing one a
  withdrawal, `JNL`, `JNLC` and `JNLS` a journal; any other type not known,
  as reported. A fill states its price and quantity, never the cash it
  moved: its amount is the quantity times the price, derived by that rule
  (Alpaca takes its regulatory fees as `FEE` activities of their own); a 0
  Alpaca writes where no amount applies is no amount. Its instrument is
  resolved by its symbol.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal, InvalidOperation
from typing import Any

import meridian
from meridian.edge import as_reported, derived, reported
from meridian.plugin.v1 import operations_pb2 as ops

from .broker import Broker, BrokerError, NoKey, Unanswered
from .trading import MARKET_ZONE, moment

log = logging.getLogger("alpaca.custody")

SOURCE = "alpaca"
ACTIVITY_SCHEME = "alpaca:activity-type"
ASSET_CLASS_SCHEME = "alpaca:asset-class"
MULTIPLIER_SCHEME = "alpaca:multiplier"
SYMBOL_SCHEME = "alpaca:symbol"

RULE_SIGN_IN = "Alpaca refused the key"
RULE_DISABLED = "Alpaca says the account is closed, inactive or blocked"
RULE_STALE = "Alpaca has not answered for longer than Stale after"
RULE_INSTITUTION = "the connection's broker: Alpaca"
RULE_SETTLED = (
    "the quantity less the account's fills not settled at the as-of date, each by the "
    "deployment's settlement date for it"
)
RULE_CASH_SETTLED = (
    "the cash less the cost, plus the proceeds, of the account's fills not settled at the "
    "as-of date"
)
RULE_FILL_AMOUNT = "the fill's quantity times its price: Alpaca charges no commission"

#: Alpaca's account statuses that say the account serves only what it holds.
DISABLED = frozenset({"ACCOUNT_CLOSED", "ACCOUNT_CLOSED_PENDING", "INACTIVE", "REJECTED"})
#: Alpaca's margin classifications (`multiplier`), every one a margin account.
MARGIN_MULTIPLIERS = frozenset({"1", "2", "4"})
#: How far back the fills that may still be unsettled are looked for.
UNSETTLED_DAYS = 10

_K = meridian.ActivityKind
KINDS: dict[str, meridian.ActivityKind] = {
    "DIV": _K.ACTIVITY_KIND_DIVIDEND,
    "DIVCGL": _K.ACTIVITY_KIND_DIVIDEND,
    "DIVCGS": _K.ACTIVITY_KIND_DIVIDEND,
    "DIVFT": _K.ACTIVITY_KIND_DIVIDEND,
    "DIVROC": _K.ACTIVITY_KIND_DIVIDEND,
    "DIVTXEX": _K.ACTIVITY_KIND_DIVIDEND,
    "INT": _K.ACTIVITY_KIND_INTEREST,
    "FEE": _K.ACTIVITY_KIND_FEE,
    "CFEE": _K.ACTIVITY_KIND_FEE,
    "DIVFEE": _K.ACTIVITY_KIND_FEE,
    "DIVNRA": _K.ACTIVITY_KIND_TAX,
    "DIVTW": _K.ACTIVITY_KIND_TAX,
    "INTNRA": _K.ACTIVITY_KIND_TAX,
    "INTTW": _K.ACTIVITY_KIND_TAX,
    "SPLIT": _K.ACTIVITY_KIND_SPLIT,
    "MA": _K.ACTIVITY_KIND_CORPORATE_ACTION,
    "NC": _K.ACTIVITY_KIND_CORPORATE_ACTION,
    "SPIN": _K.ACTIVITY_KIND_CORPORATE_ACTION,
    "REORG": _K.ACTIVITY_KIND_CORPORATE_ACTION,
    "CSD": _K.ACTIVITY_KIND_CONTRIBUTION,
    "CSW": _K.ACTIVITY_KIND_WITHDRAWAL,
    "JNL": _K.ACTIVITY_KIND_JOURNAL,
    "JNLC": _K.ACTIVITY_KIND_JOURNAL,
    "JNLS": _K.ACTIVITY_KIND_JOURNAL,
}
#: The activity kinds whose quantity is units moved, not cash.
UNITS_MOVED = frozenset(
    {
        _K.ACTIVITY_KIND_SPLIT,
        _K.ACTIVITY_KIND_CORPORATE_ACTION,
        _K.ACTIVITY_KIND_TRANSFER_IN,
        _K.ACTIVITY_KIND_TRANSFER_OUT,
        _K.ACTIVITY_KIND_UNSPECIFIED,
    }
)
ASSET_CLASSES = {"us_option": "derivative", "crypto": "crypto_asset"}

Json = dict[str, Any]


def _ns(at: datetime) -> int:
    return int(at.timestamp()) * 1_000_000_000 + at.microsecond * 1000


def day_ns(day: date) -> int:
    return _ns(datetime.combine(day, time(), UTC))


def stated(value: Any) -> Decimal | None:
    """A number Alpaca stated, exactly; None where it gave none."""
    if value is None or value == "" or isinstance(value, bool):
        return None
    try:
        return Decimal(str(value))
    except InvalidOperation:
        return None


def nonzero(value: Any) -> Decimal | None:
    number = stated(value)
    return None if number is None or number == 0 else number


def currency_of(account: Json | None) -> str:
    """The account's currency, which Alpaca states every amount in."""
    return str((account or {}).get("currency") or "USD")


# ── What a read is ───────────────────────────────────────────────────────


@dataclass
class Read:
    """One read of Alpaca: what it answered, and the raw records it is in.
    `refused` where Alpaca refused the key; `failed` where it did not answer
    otherwise."""

    at: datetime
    account: Json | None = None
    positions: list[Json] = field(default_factory=list)
    activities: list[Json] = field(default_factory=list)
    account_key: str = ""
    positions_key: str = ""
    activity_keys: dict[str, str] = field(default_factory=dict)
    refused: str = ""
    failed: str = ""


def read(broker: Broker, after: str = "") -> Read:
    """One read of the account, its positions and its activities, each page
    kept as its raw record."""
    done = Read(at=broker.now())
    try:
        account = broker.account()
        done.account, done.account_key = account.value, account.raw_key
        positions = broker.positions()
        done.positions = positions.value if isinstance(positions.value, list) else []
        done.positions_key = positions.raw_key
        token = ""
        for _ in range(50):
            page = broker.activities(after=after, page_token=token)
            listed = page.value if isinstance(page.value, list) else []
            for activity in listed:
                if isinstance(activity, dict) and activity.get("id"):
                    done.activities.append(activity)
                    done.activity_keys[str(activity["id"])] = page.raw_key
            if len(listed) < 100:
                break
            token = str(listed[-1].get("id") or "")
    except BrokerError as refused:
        if refused.status in (401, 403):
            done.refused = f"{refused.status} {refused.text}".strip()
        else:
            done.failed = str(refused)
    except (Unanswered, NoKey) as lost:
        done.failed = str(lost)
    return done


# ── Its conversion ──────────────────────────────────────────────────────


@dataclass(frozen=True)
class Converted:
    """One of Alpaca's activities, converted, but for its instrument."""

    external_activity_id: str
    type: str
    kind: meridian.ActivityKind
    symbol: str
    trade_date: str
    units: Decimal | None
    price: meridian.Money | None
    amount: meridian.Money | None
    description: str
    amount_derived: bool = False

    @property
    def kind_as_reported(self) -> meridian.AsReported | None:
        if self.kind != _K.ACTIVITY_KIND_UNSPECIFIED:
            return None
        return as_reported(ACTIVITY_SCHEME, self.type or "(none)", self.description or None)


def convert(given: Json, currency: str) -> Converted | str:
    """One of Alpaca's activities converted, or why it cannot be sent."""
    activity_id = str(given.get("id") or "")
    if not activity_id:
        return "Alpaca gives it no identifier the street can key it by"
    kind_said = str(given.get("activity_type") or "").upper()
    symbol = str(given.get("symbol") or "")
    if kind_said == "FILL":
        at = moment(given.get("transaction_time"))
        if at is None:
            return f"fill {activity_id} states no time"
        qty, price = stated(given.get("qty")), stated(given.get("price"))
        side = str(given.get("side") or "")
        if qty is None or price is None or side not in ("buy", "sell", "sell_short"):
            return f"fill {activity_id} states no quantity, price or side"
        buys = side == "buy"
        units = abs(qty) if buys else -abs(qty)
        return Converted(
            external_activity_id=activity_id,
            type=kind_said,
            kind=_K.ACTIVITY_KIND_PURCHASE if buys else _K.ACTIVITY_KIND_SALE,
            symbol=symbol,
            trade_date=at.astimezone(MARKET_ZONE).date().isoformat(),
            units=units,
            price=meridian.Money(price, currency),
            amount=meridian.Money(-units * price, currency),
            description=f"{side} {qty} {symbol}",
            amount_derived=True,
        )
    day = str(given.get("date") or "")[:10]
    try:
        date.fromisoformat(day)
    except ValueError:
        return f"activity {activity_id} states no date"
    amount = nonzero(given.get("net_amount"))
    qty = nonzero(given.get("qty"))
    kind = KINDS.get(kind_said, _K.ACTIVITY_KIND_UNSPECIFIED)
    if kind_said == "ACATS" and qty is not None:
        kind = _K.ACTIVITY_KIND_TRANSFER_IN if qty > 0 else _K.ACTIVITY_KIND_TRANSFER_OUT
    elif kind_said == "ACATC" and amount is not None:
        kind = _K.ACTIVITY_KIND_CONTRIBUTION if amount > 0 else _K.ACTIVITY_KIND_WITHDRAWAL
    return Converted(
        external_activity_id=activity_id,
        type=kind_said,
        kind=kind,
        symbol=symbol,
        trade_date=day,
        units=qty if kind in UNITS_MOVED else None,
        price=None,
        amount=None if amount is None else meridian.Money(amount, currency),
        description=str(given.get("description") or "")[:200],
    )


@dataclass(frozen=True)
class Freshness:
    state: meridian.SyncState
    healthy: bool
    detail: str


def freshness(done: Read, last_good: datetime | None, stale_after: timedelta) -> Freshness | None:
    """The account's sync state from a read, or None where the read says
    nothing new (Alpaca did not answer, within Stale after of its last)."""
    if done.refused:
        return Freshness(
            meridian.SyncState.SYNC_STATE_NEEDS_SIGN_IN,
            False,
            f'Alpaca refused the key ({done.refused}); derived by the rule "{RULE_SIGN_IN}"',
        )
    if done.failed:
        if last_good is not None and done.at - last_good > stale_after:
            return Freshness(
                meridian.SyncState.SYNC_STATE_STALE,
                False,
                f"Alpaca last answered {last_good.isoformat(timespec='seconds')} "
                f'({done.failed}); derived by the rule "{RULE_STALE}"',
            )
        return None
    account = done.account or {}
    status = str(account.get("status") or "")
    if status in DISABLED or account.get("account_blocked") is True:
        said = status or "account_blocked"
        return Freshness(
            meridian.SyncState.SYNC_STATE_DISABLED,
            False,
            f'Alpaca says {said}; derived by the rule "{RULE_DISABLED}"',
        )
    return Freshness(meridian.SyncState.SYNC_STATE_CURRENT, True, "Alpaca answered")


def account_kind(account: Json) -> tuple[meridian.AccountKind, meridian.AsReported | None]:
    """The account's kind from its margin classification, or not known as
    reported."""
    multiplier = str(account.get("multiplier") or "")
    if multiplier in MARGIN_MULTIPLIERS:
        return meridian.AccountKind.ACCOUNT_KIND_MARGIN, None
    return meridian.AccountKind.ACCOUNT_KIND_UNSPECIFIED, as_reported(
        MULTIPLIER_SCHEME,
        multiplier or "(none)",
        f"Alpaca's margin classification {multiplier or '(not stated)'}",
    )


def external_account(account: Json, paper: bool) -> meridian.ExternalAccount:
    number = str(account.get("account_number") or "")
    kind, reported = account_kind(account)
    return meridian.ExternalAccount(
        external_account_id=number,
        name=f"Alpaca {'paper' if paper else 'live'} {number}",
        account_kind=kind,
        account_kind_as_reported=reported,
    )


def institution(paper: bool) -> str:
    return "Alpaca (paper)" if paper else "Alpaca"


# ── Sending ──────────────────────────────────────────────────────────────


class Unsettled(Exception):
    """A fill the date read gives no settlement date yet: the statement is
    withheld, since a pending quantity names its date."""


@dataclass
class Recorded:
    """What one read came to, for the pages."""

    statement: str = ""
    rows: int = 0
    activities: int = 0
    stopped: str = ""
    unlinked: bool = False


class Custody:
    """Sends what a read of Alpaca says through the SDK's typed operations."""

    def __init__(self, plugin: Any, paper: bool = False) -> None:
        self._plugin = plugin
        self.paper = paper
        #: The raw record of the account this read answered, which states
        #: the currency of its amounts: a resolve's currency is reported from
        #: it, not from the record the security came from.
        self.currency_record = ""
        #: Each symbol resolved in this read, by symbol.
        self._resolved: dict[str, str] = {}

    async def accounts(self, done: Read) -> None:
        if done.account:
            await self._plugin.report_external_accounts(
                accounts=[external_account(done.account, self.paper)]
            )

    async def sync(self, number: str, fresh: Freshness, done: Read, history_from: str = "") -> None:
        last = _ns(done.at) if fresh.healthy else 0
        await self._plugin.report_sync_status(
            source=SOURCE,
            external_account_id=number,
            state=fresh.state,
            connection_healthy=fresh.healthy,
            status_detail=fresh.detail[:400],
            observed_at_ns=_ns(done.at),
            last_synced_at_ns=last,
            holdings_as_of_ns=last,
            history_from=history_from,
        )

    async def resolve(
        self,
        symbol: str,
        as_of_ns: int,
        currency: str,
        asset_class: str = "",
        description: str = "",
    ) -> str:
        """The deployment's record for a symbol, stating what Alpaca says of
        it; "" where none, or more than one, record matches, the miss
        reported."""
        if symbol in self._resolved:
            return self._resolved[symbol]
        identifiers = [meridian.Identifier(scheme="symbol", value=symbol, source=SOURCE)]
        converted = ASSET_CLASSES.get(asset_class, "")
        found = await self._plugin.resolve_identifier(
            identifiers=identifiers,
            as_of_ns=as_of_ns,
            stated_asset_class=converted or None,
            stated_currency=currency,
            provenance=(
                [reported("stated_currency", self._plugin.raw_record(self.currency_record))]
                if self.currency_record
                else []
            ),
            stated_description=description,
        )
        instrument = str(found.instrument_id or "") if found.found else ""
        if not instrument:
            await self._plugin.report_missing_instrument(
                source=SOURCE,
                identifiers=identifiers,
                as_of_ns=as_of_ns,
                reason=found.miss_reason or ops.MISS_REASON_NOT_FOUND,
                asset_class=converted or None,
                asset_class_as_reported=(
                    as_reported(ASSET_CLASS_SCHEME, asset_class)
                    if asset_class and not converted
                    else None
                ),
                observed_at_ns=as_of_ns,
            )
        self._resolved[symbol] = instrument
        return instrument

    async def _pending(
        self, done: Read, as_of: date, currency: str
    ) -> tuple[dict[str, list[tuple[str, Decimal]]], list[tuple[str, Decimal]]]:
        """Each symbol's fills not settled at the as-of date, by their value
        dates from the date read ("" where it gives none), and the cash's."""
        securities: dict[str, list[tuple[str, Decimal]]] = {}
        cash: list[tuple[str, Decimal]] = []
        for given in done.activities:
            if str(given.get("activity_type") or "").upper() != "FILL":
                continue
            converted = convert(given, currency)
            if isinstance(converted, str) or converted.units is None or converted.price is None:
                continue
            traded = date.fromisoformat(converted.trade_date)
            if traded > as_of or (as_of - traded).days > UNSETTLED_DAYS:
                continue
            instrument = await self.resolve(converted.symbol, day_ns(traded), currency)
            value_date = ""
            if instrument:
                answer = await self._plugin.compute_date(
                    settlement=ops.SettlementDateInputs(
                        instrument_id=instrument, trade_date=converted.trade_date
                    ),
                    as_of_ns=_ns(done.at),
                )
                if answer.outcome == ops.DATE_OUTCOME_COMPUTED and answer.date:
                    value_date = str(answer.date)
            if not value_date:
                raise Unsettled(f"no settlement date for a fill of {converted.symbol} on {traded}")
            if date.fromisoformat(value_date) <= as_of:
                continue
            units = converted.units
            securities.setdefault(converted.symbol, []).append((value_date, units))
            cash.append((value_date, -units * converted.price.amount))
        return securities, cash

    async def statement(self, done: Read, number: str) -> Recorded:
        """W2.2 to W2.4: the account's positions and its cash as of the
        read's day, each row with its raw record and the provenance of every
        value this plugin closed."""
        outcome = Recorded()
        account = done.account or {}
        currency = currency_of(account)
        as_of = done.at.astimezone(MARKET_ZONE).date()
        as_of_ns = day_ns(as_of)
        rows: list[dict[str, Any]] = []
        try:
            pending, cash_pending = await self._pending(done, as_of, currency)
        except Unsettled as waiting:
            outcome.stopped = str(waiting)
            log.info("the statement of %s waits: %s", number, waiting)
            return outcome
        for index, position in enumerate(done.positions):
            symbol = str(position.get("symbol") or "")
            qty = stated(position.get("qty"))
            if not symbol or qty is None or qty == 0:
                continue
            side = str(position.get("side") or ("short" if qty < 0 else "long"))
            quantity = abs(qty) if side == "long" else -abs(qty)
            moves = pending.get(symbol, [])
            settled = quantity - sum((units for _, units in moves), Decimal(0))
            instrument = await self.resolve(
                symbol,
                as_of_ns,
                currency,
                str(position.get("asset_class") or ""),
                str(position.get("name") or ""),
            )
            value = stated(position.get("market_value"))
            average = stated(position.get("avg_entry_price"))
            rows.append(
                {
                    **(
                        {"instrument_id": instrument}
                        if instrument
                        else {
                            "unresolved_identifiers": [
                                meridian.Identifier(scheme="symbol", value=symbol, source=SOURCE)
                            ]
                        }
                    ),
                    "quantity": quantity,
                    "side": (
                        meridian.HoldingSide.HOLDING_SIDE_LONG
                        if side == "long"
                        else meridian.HoldingSide.HOLDING_SIDE_SHORT
                    ),
                    "settle_date_quantity": settled,
                    "pending": [
                        meridian.ReportedPending(value_date=day, quantity=units)
                        for day, units in moves
                    ],
                    "market_value": None if value is None else meridian.Money(value, currency),
                    "average_cost": (
                        None if average is None else meridian.Money(average, currency)
                    ),
                    "raw_record": self._plugin.raw_record(f"{done.positions_key}#{index}"),
                    "provenance": [derived("settle_date_quantity", RULE_SETTLED)],
                }
            )
        cash = nonzero(account.get("cash"))
        if cash is not None:
            settled_cash = cash - sum((amount for _, amount in cash_pending), Decimal(0))
            found = await self._plugin.resolve_identifier(
                identifiers=[meridian.Identifier(scheme="iso4217", value=currency)],
                as_of_ns=as_of_ns,
            )
            rows.append(
                {
                    "instrument_id": str(found.instrument_id or ""),
                    "quantity": cash,
                    "side": meridian.HoldingSide.HOLDING_SIDE_LONG,
                    "settle_date_quantity": settled_cash,
                    "pending": [
                        meridian.ReportedPending(value_date=day, quantity=amount)
                        for day, amount in cash_pending
                    ],
                    "market_value": meridian.Money(cash, currency),
                    "raw_record": self._plugin.raw_record(f"{done.account_key}#cash"),
                    "provenance": [derived("settle_date_quantity", RULE_CASH_SETTLED)],
                }
            )
        try:
            opened = await self._plugin.record_holdings_statement(
                source=SOURCE,
                external_statement_id=f"{number}:{done.at.strftime('%Y%m%dT%H%M%S%fZ')}",
                external_account_id=number,
                institution=institution(self.paper),
                as_of_date=as_of.isoformat(),
                read_at_ns=_ns(done.at),
                expected_rows=len(rows),
                raw_record=self._plugin.raw_record(done.account_key),
                provenance=[derived("institution", RULE_INSTITUTION)],
            )
            outcome.statement, outcome.rows = opened.statement_id, len(rows)
            if opened.already_recorded:
                return outcome
            for row in rows:
                await self._plugin.record_holding(
                    statement_id=opened.statement_id, external_account_id=number, **row
                )
        except meridian.MeridianError as refused:
            outcome.stopped = str(refused)
            outcome.unlinked = isinstance(refused, meridian.NotLinked)
            log.info("the statement of %s stopped: %s", number, refused)
        return outcome

    async def activities(
        self, done: Read, number: str, skip: Callable[[str], bool] = lambda _: False
    ) -> tuple[int, list[tuple[Converted, str]]]:
        """W2.10: each activity, with the raw record it is in, sent to the
        street, one per call; what was sent, for History."""
        currency = currency_of(done.account)
        sent: list[tuple[Converted, str]] = []
        for given in done.activities:
            converted = convert(given, currency)
            if isinstance(converted, str):
                log.warning("%s: %s", number, converted)
                continue
            if skip(converted.external_activity_id):
                continue
            as_of_ns = day_ns(date.fromisoformat(converted.trade_date))
            instrument = (
                await self.resolve(converted.symbol, as_of_ns, currency) if converted.symbol else ""
            )
            key = done.activity_keys.get(converted.external_activity_id, "")
            try:
                await self._plugin.record_activity(
                    external_account_id=number,
                    source=SOURCE,
                    activity=meridian.CustodialActivity(
                        external_activity_id=converted.external_activity_id,
                        kind=converted.kind,
                        kind_as_reported=converted.kind_as_reported,
                        trade_date=converted.trade_date,
                        units=converted.units,
                        price=converted.price,
                        amount=converted.amount,
                        description=converted.description,
                        instrument_id=instrument,
                        instrument_as_reported=(
                            as_reported(SYMBOL_SCHEME, converted.symbol)
                            if converted.symbol and not instrument
                            else None
                        ),
                        raw_record=self._plugin.raw_record(
                            f"{key}#{converted.external_activity_id}"
                        ),
                        provenance=(
                            [derived("amount", RULE_FILL_AMOUNT)]
                            if converted.amount_derived
                            else []
                        ),
                    ),
                )
            except meridian.NotLinked as refused:
                log.info("the activities of %s stopped: %s", number, refused)
                break
            except (meridian.MeridianError, ValueError) as refused:
                log.warning(
                    "activity %s was not recorded: %s", converted.external_activity_id, refused
                )
                continue
            sent.append((converted, instrument))
        return len(sent), sent


# ── The schedule ─────────────────────────────────────────────────────────


@dataclass
class Status:
    """What the pages show of custody: never a key."""

    number: str = ""
    state: str = "Not read yet"
    detail: str = ""
    last_read: datetime | None = None
    last_good: datetime | None = None
    statement_as_of: str = ""
    statement_state: str = ""
    activities: list[tuple[Converted, str]] = field(default_factory=list)
    error: str = ""


STATES = {
    meridian.SyncState.SYNC_STATE_CURRENT: "Current",
    meridian.SyncState.SYNC_STATE_STALE: "Stale",
    meridian.SyncState.SYNC_STATE_NEEDS_SIGN_IN: "Needs sign-in",
    meridian.SyncState.SYNC_STATE_DISABLED: "Disabled",
}


class Syncer:
    """Reads Alpaca each Read every, and reports what it said."""

    def __init__(
        self,
        plugin: Any,
        broker: Callable[[], Broker | None],
        *,
        paper: Callable[[], bool],
        stale_after: Callable[[], timedelta],
        linked: Callable[[str], bool] = lambda _: True,
    ) -> None:
        self._plugin = plugin
        self._broker = broker
        self._paper = paper
        self._stale_after = stale_after
        self._linked = linked
        self.status = Status()
        self._recorded: set[str] = set()
        self._backfilled: set[str] = set()

    async def run_once(self) -> None:
        broker = self._broker()
        if broker is None:
            return
        number = self.status.number
        after = (
            ""
            if number not in self._backfilled
            else (broker.now() - timedelta(days=UNSETTLED_DAYS)).date().isoformat()
        )
        done = await asyncio.to_thread(read, broker, after)
        custody = Custody(self._plugin, self._paper())
        custody.currency_record = done.account_key
        if done.account:
            number = str(done.account.get("account_number") or number)
            self.status.number = number
        self.status.last_read = done.at
        fresh = freshness(done, self.status.last_good, self._stale_after())
        if done.account is not None and not done.failed and not done.refused:
            self.status.last_good = done.at
            await custody.accounts(done)
        if fresh is None:
            self.status.error = done.failed
            return
        self.status.state = STATES.get(fresh.state, "")
        self.status.detail = fresh.detail
        self.status.error = done.failed or done.refused
        if not number:
            return
        created = moment((done.account or {}).get("created_at"))
        await custody.sync(
            number,
            fresh,
            done,
            created.astimezone(MARKET_ZONE).date().isoformat() if created else "",
        )
        if not fresh.healthy or not self._linked(number):
            return
        recorded = await custody.statement(done, number)
        self.status.statement_as_of = done.at.astimezone(MARKET_ZONE).date().isoformat()
        self.status.statement_state = (
            "Not linked" if recorded.unlinked else ("Stopped" if recorded.stopped else "Closed")
        )
        count, sent = await custody.activities(done, number, lambda a: a in self._recorded)
        self._recorded.update(c.external_activity_id for c, _ in sent)
        self.status.activities = (sent + self.status.activities)[:200]
        self._backfilled.add(number)
        log.info("read %s: %d rows, %d activities", number, recorded.rows, count)
