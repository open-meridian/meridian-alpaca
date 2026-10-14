"""`make e2e`: the plugin on core's plugin harness, on sample data, with no
key (the harness's README; plans/the-lake-prices-the-book, row 8). Its
recorded responses are the synthetic exchange shaped on Alpaca's
documented and recorded responses (src/alpaca/exchanges/exchange.json);
`make e2e-recorded RECORDED=<dir>` replays a developer's own recording, never
committed, through the same run.

Three plugins: `alpaca`, this one, `reader` (e2e/reader.py), a stand-in for
whatever reads prices, and `desk` (e2e/desk.py), a stand-in holding
`portfolio`, `oms` and `ems` that places orders at Alpaca's instance, each
from the same image.

1. The catalogue: the three datasets listed by `dashboard__list_datasets`,
   as the plugin declared them -- daily pulled and pushed, its day New
   York's to 16:00; live pulled and streamed; trades pulled and streamed,
   kept two days -- each with Alpaca's terms, one person's, as its default
   licence.
2. Licensed and entitled: the deployment admin licenses the three, and
   entitles the reader to them, the broker reloaded with each.
3. A close wanted: the reader reads SPY's close for 2026-10-08, which the lake
   has not got, so asks the plugin; the plugin records it against the want:
   573.78 in dollars, consolidated (no venue), its business date a plain
   date, published at the close, its raw record named.
4. A share class: BRK.B's close and daily bar (no VWAP, as Alpaca stated
   none), its row keyed in the one spelling.
5. Another source's record: TQQQ, known to the deployment only as a
   custodian's book holds it (its CUSIP and the custodian's symbol), is
   wanted; the plugin reads the record (W3.6, a data plugin's from contract v18 as
   ruled 2026-10-10), finds its own symbol and records the close against
   the want: 82.37, keyed in Alpaca's spelling.
6. Live, on a venue the deployment does not hold: SPY's last price, from
   IEX, recorded with no venue and Alpaca's code as reported, and the miss
   counted (the harness has no platform to pull the venue from).
6b. Quotes (contract v19): the reader reads SPY's and BRK.B's latest quotes,
   which the lake has not got; live is streamed, so the read is a standing
   want, and the plugin records each listing's quote and streams them from
   then on: SPY's two-sided with its sizes in shares, BRK.B's with no offer.
6c. Trades: under those standing wants the synthetic stream's trades are
   recorded, kept two days: read over a range, a regular trade eligible for
   every statistic with no aggressor, an odd lot not to set the high, low
   or close and then withdrawn as its next version, a code with no
   conversion kept as reported, its eligibilities not known.
6d. Caught up from the lake: the trades recorded after the watermark the
   last read said, none read twice.
7. Parity: the plugin's pages, Connection and Datasets, the data role's
   (contract v20), read under Manage and through `/mcp` as
   `alpaca__read_connection` and `alpaca__read_datasets`, the same answers
   under the role's names, the stream's session among Alpaca's own; neither
   shows a price.
8. Storage: the plugin's container made again keeps which listing each
   subject is, in the storage the deployment grants it; and (8b) it reads on
   start that each of its datasets is licensed, as Datasets then says.
9. `store lake`: each row as recorded, numbered with no holes, the wants
   answered and declined, the venue's miss.
10. From 0.7.0, the order path (e2e/orders.py): a third plugin, the desk
   (e2e/desk.py), places at Alpaca's instance, holding `connectivity`
   beside `data`, and the plugin trades with its simulated brokerage; from
   0.8.0 Alpaca holds `custody` too, its account linked, an order placed in
   Alpaca's own app reported on no placement and accepted by the desk, and
   the desk, holding `operations`, reconciles the book against Alpaca's
   statement with no break.

Run by the Makefile from the repository's root, after it builds the image and
copies the harness out; standard library only.
"""

import json
import os
import subprocess
import sys
import time

import orders

#: `--recorded` replays a developer's recording (`make e2e-recorded`): its
#: values are Alpaca's, not the synthetic exchange's, so each is checked for
#: its shape. The Makefile gives the compose files what they read
#: (MERIDIAN_RUNTIME_IMAGE, ALPACA_E2E, RECORDED) in the environment this
#: inherits.
RECORDED = "--recorded" in sys.argv[1:]
NAME = "alpaca-e2e-recorded" if RECORDED else "alpaca-e2e"
LOG = ".e2e/runner.log"
COMPOSE = ["docker", "compose", "-p", NAME, "-f", ".e2e/harness/compose.yaml",
           "-f", ".e2e/harness/plugins.yaml", "-f", "e2e/plugin.yaml"]
DAY = "2026-10-08"
DAILY, LIVE, TRADES = "alpaca:daily", "alpaca:live", "alpaca:trades"
MINUTE_NS = 60 * 10**9
COMPOSE += ["-f", "e2e/recorded.yaml"] if RECORDED else []


def same(value, synthetic):
    """The synthetic exchange's value, or, replaying a recording, a value."""
    return bool(value) if RECORDED else value == synthetic


class Failed(Exception):
    pass


def log(text):
    with open(LOG, "a", encoding="utf-8") as kept:
        kept.write(text + "\n")


def compose(*args, check=True):
    done = subprocess.run(COMPOSE + list(args), capture_output=True, text=True,
                          timeout=600)
    log(f"$ compose {' '.join(args)[:300]}\n{done.stdout}{done.stderr}")
    if check and done.returncode != 0:
        raise Failed(f"compose {' '.join(args)[:200]}: {(done.stdout + done.stderr).strip()[-600:]}")
    return done


def runner(*args, check=True):
    done = compose("run", "--rm", "-T", "runner", *args, check=check)
    return done.stdout if check else done


def must(condition, why):
    if not condition:
        raise Failed(why)


def page(instance, level, path):
    said = runner("page", "--instance", instance, "--level", level, path)
    status, _, body = said.partition("\n")
    must(status.strip() == "200", f"{instance} {path} answered {said[-600:]}")
    return body


def reader(path):
    return json.loads(page("reader", "read", path))


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


def call(tool, arguments, outcome):
    done = runner("mcp", "call", tool, json.dumps(arguments), check=False)
    try:
        answer = json.loads(done.stdout)
    except ValueError:
        raise Failed(f"{tool}: no answer: {(done.stdout + done.stderr)[-400:]}")
    must(answer.get("outcome") == outcome,
         f"{tool} answered {answer.get('outcome')}, not {outcome}: {done.stdout[-700:]}")
    return answer


def answered(path, rows="prices"):
    """A read, again until the lake answers it with rows."""
    def attempt():
        got = reader(path)
        return got if got[rows] else None
    return until(90, attempt, f"{path} was never answered")


def run():
    started = time.time()
    compose("down", "-v", "--remove-orphans", check=False)
    compose("up", "-d")
    for instance in ("alpaca", "reader", "desk"):
        runner("ready", "--instance", instance)
    runner("settings", "sample_data=true")
    # From 0.7.0 it holds more than one role (from 0.8.0 three), and a grant
    # names one.
    runner("grant", "--level", "admin", "--role", "data")
    runner("grant", "--instance", "reader", "--level", "read", "--role", "reporting")
    runner("mcp", "connect", "--covers", "deployment_admin", "--covers", "alpaca:data:admin")

    # 1. The catalogue.
    def listed():
        held = {d["dataset"]: d for d in call("dashboard__list_datasets", {}, "unchanged")["data"]["datasets"]}
        return held if {DAILY, LIVE, TRADES} <= set(held) else None
    held = until(90, listed, "step 1, the three datasets were not listed")
    daily, live = held[DAILY]["catalogue_entry"], held[LIVE]["catalogue_entry"]
    trades_entry = held[TRADES]["catalogue_entry"]
    must(daily["modes"] == ["pull", "push"] and live["modes"] == ["pull", "stream"]
         and trades_entry["modes"] == ["pull", "stream"],
         f"step 1, the modes: {daily['modes']}, {live['modes']}, {trades_entry['modes']}")
    must(trades_entry["licence_default"]["kept"] is True
         and trades_entry["licence_default"]["retention_days"] == 2,
         f"step 1, trades kept two days by default: {trades_entry['licence_default']}")
    must(daily["day_time_zone"] == "America/New_York" and daily["day_end_minute"] == 960,
         f"step 1, the declared day: {daily}")
    must(daily["licence_default"]["personal_use"] is True and daily["licence_default"]["kept"] is True
         and live["licence_default"]["kept"] is False,
         f"step 1, Alpaca's terms as the default licence: {daily['licence_default']}, {live['licence_default']}")

    # 2. Licensed and entitled.
    runner("licence", DAILY, "kept=true", "retention_days=0", "derived_use=true", "display=true",
           "personal_use=true", "note=Alpaca's terms: the account holder's personal use.")
    runner("licence", LIVE, "kept=true", "retention_days=2", "derived_use=true", "display=true",
           "personal_use=true", "note=Kept two days, for the e2e to read back.")
    runner("licence", TRADES, "kept=true", "retention_days=2", "derived_use=true", "display=true",
           "personal_use=true", "note=Alpaca's terms; kept two days, for catching up.")
    for dataset in (DAILY, LIVE, TRADES):
        runner("entitle", "--instance", "reader", dataset, "note=The e2e's reader.")

    ids = reader("/resolve")
    spy, brk, other = ids["spy"], ids["brk"], ids["other"]

    # 3. A close wanted, and recorded against the want.
    first = reader(f"/prices?subject={spy}&date={DAY}&kind=close&dataset={DAILY}")
    must(first["prices"] or any(u["reason"] == "UNANSWERED_REASON_ASKED_SOURCE" for u in first["unanswered"]),
         f"step 3, a close not kept is asked of the plugin: {first}")
    close = answered(f"/prices?subject={spy}&date={DAY}&kind=close&dataset={DAILY}")["prices"]
    must(len(close) == 1, f"step 3, one close: {close}")
    row = close[0]
    must(same(row["amount"], "573.78") and row["currency_code"] in ("USD", "") and row["venue_id"] == ""
         and row["business_date"] == DAY and row["published"] and row["version"] == 1
         and row["row_key"] == f"SPY:1d:2026-10-08@{spy}" and row["raw"].startswith("bars/sip/1Day/"),
         f"step 3, SPY's close as recorded: {row}")

    # 4. A share class: the close and the bar.
    brk_close = answered(f"/prices?subject={brk}&date={DAY}&kind=close&dataset={DAILY}")["prices"]
    must(len(brk_close) == 1 and same(brk_close[0]["amount"], "453.36") and brk_close[0]["row_key"] == f"BRK.B:1d:2026-10-08@{brk}",
         f"step 4, BRK.B's close: {brk_close}")
    bars = answered(f"/bars?subject={brk}&date={DAY}&dataset={DAILY}", rows="bars")["bars"]
    must(len(bars) == 1 and same(bars[0]["close"], "453.36")
         and (RECORDED or bars[0]["vwap"] is False) and same(bars[0]["volume"], "3459789"),
         f"step 4, BRK.B's bar, no VWAP: {bars}")

    # 5. Another source's record, read and priced.
    reader(f"/prices?subject={other}&date={DAY}&kind=close&dataset={DAILY}")
    held = answered(f"/prices?subject={other}&date={DAY}&kind=close&dataset={DAILY}")["prices"]
    must(len(held) == 1 and same(held[0]["amount"], "82.37") and held[0]["row_key"] == f"TQQQ:1d:2026-10-08@{other}",
         f"step 5, TQQQ's close for another source's record: {held}")

    # 6. Live, on a venue the deployment does not hold.
    reader(f"/prices?subject={spy}&kind=last&dataset={LIVE}")
    last = answered(f"/prices?subject={spy}&kind=last&dataset={LIVE}")["prices"]
    must(same(last[0]["amount"], "575.44") and last[0]["venue_id"] == ""
         and last[0]["unconverted"] == ["alpaca:venue=V"] and last[0]["business_date"] == "",
         f"step 6, SPY's last on IEX, the venue kept as reported: {last}")

    # 6b. Quotes: a standing want of a streamed dataset.
    def quoted(subject, streamed=lambda q: True):
        def attempt():
            got = reader(f"/quotes?subject={subject}&dataset={LIVE}")
            return [q for q in got["quotes"] if RECORDED or streamed(q)] or None
        return until(90, attempt, f"step 6b, {subject}'s quote was never recorded")
    spy_quote = quoted(spy)[0]
    must(spy_quote["bid"] and spy_quote["ask"] and same(spy_quote["bid_quantity"], "200")
         and same(spy_quote["ask_quantity"], "300") and spy_quote["business_date"] == ""
         and spy_quote["venue_id"] == "" and spy_quote["unconverted"] == ["alpaca:venue=V"],
         f"step 6b, SPY's quote, two-sided with its sizes: {spy_quote}")
    # The stream's quote, later than the latest one read when it was wanted.
    brk_quote = quoted(brk, lambda q: q["ask"] is None)[0]
    must(RECORDED or (brk_quote["bid"] and brk_quote["ask"] is None and brk_quote["ask_quantity"] is None),
         f"step 6b, BRK.B's quote with no offer: {brk_quote}")

    # 6c. Trades streamed under the standing wants, read over a range.
    def traded(after=None, at_least=1):
        def attempt():
            if after:
                got = reader(f"/trades?subject={spy}&dataset={TRADES}&after={after}")
            else:
                now = time.time_ns()
                got = reader(f"/trades?subject={spy}&dataset={TRADES}"
                             f"&from={now - 10 * MINUTE_NS}&until={now + MINUTE_NS}")
            return got if len({t["row_key"] for t in got["trades"]}) >= at_least else None
        return until(120, attempt, f"step 6c/6d, SPY's trades were never recorded (after {after})")
    if not RECORDED:
        day = traded(at_least=3)
        rows = day["trades"]
        # The harness holds no venue: each trade keeps Alpaca's letter as reported.
        regular = [t for t in rows if t["characteristics"] == [] and not t["cancelled"]
                   and t["unconverted"] == ["alpaca:venue=V"]]
        odd = [t for t in rows if t["characteristics"] == ["TRADE_CHARACTERISTIC_ODD_LOT"]]
        unknown = [t for t in rows if "alpaca:trade.c=~" in t["unconverted"]]
        must(regular and all(r["consolidated"] == ["ELIGIBLE_ELIGIBLE"] * 4
                             and r["aggressor"] == "AGGRESSOR_UNSPECIFIED"
                             and r["business_date"] == "" for r in regular),
             f"step 6c, a regular trade, every statistic, no aggressor: {regular[:2]}")
        must(odd and odd[0]["consolidated"] == ["ELIGIBLE_NOT_ELIGIBLE"] * 3 + ["ELIGIBLE_ELIGIBLE"]
             and any(t["cancelled"] and t["version"] == 2 for t in odd),
             f"step 6c, an odd lot, withdrawn as its next version: {odd[:3]}")
        must(unknown and unknown[0]["consolidated"] == ["ELIGIBLE_UNSPECIFIED"] * 4,
             f"step 6c, a code with no conversion, kept as reported: {unknown[:1]}")
        must(all(spy in t["subjects"] for t in rows),
             f"step 6c, every row names its listing's records: {rows[:1]}")

        # 6d. Caught up from the lake, after the watermark the read said.
        later = traded(after=day["watermark"])
        seen = {(t["row_key"], t["version"]) for t in rows}
        must(not any((t["row_key"], t["version"]) in seen for t in later["trades"]),
             f"step 6d, nothing read twice after the watermark: {later['trades'][:2]}")

    # 7. Parity: the pages under Manage and their tools through /mcp.
    listed_tools = runner("mcp", "list").split()
    for tool in ("alpaca__read_connection", "alpaca__read_datasets"):
        must(tool in listed_tools, f"step 7, {tool} is offered: {listed_tools}")
    # The data role's common fields, under its names, with Alpaca's beside.
    connection = call("alpaca__read_connection", {}, "unchanged")["data"]
    must(connection["mode"] == "Sample data" and connection["daily_feed"] == "sip"
         and [c["label"] for c in connection["credentials"]] == ["API key ID", "API secret key"],
         f"step 7, read_connection: {connection}")
    must(connection["sessions"] == 1 and connection["stream"]["state"] == "streaming",
         f"step 7, the stream's session: {connection['stream']}")
    datasets = call("alpaca__read_datasets", {}, "unchanged")["data"]
    by_key = {d["key"]: d for d in datasets["served"]}
    must(by_key["daily"]["recorded"] >= 4
         and by_key["live"]["recorded"] >= 1 and datasets["listings"] == 4
         and datasets["reads_instruments"] is True and datasets["streamed"] >= 2
         and (RECORDED or by_key["trades"]["recorded"] >= 3),
         f"step 7, read_datasets: {datasets}")
    shown = page("alpaca", "admin", "/datasets")
    must("alpaca:daily" in shown and "573.78" not in shown and "<h2>Served</h2>" in shown,
         "step 7, the Datasets page, the role's, shows no price")
    shown = page("alpaca", "admin", "/")
    must("<h2>Key</h2>" in shown and "<h2>Limits and use</h2>" in shown, "step 7, the role's Connection")

    # 8. Storage: which listing each subject is, kept across a new container.
    compose("up", "-d", "--no-deps", "--force-recreate", "alpaca")
    kept = compose("exec", "-T", "alpaca", "python", "-c",
                   "import json; from meridian import edge; "
                   "print(json.dumps(json.load(open(edge.storage_dir() / 'products.json'))))").stdout
    products = json.loads(kept)
    must(products.get(spy) == "SPY" and products.get(brk) == "BRK.B" and products.get(other) == "TQQQ"
         and sorted(products.values()) == ["BRK.B", "SPY", "TQQQ", "TQQQ"],
         f"step 8, the product map kept: {kept}")
    runner("ready", "--instance", "alpaca")

    # 8b. Its datasets' licences, read on start (contract v20): each one
    # licensed in step 2 said so on Datasets.
    def licensed():
        served = call("alpaca__read_datasets", {}, "unchanged")["data"]["served"]
        return served if all(s["licensed"] for s in served) else None
    until(90, licensed, "step 8b, the licences were never read")

    # 9. The store.
    lines = compose("run", "--rm", "-T", "store", "lake").stdout.split("\n")
    log("store lake:\n" + "\n".join(lines))
    daily_rows = [line.split("|") for line in lines if line.startswith(f"row|{DAILY}|")]
    sequences = sorted(int(r[3]) for r in daily_rows)
    must(sequences == list(range(1, len(sequences) + 1)) and len(sequences) >= 3,
         f"step 9, daily's rows numbered from 1 with no holes: {sequences}")
    keys = {(r[2], r[4]) for r in daily_rows}
    must({("price", f"SPY:1d:2026-10-08@{spy}"), ("price", f"BRK.B:1d:2026-10-08@{brk}"),
          ("bar", f"BRK.B:1d:2026-10-08@{brk}")} <= keys, f"step 9, the rows kept: {sorted(keys)}")
    must(any(line.startswith(f"want|{DAILY}|answered|{other}") for line in lines),
         f"step 9, another source's record answered: {[l for l in lines if l.startswith('want|')]}")
    must(any(line.startswith(f"want|{DAILY}|answered|{spy}") for line in lines),
         f"step 9, SPY's want answered: {[l for l in lines if l.startswith('want|')]}")
    live_rows = [line.split("|") for line in lines if line.startswith(f"row|{LIVE}|")]
    must(live_rows and all(r[-1] not in ("", "0") for r in live_rows),
         f"step 9, the live rows carry their unconverted venue: {live_rows}")
    must(any(line.startswith("miss|alpaca|") for line in lines), f"step 9, the venue's miss: {lines}")
    trade_rows = [line.split("|") for line in lines if line.startswith(f"row|{TRADES}|")]
    numbered = sorted(int(r[3]) for r in trade_rows)
    must(RECORDED or (numbered and numbered == list(range(1, len(numbered) + 1))),
         f"step 9, the trades numbered from 1 with no holes: {numbered[:20]}")

    # 10. The order path, in the same harness.
    orders.COMPOSE, orders.LOG = COMPOSE, LOG
    path = orders.run()

    took = int(time.time() - started)
    print(f"{NAME} OK in {took}s on the plugin harness, sample data, no key: the three datasets listed as "
          "declared, Alpaca's personal-use terms their default licence; licensed and the reader "
          "entitled; SPY's 2026-10-08 close wanted and recorded against the want (573.78, "
          "consolidated, a plain date, published at the close); BRK.B's close and bar (no VWAP); "
          "another source's record read and its close recorded (82.37); SPY's "
          "last on IEX with the venue kept as reported and the miss counted; Connection and "
          "Datasets, the data role's, read under Manage and through /mcp alike, no price shown; "
          "the product map kept across a new container, its licences read on start; `store lake` numbered with no holes; quotes as a standing want, streamed; trades streamed and kept, their "
          "attributes converted, caught up after the watermark; " + path)


def main():
    os.makedirs(".e2e", exist_ok=True)
    open(LOG, "w").close()
    try:
        run()
    except (Failed, orders.Failed, subprocess.TimeoutExpired, KeyError, ValueError, IndexError,
            TypeError) as failed:
        compose("logs", "--no-color", check=False)
        print(f"{NAME} FAILED: {failed}; the run is in {LOG}", file=sys.stderr)
        return 1
    finally:
        compose("logs", "--no-color", check=False)
        compose("down", "-v", "--remove-orphans", check=False)
    return 0


if __name__ == "__main__":
    sys.exit(main())
