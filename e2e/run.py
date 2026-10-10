"""`make e2e`: the plugin on core's plugin harness, in synthetic mode, with no
key (the harness's README; plans/the-lake-prices-the-book, row 8). Its
recorded responses are the synthetic exchange shaped on Alpaca's
documented and recorded responses (src/alpaca/exchanges/exchange.json);
`make e2e-recorded RECORDED=<dir>` replays a developer's own recording, never
committed, through the same run.

Two plugins: `alpaca`, this one, and `reader` (e2e/reader.py), a stand-in for
whatever reads prices, from the same image.

1. The catalogue: both datasets listed by `dashboard__list_datasets`, as the
   plugin declared them -- daily pulled and pushed, its day New York's to
   16:00; live pulled and streamed -- each with Alpaca's terms, one
   person's, as its default licence.
2. Licensed and entitled: the deployment admin licenses both, and entitles
   the reader to both, the broker reloaded with each.
3. A close wanted: the reader reads SPY's close for 2026-10-08, which the lake
   has not got, so asks the plugin; the plugin records it against the want:
   573.78 in dollars, consolidated (no venue), its business date a plain
   date, published at the close, its raw record named.
4. A share class: BRK.B's close and daily bar (no VWAP, as Alpaca stated
   none), its row keyed in the one spelling.
5. Another source's record: TQQQ, known to the deployment only as a
   custodian's book holds it (its CUSIP and the custodian's symbol), is
   wanted; the plugin reads the record (W3.6, a dgm's from contract v18 as
   ruled 2026-10-10), finds its own symbol and records the close against
   the want: 82.37, keyed in Alpaca's spelling.
6. Live, on a venue the deployment does not hold: SPY's last price, from
   IEX, recorded with no venue and Alpaca's code as reported, and the miss
   counted (the harness has no platform to pull the venue from).
7. Parity: the plugin's pages, Connection and Datasets, read under Manage and
   through `/mcp` as `alpaca__read_connection` and `alpaca__read_datasets`,
   the same answers; neither shows a price.
8. Storage: the plugin's container made again keeps which listing each
   subject is, in the storage the deployment grants it.
9. `store lake`: each row as recorded, numbered with no holes, the wants
   answered and declined, the venue's miss.

Run by the Makefile from the repository's root, after it builds the image and
copies the harness out; standard library only.
"""

import json
import os
import subprocess
import sys
import time

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
DAILY, LIVE = "alpaca:daily", "alpaca:live"
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
    for instance in ("alpaca", "reader"):
        runner("ready", "--instance", instance)
    runner("settings", "synthetic=true")
    runner("grant", "--level", "admin")
    runner("grant", "--instance", "reader", "--level", "read", "--role", "reporting")
    runner("mcp", "connect", "--covers", "deployment_admin", "--covers", "alpaca:admin")

    # 1. The catalogue.
    def listed():
        held = {d["dataset"]: d for d in call("dashboard__list_datasets", {}, "read")["data"]["datasets"]}
        return held if {DAILY, LIVE} <= set(held) else None
    held = until(90, listed, "step 1, the two datasets were not listed")
    daily, live = held[DAILY]["catalogue_entry"], held[LIVE]["catalogue_entry"]
    must(daily["modes"] == ["pull", "push"] and live["modes"] == ["pull", "stream"],
         f"step 1, the modes: {daily['modes']}, {live['modes']}")
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
    for dataset in (DAILY, LIVE):
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
         and row["row_key"] == "SPY:1d:2026-10-08" and row["raw"].startswith("bars/sip/1Day/"),
         f"step 3, SPY's close as recorded: {row}")

    # 4. A share class: the close and the bar.
    brk_close = answered(f"/prices?subject={brk}&date={DAY}&kind=close&dataset={DAILY}")["prices"]
    must(len(brk_close) == 1 and same(brk_close[0]["amount"], "453.36") and brk_close[0]["row_key"] == "BRK.B:1d:2026-10-08",
         f"step 4, BRK.B's close: {brk_close}")
    bars = answered(f"/bars?subject={brk}&date={DAY}&dataset={DAILY}", rows="bars")["bars"]
    must(len(bars) == 1 and same(bars[0]["close"], "453.36")
         and (RECORDED or bars[0]["vwap"] is False) and same(bars[0]["volume"], "3459789"),
         f"step 4, BRK.B's bar, no VWAP: {bars}")

    # 5. Another source's record, read and priced.
    reader(f"/prices?subject={other}&date={DAY}&kind=close&dataset={DAILY}")
    held = answered(f"/prices?subject={other}&date={DAY}&kind=close&dataset={DAILY}")["prices"]
    must(len(held) == 1 and same(held[0]["amount"], "82.37") and held[0]["row_key"] == "TQQQ:1d:2026-10-08",
         f"step 5, TQQQ's close for another source's record: {held}")

    # 6. Live, on a venue the deployment does not hold.
    reader(f"/prices?subject={spy}&kind=last&dataset={LIVE}")
    last = answered(f"/prices?subject={spy}&kind=last&dataset={LIVE}")["prices"]
    must(same(last[0]["amount"], "575.44") and last[0]["venue_id"] == ""
         and last[0]["unconverted"] == ["alpaca:venue=V"] and last[0]["business_date"] == "",
         f"step 6, SPY's last on IEX, the venue kept as reported: {last}")

    # 7. Parity: the pages under Manage and their tools through /mcp.
    listed_tools = runner("mcp", "list").split()
    for tool in ("alpaca__read_connection", "alpaca__read_datasets"):
        must(tool in listed_tools, f"step 7, {tool} is offered: {listed_tools}")
    connection = call("alpaca__read_connection", {}, "unchanged")["data"]
    must(connection["mode"] == "synthetic" and connection["daily_feed"] == "sip",
         f"step 7, read_connection: {connection}")
    datasets = call("alpaca__read_datasets", {}, "unchanged")["data"]
    by_key = {d["key"]: d for d in datasets["datasets"]}
    must(by_key["daily"]["recorded"] >= 4
         and by_key["live"]["recorded"] >= 1 and datasets["listings"] == 4
         and datasets["reads_instruments"] is True,
         f"step 7, read_datasets: {datasets}")
    shown = page("alpaca", "admin", "/datasets")
    must("alpaca:daily" in shown and "573.78" not in shown, "step 7, the Datasets page shows no price")
    page("alpaca", "admin", "/")

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

    # 9. The store.
    lines = compose("run", "--rm", "-T", "store", "lake").stdout.split("\n")
    log("store lake:\n" + "\n".join(lines))
    daily_rows = [line.split("|") for line in lines if line.startswith(f"row|{DAILY}|")]
    sequences = sorted(int(r[3]) for r in daily_rows)
    must(sequences == list(range(1, len(sequences) + 1)) and len(sequences) >= 3,
         f"step 9, daily's rows numbered from 1 with no holes: {sequences}")
    keys = {(r[2], r[4]) for r in daily_rows}
    must({("price", "SPY:1d:2026-10-08"), ("price", "BRK.B:1d:2026-10-08"),
          ("bar", "BRK.B:1d:2026-10-08")} <= keys, f"step 9, the rows kept: {sorted(keys)}")
    must(any(line.startswith(f"want|{DAILY}|answered|{other}") for line in lines),
         f"step 9, another source's record answered: {[l for l in lines if l.startswith('want|')]}")
    must(any(line.startswith(f"want|{DAILY}|answered|{spy}") for line in lines),
         f"step 9, SPY's want answered: {[l for l in lines if l.startswith('want|')]}")
    live_rows = [line.split("|") for line in lines if line.startswith(f"row|{LIVE}|")]
    must(live_rows and all(r[-1] not in ("", "0") for r in live_rows),
         f"step 9, the live rows carry their unconverted venue: {live_rows}")
    must(any(line.startswith("miss|alpaca|") for line in lines), f"step 9, the venue's miss: {lines}")

    took = int(time.time() - started)
    print(f"{NAME} OK in {took}s on the plugin harness, synthetic, no key: both datasets listed as "
          "declared, Alpaca's personal-use terms their default licence; licensed and the reader "
          "entitled; SPY's 2026-10-08 close wanted and recorded against the want (573.78, "
          "consolidated, a plain date, published at the close); BRK.B's close and bar (no VWAP); "
          "another source's record read and its close recorded (82.37); SPY's "
          "last on IEX with the venue kept as reported and the miss counted; Connection and "
          "Datasets read under Manage and through /mcp alike, no price shown; the product map "
          "kept across a new container; `store lake` numbered with no holes")


def main():
    os.makedirs(".e2e", exist_ok=True)
    open(LOG, "w").close()
    try:
        run()
    except (Failed, subprocess.TimeoutExpired, KeyError, ValueError, IndexError, TypeError) as failed:
        compose("logs", "--no-color", check=False)
        print(f"{NAME} FAILED: {failed}; the run is in {LOG}", file=sys.stderr)
        return 1
    finally:
        compose("logs", "--no-color", check=False)
        compose("down", "-v", "--remove-orphans", check=False)
    return 0


if __name__ == "__main__":
    sys.exit(main())
