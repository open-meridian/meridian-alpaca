"""The desk Alpaca's `make e2e` places its orders from (from 0.7.0), as
SnapTrade's e2e has one: a stand-in holding `portfolio`, `oms` and `ems` on
the Python SDK, `reporting` to name an instrument by its identifiers
(W3.1), and from 0.8.0 `operations`, to record the account's opening balance
from Alpaca's statement and reconcile the book against the next, run from
the plugin's own image (e2e/plugin.yaml). Each page answers
JSON so the harness's runner drives it (`page --level ... PATH`), acting for
the person the session's `Meridian-Caller` header names. SIMULATED ONLY: it
reaches the deployment through its sidecar and nothing else, and the orders
go to the plugin's simulated brokerage (src/alpaca/synthetic_broker.py).

  /propose?account=&instrument=&quantity=[&tif=][&key=]  ProposeOrder, a limit buy
  /release?order=                         ReleaseOrder (oms)
  /claim?order=                           the claimed working condition (ems)
  /place?order=&quantity=&code=           CreatePlacement at Alpaca's instance
  /cancel?placement=                      RequestPlacementChange, a cancel (ems)
  /replace?placement=[&limit=][&quantity=][&tif=]  RequestPlacementChange, a replace (ems)
  /map?account=&code=                     MapVenueAccount on Alpaca (ems's admin)
  /orders?account=                        ListOrders, a line each
  /resolve?figi=&symbol=                  ResolveIdentifier: the record named by its identifiers
  /settles?instrument=&date=              ComputeDate: whether the date read gives a settlement date
  /unmatched?account=                     ListOrders' executions on no placement (contract v23)
  /accept?executions=a,b                  AcceptExecution to a new order (contract v23, ems)
  /opening?account=&date=                 RecordOpeningBalance from the custodial positions (operations)
  /compare?account=[&record=1]            the book's positions against the custodial ones; with
                                          record, each difference recorded a break (operations)
"""

import asyncio
import http.server
import json
import threading
import urllib.parse
from decimal import Decimal

import meridian
from meridian import Interface, Page
from meridian.operations import as_decimal
from meridian.plugin.v1 import operations_pb2 as ops

PORT = 8000
LOOP = asyncio.new_event_loop()
CONNECTIVITY = "alpaca"
LIMIT = meridian.Money(Decimal("230.00"), "USD")
TIMES = {
    "day": ops.TIME_IN_FORCE_DAY,
    "gtc": ops.TIME_IN_FORCE_GOOD_TILL_CANCELLED,
    "gtd": ops.TIME_IN_FORCE_GOOD_TILL_DATE,
}


class Refusal(Exception):
    def __init__(self, said):
        super().__init__(json.dumps(said))
        self.said = said


def one(query, name, default=""):
    return query.get(name, [default])[0]


def order_json(order):
    q = order.status.quantities
    return {
        "order_id": order.order_id,
        "state": ops.OrderState.Name(order.status.state),
        "ordered": str(as_decimal(q.ordered)),
        "reported_filled": str(as_decimal(q.reported_filled)),
        "committed": str(as_decimal(q.committed)),
        "placements": [
            {
                "placement_id": p.placement_id,
                "state": ops.PlacementState.Name(p.status.state),
                "venue_order_id": p.venue_order_id,
                "venue_order_ids": list(p.venue_order_ids),
                "rejection": ops.RejectionReason.Name(p.rejection_reason),
                "quantity": str(as_decimal(p.quantity)),
                "limit": str(as_decimal(p.limit_price.amount)) if p.HasField("limit_price") else "",
                "changes": [
                    [
                        ops.PlacementChangeKind.Name(c.placement_change.kind),
                        ops.ChangeRequestOutcome.Name(c.outcome),
                    ]
                    for c in p.change_requests
                ],
            }
            for p in order.placements
        ],
    }


class Desk:
    def __init__(self, plugin):
        self.plugin = plugin

    async def call(self, operation, **params):
        try:
            return await getattr(self.plugin, operation)(**params)
        except meridian.MeridianError as failed:
            said = {"words": str(failed), "type": type(failed).__name__}
            raise Refusal({"refused": said}) from None

    async def answer(self, path, query, caller):
        order = one(query, "order")
        if path == "/propose":
            tif = TIMES[one(query, "tif", "day")]
            done = await self.call(
                "propose_order",
                account_id=one(query, "account"),
                instrument_id=one(query, "instrument"),
                side="buy",
                quantity=Decimal(one(query, "quantity")),
                order_type="limit",
                limit_price=LIMIT,
                time_in_force=tif,
                expire_date="2026-12-31" if tif == ops.TIME_IN_FORCE_GOOD_TILL_DATE else "",
                idempotency_key=one(query, "key"),
                acting_for=caller,
            )
            return order_json(done.order)
        if path == "/release":
            done = await self.call("release_order", order_id=order, acting_for=caller)
            return order_json(done.order)
        if path == "/claim":
            done = await self.call(
                "set_working_condition",
                order_id=order,
                condition=ops.ORDER_CONDITION_CLAIMED,
                set=True,
                acting_for=caller,
            )
            return order_json(done.order)
        if path == "/place":
            done = await self.call(
                "create_placement",
                order_id=order,
                connectivity_instance_id=CONNECTIVITY,
                venue_account_code=one(query, "code"),
                quantity=Decimal(one(query, "quantity")),
                limit_price=LIMIT,
                acting_for=caller,
            )
            return {
                "placement_id": done.placement.placement_id,
                "state": ops.PlacementState.Name(done.placement.status.state),
            }
        if path == "/cancel":
            done = await self.call(
                "request_placement_change",
                placement_id=one(query, "placement"),
                change=meridian.PlacementChange(kind=ops.PLACEMENT_CHANGE_KIND_CANCEL),
                reason="cancelled by the desk",
                acting_for=caller,
            )
            return {"change_request_id": done.change_request_id}
        if path == "/replace":
            tif = one(query, "tif")
            done = await self.call(
                "request_placement_change",
                placement_id=one(query, "placement"),
                change=meridian.PlacementChange(
                    kind=ops.PLACEMENT_CHANGE_KIND_REPLACE,
                    quantity=Decimal(one(query, "quantity")) if one(query, "quantity") else None,
                    limit_price=(
                        meridian.Money(Decimal(one(query, "limit")), "USD")
                        if one(query, "limit")
                        else None
                    ),
                    time_in_force=TIMES[tif] if tif else None,
                ),
                reason="replaced by the desk",
                acting_for=caller,
            )
            return {"change_request_id": done.change_request_id}
        if path == "/map":
            await self.call(
                "map_venue_account",
                connectivity_instance_id=CONNECTIVITY,
                venue_account_code=one(query, "code"),
                account_id=one(query, "account"),
                acting_for=caller,
            )
            return {"mapped": one(query, "code")}
        if path == "/resolve":
            found = await self.call(
                "resolve_identifier",
                identifiers=[
                    meridian.Identifier(scheme="figi", value=one(query, "figi")),
                    meridian.Identifier(
                        scheme="symbol", value=one(query, "symbol"), source="alpaca"
                    ),
                ],
            )
            return {"instrument_id": found.instrument_id}
        if path == "/settles":
            answer = await self.call(
                "compute_date",
                settlement=ops.SettlementDateInputs(
                    instrument_id=one(query, "instrument"), trade_date=one(query, "date")
                ),
            )
            return {"outcome": ops.DateOutcome.Name(answer.outcome), "date": answer.date}
        if path == "/orders":
            listed = await self.call("list_orders", account_id=one(query, "account"), page_size=500)
            return {"orders": [order_json(o) for o in listed.orders]}
        if path == "/unmatched":
            listed = await self.call(
                "list_orders",
                account_id=one(query, "account"),
                execution_state=ops.EXECUTION_STATE_UNMATCHED,
                page_size=500,
            )
            return {
                "executions": [
                    {
                        "execution_id": e.execution_id,
                        "venue_order_id": e.venue_order_id,
                        "quantity": str(as_decimal(e.quantity)),
                        "state": ops.ExecutionState.Name(e.state),
                    }
                    for e in listed.executions
                    if not e.placement_id and e.state == ops.EXECUTION_STATE_UNMATCHED
                ]
            }
        if path == "/accept":
            done = await self.call(
                "accept_execution",
                execution_ids=one(query, "executions").split(","),
                new_order=ops.NewOrderFromExecutions(note="Placed in the broker's own app"),
                note="Ours: the account holder's order at the broker",
                acting_for=caller,
            )
            return {"accepted": str(done).count("execution_id") or 1}
        if path == "/opening":
            return await self.opening(one(query, "account"), one(query, "date"), caller)
        if path == "/compare":
            return await self.compare(one(query, "account"), one(query, "record") == "1", caller)
        return {"page": "the desk"}

    async def custodial(self, account):
        """The account's custodial positions, by instrument and side."""
        held, cursor = {}, ""
        while True:
            page = await self.call(
                "list_custodial_positions", account_id=account, cursor=cursor, page_size=500
            )
            for p in page.positions:
                if not p.removed:
                    held[(p.instrument_id, p.side)] = p
            if not page.next_cursor:
                return held
            cursor = page.next_cursor

    async def booked(self, account):
        """The book's positions, by instrument and side, and its watermark."""
        held, cursor, watermark = {}, "", None
        while True:
            page = await self.call("list_positions", account_id=account, cursor=cursor)
            for p in page.positions:
                if not p.removed:
                    held[(p.instrument_id, p.side)] = p
            watermark = page.as_of
            if not page.next_cursor:
                return held, watermark
            cursor = page.next_cursor

    async def opening(self, account, day, caller):
        """The opening balance as of `day`, from the custodial positions as
        the custodian's latest statement stated them: a security with one
        lot at its average cost, the cash with none."""
        positions = []
        for (instrument, side), p in sorted((await self.custodial(account)).items()):
            quantity = as_decimal(p.quantity)
            lots = []
            if p.HasField("average_cost"):
                cost = meridian.Money(
                    as_decimal(p.average_cost.amount) * abs(quantity), p.average_cost.currency_code
                )
                lots = [
                    meridian.OpeningLot(
                        quantity=abs(quantity),
                        terms=meridian.LotTerms(
                            cost=cost, acquired_date=day, source=ops.LOT_SOURCE_OPENING_BALANCE
                        ),
                    )
                ]
            positions.append(
                meridian.OpeningPosition(
                    instrument_id=instrument,
                    side=side,
                    trade_date_quantity=quantity,
                    settled_quantity=as_decimal(p.settle_date_quantity),
                    lots=lots,
                )
            )
        await self.call(
            "record_opening_balance",
            account_id=account,
            as_of_date=day,
            sources=[
                meridian.OpeningSource(
                    kind=ops.OPENING_SOURCE_KIND_CUSTODIAN,
                    name="alpaca",
                    as_of_date=day,
                    basis=ops.POSITION_BASIS_TRADE_DATE,
                )
            ],
            positions=positions,
            reason="The e2e account's opening balance, from Alpaca's statement",
            idempotency_key=f"opening:{account}:{day}",
            acting_for=caller,
        )
        return {"opened": len(positions)}

    async def compare(self, account, record, caller):
        """Each position's trade-date and settled quantities, the book's
        against the custodian's; with `record`, each difference a break."""
        street = await self.custodial(account)
        book, watermark = await self.booked(account)
        differences = []
        for key in sorted(set(street) | set(book)):
            held, stated = book.get(key), street.get(key)
            pairs = [
                (
                    "trade_date_quantity",
                    as_decimal(held.trade_date_quantity) if held else Decimal(0),
                    as_decimal(stated.quantity) if stated else Decimal(0),
                ),
                (
                    "settled_quantity",
                    as_decimal(held.settled_quantity) if held else Decimal(0),
                    as_decimal(stated.settle_date_quantity) if stated else Decimal(0),
                ),
            ]
            for field, ours, theirs in pairs:
                if ours != theirs:
                    differences.append((key, field, ours, theirs))
        if record:
            for (instrument, side), field, ours, theirs in differences:
                await self.call(
                    "record_break",
                    account_id=account,
                    position=meridian.PositionKey(instrument_id=instrument, side=side),
                    category=(
                        ops.BREAK_CATEGORY_TRADE_DATE_QUANTITY
                        if field == "trade_date_quantity"
                        else ops.BREAK_CATEGORY_SETTLED_QUANTITY
                    ),
                    differences=[
                        meridian.BreakDifference(
                            field=field,
                            book=meridian.BreakValue(quantity=ours),
                            street=meridian.BreakValue(quantity=theirs),
                        )
                    ],
                    book_watermark=watermark,
                    idempotency_key=f"break:{account}:{instrument}:{side}:{field}:{theirs}",
                    acting_for=caller,
                )
        return {
            "compared": len(set(street) | set(book)),
            "street": len(street),
            "breaks": [
                {"instrument": i, "field": f, "book": str(o), "street": str(t)}
                for (i, _), f, o, t in differences
            ],
        }


def serve(desk):
    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            parsed = urllib.parse.urlparse(self.path)
            caller = self.headers.get("Meridian-Caller") or None
            future = asyncio.run_coroutine_threadsafe(
                desk.answer(parsed.path, urllib.parse.parse_qs(parsed.query), caller), LOOP
            )
            try:
                body = json.dumps(future.result(timeout=60), sort_keys=True)
            except Refusal as failed:
                body = json.dumps(failed.said, sort_keys=True)
            except Exception as failed:  # said on the page, for the run to quote
                body = json.dumps({"failed": f"{type(failed).__name__}: {failed}"})
            data = body.encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def log_message(self, *_args):
            pass

    http.server.ThreadingHTTPServer(("127.0.0.1", PORT), Handler).serve_forever()


async def main():
    roles = ["portfolio", "oms", "ems", "reporting", "operations"]
    plugin = await meridian.connect(
        # Contract v25: what the stand-in registers is its declaration.
        declaration=meridian.Declaration(interface=Interface(
            PORT, "Desk", pages=[Page("/", "Desk", levels=["write", "admin"], roles=roles)]
        ))
    )
    print(f"DESK registered as {plugin.identity.instance_id}", flush=True)
    threading.Thread(target=serve, args=(Desk(plugin),), daemon=True).start()
    while True:
        await asyncio.sleep(3600)


if __name__ == "__main__":
    asyncio.set_event_loop(LOOP)
    LOOP.run_until_complete(main())
