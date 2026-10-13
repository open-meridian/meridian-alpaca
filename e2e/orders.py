"""`make e2e`'s order path (contract v22; from 0.7.0), after the data steps,
in the same harness (e2e/run.py calls it): Alpaca holding `data` and
`connectivity` in sample data, and the desk (e2e/desk.py) holding
`portfolio`, `oms` and `ems`, as SnapTrade's e2e/orders.py. SIMULATED ONLY:
the plugin's simulated brokerage (src/alpaca/synthetic_broker.py); no
Alpaca, no paper account and no trading API is reached.

1. Set up: an account for the orders, the desk's grants on it, Apple's
   record named by the desk and completed, the platform asked for its
   listing; Alpaca made available and linked to the desk, the account mapped
   to the simulated account's code; trading enabled on it under the
   connectivity role's admin, on the plugin's Trading page; releases not
   waiting for compliance (no compliance plugin here).
2. A buy of 10, limit, day: released, claimed, placed at Alpaca's instance,
   acknowledged with Alpaca's order ID and filled 4 then 6; the order
   filled; the book's position up by 10 with its pending lines. The order
   the brokerage took outside the platform, on the mapped account, is left
   alone: no order recorded, no fill (venue-originated fills are v23's).
3. Good till date, which Alpaca takes none of: rejected `unsupported`.
4. A replace and a cancel the desk asks for, each by its kind on the
   placement's change request: a working placement of 200 replaced at Alpaca
   to a limit of 229.50, Alpaca's new order followed and the request
   applied; a replace to good till date refused `unsupported`, the placement
   working on; then cancelled at Alpaca, then in the book.
5. A pause on Alpaca: a placement held back, queued, nothing sent; lifted,
   it is sent and filled. A stop with a placement of 200 working: Alpaca
   cancels it at once, reporting the cancel naming the stop; lifted again.
6. Alpaca restarted with a placement working: it reads the book again,
   finds its order by the placement's venue order ID, sends no second order,
   and cancels it there when the desk asks.
7. `store book` prints the orders, placements and executions.

Standard library only.
"""

import json
import subprocess
import time

#: Set by e2e/run.py: its compose command and its log.
COMPOSE: list[str] = []
LOG = ".e2e/runner.log"
CODE = "SYN-ALP-1001"


class Failed(Exception):
    pass


def log(text):
    with open(LOG, "a", encoding="utf-8") as kept:
        kept.write(text + "\n")


def compose(*args, check=True):
    done = subprocess.run(COMPOSE + list(args), capture_output=True, text=True, timeout=600)
    log(f"$ compose {' '.join(args)[:300]}\n{done.stdout}{done.stderr}")
    if check and done.returncode != 0:
        raise Failed(f"compose {' '.join(args)[:200]}: {(done.stdout + done.stderr).strip()[-600:]}")
    return done


def runner(*args):
    return compose("run", "--rm", "-T", "runner", *args).stdout


def must(condition, why):
    if not condition:
        raise Failed(why)


def until(seconds, attempt, what):
    deadline = time.monotonic() + seconds
    last = None
    while True:
        try:
            answer = attempt()
        except Failed as failed:
            answer, last = None, failed
        if answer:
            return answer
        if time.monotonic() > deadline:
            raise Failed(f"{what}, in {seconds} seconds" + (f": {last}" if last else ""))
        time.sleep(2)


def desk(level, path):
    said = runner("page", "--instance", "desk", "--level", level, path)
    status, _, body = said.partition("\n")
    must(status.strip() == "200", f"desk {path} answered {said[-600:]}")
    return json.loads(body)


def order_of(account, order_id):
    for order in desk("write", f"/orders?account={account}")["orders"]:
        if order["order_id"] == order_id:
            return order
    raise Failed(f"{order_id} is not listed")


def placement_of(order, placement_id):
    for held in order["placements"]:
        if held["placement_id"] == placement_id:
            return held
    raise Failed(f"{placement_id} is not on {order['order_id']}")


def placed(account, instrument, quantity, key, tif="day"):
    """An order proposed, released, claimed and placed at Alpaca."""
    proposed = desk(
        "write",
        f"/propose?account={account}&instrument={instrument}&quantity={quantity}&tif={tif}&key={key}",
    )
    must(proposed.get("order_id"), f"{key}: proposed: {proposed}")
    order = proposed["order_id"]
    released = desk("write", f"/release?order={order}")
    must(released.get("state") == "ORDER_STATE_RELEASED", f"{key}: released: {released}")
    desk("write", f"/claim?order={order}")
    placement = desk("write", f"/place?order={order}&quantity={quantity}&code={CODE}")
    must(placement.get("placement_id"), f"{key}: placed: {placement}")
    return order, placement["placement_id"]


def placement_state(account, order, placement_id, *states):
    def attempt():
        held = placement_of(order_of(account, order), placement_id)
        return held if held["state"] in states else None

    return attempt


def brokerage_orders():
    """The simulated brokerage's orders, by their client order IDs, as its
    state in the plugin's storage holds them."""
    said = compose(
        "exec", "-T", "alpaca", "python", "-c",
        "import json; from meridian import edge;"
        "p = edge.storage_dir() / 'synthetic-venue' / 'orders.json';"
        "held = json.loads(p.read_text()) if p.exists() else {};"
        "print(json.dumps([o.get('client_order_id', '') for o in held.get('orders', [])]))",
    ).stdout
    return json.loads(said.strip().splitlines()[-1])


def run():
    # 1. Set up.
    account = runner("account", "E2E Orders", "--instance", "desk").strip().splitlines()[-1]
    for role in ("portfolio", "oms", "ems"):
        runner("grant", "--instance", "desk", "--level", "write", "--role", role, "--accounts", account)
    runner("grant", "--instance", "desk", "--level", "admin", "--role", "ems")
    runner("grant", "--instance", "alpaca", "--level", "admin", "--role", "connectivity")
    aapl = desk("write", "/resolve?figi=BBG000B9XRY4&symbol=AAPL")["instrument_id"]
    must(aapl, "step 1, Apple's record named")
    if aapl in runner("instruments"):
        runner("instrument", aapl, "asset_class=equity", "currency=USD", "source=e2e",
               "description=Apple Inc.", "--ask-platform")
    runner("connectivity", "available", "--instance", "alpaca")
    runner("connectivity", "link", "--instance", "alpaca", "--ems", "desk",
           "--user", "person:local|harness")
    mapped = desk("admin", f"/map?account={account}&code={CODE}")
    must(mapped == {"mapped": CODE}, f"step 1, mapped: {mapped}")
    trading = runner("page", "--instance", "alpaca", "--level", "admin", "/admin/connections",
                     "--until", CODE)
    must("Read-only" in trading and "Sample data" in trading,
         "step 1, the account read-only until trading is enabled on it")
    runner("form", "--instance", "alpaca", "--level", "admin", "--page", "/admin/connections",
           "--post", "/admin/connections/enable-trading", f"venue_account_code={CODE}",
           "--expect", "Trading enabled")
    runner("attribute", account, "waits=false", "note=No compliance plugin in this run.")
    # The seed rule reaches the deployment from the platform in its own time:
    # each fill waits for its settlement date, so the run waits for the rule.
    today = time.strftime("%Y-%m-%d", time.gmtime())
    until(120, lambda: desk("write", f"/settles?instrument={aapl}&date={today}").get("outcome")
          == "DATE_OUTCOME_COMPUTED", "step 1, the date read never gave Apple a settlement date")

    # 2. Placed, acknowledged and filled in two.
    order, placement = placed(account, aapl, "10", "buy-10")
    until(90, lambda: (lambda o: o if o["state"] == "ORDER_STATE_FILLED" else None)(
        order_of(account, order)), "step 2, the order was not filled")
    filled = order_of(account, order)
    must(filled["reported_filled"] == "10" and filled["committed"] == "0", f"step 2: {filled}")
    held = placement_of(filled, placement)
    must(held["venue_order_id"].startswith("syn-ord-"), f"step 2, Alpaca's order ID: {held}")
    book = compose("run", "--rm", "-T", "store", "book").stdout
    executions = [line for line in book.splitlines() if line.startswith("execution|E2E Orders|")]
    fills = sorted(line.split("|")[4:6] for line in executions)
    must(fills == [["4", "230"], ["6", "230"]], f"step 2, the fills: {executions}")
    position = [line for line in book.splitlines()
                if line.startswith("position|E2E Orders|") and "AAPL" in line]
    must(position and position[0].split("|")[4] == "10",
         f"step 2, the position up by the desk's 10, nothing of the venue's own order: {position}")
    listed = desk("write", f"/orders?account={account}")["orders"]
    must(len(listed) == 1, f"step 2, no order recorded for the venue's own: {listed}")
    must(any(line.startswith("pending|E2E Orders|") for line in book.splitlines()),
         "step 2, pending to the settlement date the date read gave")

    # 3. Good till date, which Alpaca does not offer.
    order3, placement3 = placed(account, aapl, "5", "gtd-5", tif="gtd")
    rejected = until(60, placement_state(account, order3, placement3, "PLACEMENT_STATE_REJECTED"),
                     "step 3, not rejected")
    must(rejected["rejection"] == "REJECTION_REASON_UNSUPPORTED", f"step 3: {rejected}")

    # 4. A replace, a replace not offered, and a cancel the desk asks for.
    order4, placement4 = placed(account, aapl, "200", "cancel-200")
    working = until(60, placement_state(account, order4, placement4, "PLACEMENT_STATE_WORKING"),
                    "step 4, not working")
    first_id = working["venue_order_id"]
    desk("write", f"/replace?placement={placement4}&limit=229.50")

    def replaced():
        held = placement_of(order_of(account, order4), placement4)
        done = held["changes"] == [["PLACEMENT_CHANGE_KIND_REPLACE", "CHANGE_REQUEST_OUTCOME_APPLIED"]]
        return held if done else None

    held4 = until(60, replaced, "step 4, the replace was not applied")
    must(held4["limit"] in ("229.5", "229.50") and held4["venue_order_id"] not in ("", first_id)
         and held4["venue_order_ids"] == [first_id, held4["venue_order_id"]],
         f"step 4, replaced at Alpaca with its new order ID: {held4}")
    desk("write", f"/replace?placement={placement4}&tif=gtd")

    def refused():
        held = placement_of(order_of(account, order4), placement4)
        last = held["changes"][-1:] == [["PLACEMENT_CHANGE_KIND_REPLACE", "CHANGE_REQUEST_OUTCOME_REJECTED"]]
        return held if last and held["state"] == "PLACEMENT_STATE_WORKING" else None

    until(60, refused, "step 4, the replace not offered was not refused with the placement working")
    desk("write", f"/cancel?placement={placement4}")
    until(60, placement_state(account, order4, placement4, "PLACEMENT_STATE_CANCELLED"),
          "step 4, not cancelled")

    # 5. A pause, then a stop.
    runner("instruction", "pause", "--plugin", "alpaca", "note=Hold Alpaca's sends.")
    time.sleep(3)
    order5, placement5 = placed(account, aapl, "10", "paused-10")
    time.sleep(6)
    held5 = placement_of(order_of(account, order5), placement5)
    must(held5["state"] == "PLACEMENT_STATE_QUEUED", f"step 5, held back under the pause: {held5}")
    must(placement5 not in brokerage_orders(), "step 5, nothing sent to Alpaca under the pause")
    runner("instruction", "active", "--plugin", "alpaca", "note=Resume.")
    until(90, placement_state(account, order5, placement5, "PLACEMENT_STATE_FILLED"),
          "step 5, the held placement was not sent once the pause lifted")
    order6, placement6 = placed(account, aapl, "200", "stopped-200")
    until(60, placement_state(account, order6, placement6, "PLACEMENT_STATE_WORKING"),
          "step 5, not working before the stop")
    runner("instruction", "stop", "--plugin", "alpaca", "note=Halt Alpaca; cancel its work.")
    until(60, placement_state(account, order6, placement6, "PLACEMENT_STATE_CANCELLED"),
          "step 5, the stop's cancel was not recorded")
    runner("instruction", "active", "--plugin", "alpaca", "note=Resume.")

    # 6. Restarted with a placement working.
    order7, placement7 = placed(account, aapl, "200", "restart-200")
    until(60, placement_state(account, order7, placement7, "PLACEMENT_STATE_WORKING"),
          "step 6, not working before the restart")
    compose("up", "-d", "--no-deps", "--force-recreate", "alpaca")
    runner("ready", "--instance", "alpaca")
    time.sleep(8)
    must(brokerage_orders().count(placement7) == 1, "step 6, a second order sent after the restart")
    desk("write", f"/cancel?placement={placement7}")
    until(90, placement_state(account, order7, placement7, "PLACEMENT_STATE_CANCELLED"),
          "step 6, the restarted plugin did not cancel its order at Alpaca")

    # 7. The book.
    book = compose("run", "--rm", "-T", "store", "book").stdout
    for wanted in ("order|E2E Orders|", "placement|E2E Orders|", "execution|E2E Orders|"):
        must(wanted in book, f"step 7, store book prints {wanted!r}")
    sent = brokerage_orders()
    return (
        f"the order path: trading enabled on {CODE} under the connectivity role's admin, the "
        f"desk's buy of 10 acknowledged ({held['venue_order_id']}) and filled 4 then 6, each with "
        "its settlement date, the position up by 10, the order the brokerage took outside the "
        "platform left alone; good till date rejected unsupported; a replace at Alpaca to its new "
        f"order ({held4['venue_order_id']}), one not offered refused unsupported, and a cancel at "
        "Alpaca; a placement held under a pause and sent when it lifted; a stop's cancel at once; "
        f"a restart sending no second order and cancelling at Alpaca; {len(sent)} orders at the "
        "simulated brokerage in all"
    )
