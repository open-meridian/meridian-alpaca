"""Alpaca's client (vendor.py) and the synthetic exchange: the key sent and
never kept, the rate limit honoured, a 429 waited out, the feeds the key is
entitled to, numbers read as Decimals from their own text, and the synthetic
exchange answering as Alpaca does."""

from __future__ import annotations

import json
from collections.abc import Mapping
from decimal import Decimal
from pathlib import Path

import pytest

from alpaca.raw import RawStore
from alpaca.settings import Credentials, config_from
from alpaca.synthetic import SyntheticTransport, dumps
from alpaca.vendor import (
    IEX,
    SIP,
    Alpaca,
    NoKey,
    RateLimiter,
    Response,
    VendorError,
)

from conftest import at, no_wait

KEY = Credentials("PKTESTKEYID0000000001", "test-secret-never-kept-0000000000000001")


class Scripted:
    """A transport answering each call from a script, recording what it was
    asked and with which headers."""

    def __init__(self, *answers: tuple[int, str, Mapping[str, str]]) -> None:
        self.answers = list(answers)
        self.asked: list[tuple[str, dict[str, str]]] = []

    def __call__(self, url: str, headers: Mapping[str, str]) -> Response:
        self.asked.append((url, dict(headers)))
        status, body, extra = self.answers.pop(0)
        return Response(status, {"content-type": "application/json", **extra}, body.encode())


def client(tmp: Path, transport: object, **kwargs: object) -> Alpaca:
    return Alpaca(
        RawStore(tmp),
        KEY,
        transport=transport,  # type: ignore[arg-type]
        limiter=no_wait(),
        clock_ns=lambda: at("2026-10-10", 9),
        sleep=lambda _: None,
        **kwargs,  # type: ignore[arg-type]
    )


def test_the_key_is_sent_and_never_kept_in_a_raw_record(tmp_path: Path) -> None:
    echoed = json.dumps({"message": f"bad key {KEY.key_id} {KEY.secret_key}"})
    transport = Scripted((200, '{"quotes":{}}', {}), (401, echoed, {}))
    alpaca = client(tmp_path, transport)
    alpaca.latest_quotes(["SPY"], IEX)
    with pytest.raises(VendorError) as refused:
        alpaca.latest_quotes(["SPY"], IEX)
    assert transport.asked[0][1]["APCA-API-KEY-ID"] == KEY.key_id
    kept = "".join(p.read_text() for p in tmp_path.rglob("*.json"))
    assert KEY.key_id not in kept and KEY.secret_key not in kept
    assert "[redacted]" in kept
    assert KEY.secret_key not in str(refused.value)
    assert "key_id=" not in repr(KEY) and KEY.secret_key not in repr(config_from({}))


def test_without_a_key_nothing_is_asked(tmp_path: Path) -> None:
    transport = Scripted()
    alpaca = Alpaca(RawStore(tmp_path), None, transport=transport)
    with pytest.raises(NoKey):
        alpaca.latest_trades(["SPY"], IEX)
    assert transport.asked == []


def test_a_429_is_waited_out_and_counted(tmp_path: Path) -> None:
    waited: list[float] = []
    transport = Scripted(
        (429, '{"message":"too many requests"}', {"retry-after": "3"}),
        (200, '{"trades":{}}', {}),
    )
    alpaca = client(tmp_path, transport)
    alpaca.sleep = waited.append
    alpaca.latest_trades(["SPY"], IEX)
    assert waited == [3.0]
    assert alpaca.usage.rate_limited == 1 and alpaca.usage.calls == 2


def test_the_limiter_spends_at_most_half_the_basic_plan() -> None:
    slept: list[float] = []
    now = [0.0]
    limiter = RateLimiter(clock=lambda: now[0], sleep=slept.append)
    for _ in range(3):
        limiter.acquire()
    assert slept and all(abs(s - 0.6) < 1e-9 for s in slept)  # 100 a minute


def test_entitlements_are_asked_once_each(tmp_path: Path) -> None:
    transport = Scripted(
        (200, '{"quotes":{}}', {}),
        (200, '{"bars":{},"next_page_token":null}', {}),
        (403, '{"message":"subscription does not permit querying recent SIP data"}', {}),
    )
    entitled = client(tmp_path, transport).entitlements()
    assert entitled.answered and entitled.iex and entitled.sip_history and not entitled.sip_live
    assert entitled.live_feed == IEX
    assert "feed=sip" in transport.asked[1][0] and "feed=sip" in transport.asked[2][0]


def test_a_refused_key_is_said(tmp_path: Path) -> None:
    transport = Scripted((401, '{"code":40110000,"message":"request is not authorized"}', {}))
    entitled = client(tmp_path, transport).entitlements()
    assert not entitled.answered and "not authorized" in entitled.detail


def test_numbers_are_decimals_from_their_own_text(tmp_path: Path) -> None:
    body = '{"trades":{"SPY":{"t":"2026-10-09T19:59:59Z","x":"V","p":575.123456789012345678}}}'
    fetched = client(tmp_path, Scripted((200, body, {}))).latest_trades(["SPY"], SIP)
    assert fetched.value["trades"]["SPY"]["p"] == Decimal("575.123456789012345678")


def test_the_synthetic_exchange_answers_as_alpaca_does() -> None:
    transport = SyntheticTransport()
    answer = transport(
        "https://data.alpaca.markets/v2/stocks/bars?symbols=SPY,NOPE&start=2026-10-08"
        "&end=2026-10-09T03:59:00Z&feed=sip",
        {},
    )
    body = json.loads(answer.body, parse_float=Decimal)
    assert list(body["bars"]) == ["SPY"]  # a symbol it has nothing for is left out
    assert [b["t"] for b in body["bars"]["SPY"]] == ["2026-10-08T04:00:00Z"]
    assert isinstance(body["bars"]["SPY"][0]["c"], Decimal)
    assert dumps({"c": "83.123456789012345678", "x": "V"}) == '{"c":83.123456789012345678,"x":"V"}'


def test_the_live_check_asks_for_the_key_on_standard_input_alone(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    import io

    from alpaca import live

    monkeypatch.setattr("sys.stdin", io.StringIO("only-one-line\n"))
    assert live.main(["/nonexistent"]) == 2
    assert "standard input" in capsys.readouterr().out
    assert live._text({"c": Decimal("1.50"), "n": 3, "x": "V", "ok": True}) == {
        "c": "1.50",
        "n": "3",
        "x": "V",
        "ok": True,
    }
