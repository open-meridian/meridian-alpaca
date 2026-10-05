"""Alpaca's own differences, which the shared suite does not state: the key and
where it never goes, the rate limit, the feeds and their entitlements, pages,
JSON-number prices, exchange codes, round-lot sizes, snapshots and the CLI.
Each against the synthetic exchanges."""

from __future__ import annotations

import json
import logging
from decimal import Decimal
from pathlib import Path

import pytest

from alpaca import assets, convert, sample
from alpaca.raw import RawStore
from alpaca.rows import Price, Quote
from alpaca.sample import Session
from alpaca.settings import DECLARED, IEX, SIP, config_from
from alpaca.venue import (
    DATA,
    PAGE_LIMIT,
    RATE_PER_SECOND,
    Entitlements,
    NoKey,
    RateLimiter,
    VenueError,
    feed_for,
)

from conftest import FREE, KEY, NOW_NS, Sleeps, body, client, kept_text

TQQQ = assets.listing("TQQQ")


class Clock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.now += seconds


# --- the key ------------------------------------------------------------------


def test_the_key_is_sent_as_alpacas_headers_and_kept_nowhere(tmp_path: Path) -> None:
    alpaca, replay = client(tmp_path)
    alpaca.latest_quotes(["TQQQ"], IEX)
    [(_, sent)] = replay.asked
    assert sent["APCA-API-KEY-ID"] == KEY.key_id
    assert sent["APCA-API-SECRET-KEY"] == KEY.secret_key
    kept = kept_text(tmp_path)
    assert KEY.key_id not in kept and KEY.secret_key not in kept
    [name] = list(alpaca.store.records())
    record = alpaca.store.get(name)
    assert record is not None
    assert set(record["request"]["headers"]) == {"User-Agent", "Accept"}


def test_a_key_echoed_back_is_redacted_before_it_is_kept(tmp_path: Path) -> None:
    echo = tmp_path / "echo.json"
    echo.write_text(json.dumps({"message": f"bad key {KEY.key_id}"}))
    alpaca, _ = client(tmp_path / "raw", refuse={"/v2/stocks/quotes/latest": [(401, str(echo))]})
    with pytest.raises(VenueError) as refused:
        alpaca.latest_quotes(["TQQQ"], IEX)
    assert KEY.key_id not in str(refused.value)
    assert KEY.key_id not in kept_text(tmp_path / "raw")
    assert "[redacted]" in kept_text(tmp_path / "raw")


def test_without_a_key_nothing_is_asked(tmp_path: Path) -> None:
    alpaca, replay = client(tmp_path, credentials=None)
    with pytest.raises(NoKey):
        alpaca.entitlements()
    assert replay.asked == [] and alpaca.calls == 0


def test_the_key_is_two_secret_settings_and_never_in_a_repr(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    secret = {s.name for s in DECLARED if s.secret}
    assert secret == {"alpaca_api_key_id", "alpaca_api_secret_key"}
    config = config_from({"alpaca_api_key_id": KEY.key_id, "alpaca_api_secret_key": KEY.secret_key})
    assert config.credentials == KEY and config.feed == IEX
    for shown in (repr(config), str(config), repr(KEY)):
        assert KEY.key_id not in shown and KEY.secret_key not in shown
    half = config_from({"alpaca_api_key_id": KEY.key_id, "feed": "nonsense"})
    assert half.credentials is None and half.key_id_set and not half.secret_key_set
    assert half.feed == IEX
    with caplog.at_level(logging.DEBUG):
        alpaca, _ = client(tmp_path)
        alpaca.entitlements()
    assert KEY.key_id not in caplog.text and KEY.secret_key not in caplog.text


# --- rate limits ----------------------------------------------------------------


def test_the_rate_limiter_spends_at_most_half_alpacas_basic_allowance() -> None:
    clock = Clock()
    limiter = RateLimiter(RATE_PER_SECOND, 1, clock=clock, sleep=clock.sleep)
    for _ in range(101):
        limiter.acquire()
    # One at once, then one every 0.6 seconds: 100 more take a minute.
    assert clock.now == pytest.approx(60.0)
    assert pytest.approx(100) == RATE_PER_SECOND * 60  # Alpaca's Basic plan: 200 a minute


def test_a_429_is_waited_out_by_retry_after_and_asked_again(tmp_path: Path) -> None:
    sleeps = Sleeps()
    slow = [(429, "unauthorized.json"), (429, "unauthorized.json")]
    alpaca, replay = client(
        tmp_path,
        refuse={"/v2/stocks/quotes/latest": slow},
        headers={"retry-after": "3"},
        sleep=sleeps,
    )
    fetched = alpaca.latest_quotes(["TQQQ"], IEX)
    assert fetched.value["quotes"]["TQQQ"]["bp"] == Decimal("62.79")
    assert sleeps == [3.0, 3.0] and alpaca.rate_limited == 2 and len(replay.asked) == 3
    statuses = [alpaca.store.get(k)["status"] for k in alpaca.store.records()]  # type: ignore[index]
    assert sorted(statuses) == [200, 429, 429]


def test_a_429_without_retry_after_waits_for_alpacas_reset(tmp_path: Path) -> None:
    sleeps = Sleeps()
    reset = str(NOW_NS // 10**9 + 10**6 + 30)  # TICKS has moved on less than a second
    alpaca, _ = client(
        tmp_path,
        refuse={"/v2/stocks/quotes/latest": [(429, "unauthorized.json")]},
        headers={"x-ratelimit-reset": reset},
        sleep=sleeps,
    )
    alpaca.latest_quotes(["TQQQ"], IEX)
    assert sleeps == [60.0]  # never more than a minute


def test_a_429_that_persists_is_an_error_naming_its_raw_record(tmp_path: Path) -> None:
    alpaca, _ = client(
        tmp_path, refuse={"/v2/stocks/quotes/latest": [(429, "unauthorized.json")] * 4}
    )
    with pytest.raises(VenueError) as refused:
        alpaca.latest_quotes(["TQQQ"], IEX)
    assert refused.value.status == 429
    assert alpaca.store.get(refused.value.raw_key) is not None


# --- feeds and entitlements ---------------------------------------------------------


def test_every_request_names_its_feed(tmp_path: Path) -> None:
    alpaca, replay = client(tmp_path)
    Session(alpaca, SIP, FREE).daily(TQQQ, 3, NOW_NS)
    Session(alpaca).last(TQQQ)
    alpaca.snapshots(["TQQQ"], IEX)
    feeds = [url.split("feed=")[1].split("&")[0] for url, _ in replay.asked]
    assert feeds == ["sip", "iex", "iex"]
    assert all(url.startswith(DATA + "/v2/stocks/") for url, _ in replay.asked)
    with pytest.raises(ValueError, match="names its feed"):
        alpaca.latest_quotes(["TQQQ"], "otc")


def test_a_free_account_is_entitled_to_iex_and_to_sip_past_15_minutes(tmp_path: Path) -> None:
    alpaca, replay = client(
        tmp_path, refuse={"/v2/stocks/quotes/latest sip": [(403, "forbidden-sip.json")]}
    )
    entitled = alpaca.entitlements()
    assert entitled == Entitlements(answered=True, iex=True, sip_history=True, sip_live=False)
    history = next(url for url, _ in replay.asked if "/bars" in url)
    # Ending 16 minutes before now, past the free plan's restriction.
    assert "end=2026-10-02T15%3A44%3A00Z" in history and "feed=sip" in history


def test_an_unlimited_account_is_entitled_to_sip_in_real_time(tmp_path: Path) -> None:
    alpaca, _ = client(tmp_path)
    assert alpaca.entitlements() == Entitlements(True, True, True, True)


def test_a_refused_key_is_said_without_asking_further(tmp_path: Path) -> None:
    alpaca, replay = client(
        tmp_path, refuse={"/v2/stocks/quotes/latest iex": [(401, "unauthorized.json")]}
    )
    entitled = alpaca.entitlements()
    assert not entitled.answered and "request is not authorized" in entitled.detail
    assert len(replay.asked) == 1


def test_sip_is_served_only_where_entitled_and_iex_by_default() -> None:
    assert feed_for("daily", IEX, FREE) == IEX and feed_for("live", IEX, FREE) == IEX
    assert feed_for("daily", SIP, FREE) == SIP and feed_for("live", SIP, FREE) == IEX
    assert feed_for("live", SIP, Entitlements(True, True, True, True)) == SIP
    assert feed_for("daily", SIP, None) == IEX


def test_the_catalogue_has_a_daily_and_a_live_dataset_per_feed() -> None:
    assert [d.key for d in convert.CATALOGUE] == ["daily-iex", "daily-sip", "live-iex", "live-sip"]
    assert {d.vendor for d in convert.CATALOGUE} == {"Alpaca (IEX)", "Alpaca (SIP)"}


def test_every_row_says_its_feed(tmp_path: Path) -> None:
    alpaca, _ = client(tmp_path)
    closes, _ = Session(alpaca, IEX).daily(TQQQ, 3, NOW_NS)
    # IEX's own bars are one exchange's, not the consolidated tape's.
    assert {(c.meta.source.dataset, c.meta.source.venue) for c in closes} == {("daily-iex", "IEXG")}
    closes, _ = Session(alpaca, SIP, FREE).daily(TQQQ, 3, NOW_NS)
    assert {(c.meta.source.dataset, c.meta.source.venue) for c in closes} == {("daily-sip", "")}


# --- pages, numbers, exchanges, sizes ---------------------------------------------------


def test_bars_are_paged_every_page_kept(tmp_path: Path) -> None:
    routes = {
        "/v2/stocks/bars": "bars-page-1.json",
        "/v2/stocks/bars sip VFFRUXwyMDI2LTEwLTAxVDA0OjAwOjAwWg==": "bars-page-2.json",
    }
    alpaca, replay = client(tmp_path, routes)
    closes, _ = Session(alpaca, SIP, FREE).daily(TQQQ, 3, NOW_NS)
    assert [c.meta.business_date for c in closes] == ["2026-09-30", "2026-10-01"]
    assert len(replay.asked) == 2 and "page_token=" in replay.asked[1][0]
    assert f"limit={PAGE_LIMIT}" in replay.asked[0][0] and "adjustment=raw" in replay.asked[0][0]
    assert len({c.meta.raw.key.split("#")[0] for c in closes}) == 2


def test_prices_are_json_numbers_read_as_decimals_from_their_text(tmp_path: Path) -> None:
    alpaca, _ = client(tmp_path, {"/v2/stocks/bars": "bars-precise.json"})
    [page] = alpaca.bars(["TQQQ"], NOW_NS - 86_400 * 10**9, NOW_NS, SIP)
    [bar] = page.value["bars"]["TQQQ"]
    assert bar["c"] == Decimal("62.123456789012345678") and isinstance(bar["c"], Decimal)
    assert json.loads("62.123456789012345678") != bar["c"]


def test_a_bar_that_does_not_start_at_new_yorks_midnight_is_refused() -> None:
    bar = {"t": "2026-10-02T00:00:00Z", "o": 1, "h": 1, "l": 1, "c": 1, "v": 1}
    with pytest.raises(ValueError, match="New York's midnight"):
        convert.daily(TQQQ, [bar], "k", SIP, NOW_NS)


def test_the_new_york_day_follows_daylight_time() -> None:
    assert convert.new_york_midnight_ns("2026-10-02") == 1_790_913_600 * 10**9  # 04:00Z, EDT
    assert convert.new_york_midnight_ns("2026-12-01") % (86_400 * 10**9) == 5 * 3600 * 10**9


def test_a_sip_quote_across_two_exchanges_is_consolidated(tmp_path: Path) -> None:
    alpaca, _ = client(tmp_path, {"/v2/stocks/quotes/latest sip": "quotes-latest-sip.json"})
    quote = Session(alpaca, SIP, Entitlements(True, True, True, True)).quote(TQQQ)
    assert (quote.meta.source.dataset, quote.meta.source.venue) == ("live-sip", "")
    assert (quote.bid_quantity, quote.ask_quantity) == (Decimal("900"), Decimal("1200"))


def test_an_exchange_code_with_no_certain_mic_is_reported(tmp_path: Path) -> None:
    item = dict(body("trades-latest.json")["trades"]["TQQQ"], x="D")
    trade = convert.trade(TQQQ, item, "k", SIP)
    assert trade.meta.source.venue == ""
    assert ("alpaca:trades.x", "D") in [(u.scheme, u.code) for u in trade.meta.unconverted]


def test_a_quotes_sizes_before_alpaca_stated_shares_are_as_reported(tmp_path: Path) -> None:
    alpaca, _ = client(tmp_path, {"/v2/stocks/quotes/latest": "quotes-round-lots.json"})
    quote = Session(alpaca).quote(TQQQ)
    assert quote.bid is not None and (quote.bid_quantity, quote.ask_quantity) == (None, None)
    reported = {(u.scheme, u.code) for u in quote.meta.unconverted}
    assert ("alpaca:quotes.bs.round_lots", "3") in reported
    assert ("alpaca:quotes.as.round_lots", "2") in reported


def test_conditions_are_reported_under_their_tapes_code_set(tmp_path: Path) -> None:
    alpaca, _ = client(tmp_path)
    [_, odd_lot] = Session(alpaca).trades(TQQQ, NOW_NS - 60 * 10**9, NOW_NS)
    assert [(u.scheme, u.code) for u in odd_lot.meta.unconverted] == [
        ("alpaca:trades.c:C", "@"),
        ("alpaca:trades.c:C", "I"),
    ]


def test_a_snapshot_gives_the_last_price_and_the_quote(tmp_path: Path) -> None:
    alpaca, _ = client(tmp_path)
    rows = Session(alpaca).snapshot(TQQQ)
    assert [type(r) for r in rows] == [Price, Quote]
    assert rows[0].price is not None and rows[0].price.amount == Decimal("62.795")  # type: ignore[union-attr]
    assert all(r.meta.raw.key.endswith(("#latestTrade", "#latestQuote")) for r in rows)


def test_a_symbol_in_another_spelling_or_currency_is_refused() -> None:
    for code in ("BRK/B", "BRK-B", "tqqq", "", "TQQQ.TOOLONG"):
        with pytest.raises(ValueError):
            assets.listing(code)
    with pytest.raises(ValueError, match="US dollars"):
        assets.listing("TQQQ", "CAD")


# --- the CLI --------------------------------------------------------------------------


def test_the_cli_prints_each_row_as_exact_json(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(sample, "Alpaca", lambda store, key: client(store.root, credentials=key)[0])
    monkeypatch.setattr("time.time_ns", lambda: NOW_NS)
    argv = ["--symbol", "TQQQ", "--feed", "sip", "--days", "3", "--raw-dir", str(tmp_path)]
    assert sample.main(argv, KEY) == 0
    out = capsys.readouterr()
    printed = [json.loads(line) for line in out.out.splitlines()]
    kinds = [(p["row"], p.get("kind")) for p in printed]
    assert kinds[:3] == [("Price", "close")] * 3 and ("Quote", None) in kinds
    close = printed[0]
    assert close["price"] == {
        "amount": "62.12",
        "currency": {"scheme": "iso4217", "value": "USD", "source": ""},
    }
    assert close["meta"]["business_date"] == "2026-09-30"
    assert close["meta"]["source"] == {"dataset": "daily-sip", "venue": ""}
    assert "sip live True" in out.err  # the synthetic exchanges refuse nothing
    assert KEY.key_id not in out.out + out.err and KEY.secret_key not in out.out + out.err
    assert any(RawStore(tmp_path).records())
