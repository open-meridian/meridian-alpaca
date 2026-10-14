"""Connectivity's own behaviour, beyond what the suite asks: the send, the
fills as stated and as inferred, a send never answered, a restart, a replace
and a cancel the ems asks for, the firm's controls, enabling trading and the
catalogue, and an order placed outside the platform. SIMULATED ONLY: the
simulated brokerage (synthetic_broker.py), nothing reaching Alpaca."""

from __future__ import annotations

import asyncio
import json
from decimal import Decimal
from pathlib import Path

from meridian.operations import as_decimal
from meridian.plugin.v1 import operations_pb2 as ops
from meridian.suites import Recorder

from alpaca import synthetic_broker
from alpaca.synthetic_broker import SyntheticBroker
from alpaca.trading import Noted, Watched, execution_id, fill_price
from trading_cases import CODE, Rig, asking, clock, placement


def run(work: object) -> None:
    asyncio.run(work)  # type: ignore[arg-type]


def placed(recorder: Recorder, *, quantity: str = "10", **rig: object) -> Rig:
    made = Rig(recorder, **rig)  # type: ignore[arg-type]
    held = placement(quantity=quantity)
    made.holds(held)
    run(made.hear(held))
    return made


def events(recorder: Recorder) -> list[str]:
    return [ops.PlacementEvent.Name(p.event) for p in recorder.on("ReportPlacement")]


def test_a_placement_is_sent_with_its_id_as_the_client_order_id() -> None:
    recorder = Recorder("alpaca")
    rig = placed(recorder)
    (order,) = [o for o in rig.brokerage.held["orders"] if o["id"].startswith("syn-ord-")]
    assert order["client_order_id"] == "PLC-01JAE00000000000000000001"
    assert order["symbol"] == "AAPL" and order["qty"] == "10" and order["limit_price"] == "230"
    assert order["type"] == "limit" and order["time_in_force"] == "day"
    (sent,) = recorder.on("ReportPlacement")
    assert sent.event == ops.PLACEMENT_EVENT_ACKNOWLEDGED and sent.venue_order_id == order["id"]
    kept = rig.store.get(sent.raw_record.key)
    assert kept is not None and kept["call"] == "placing an order"
    assert sent.raw_record.key.startswith("trading/")
    assert json.loads(kept["request"]["body"])["client_order_id"] == order["client_order_id"]


def test_fills_read_from_the_orders_are_inferred_and_derived() -> None:
    recorder = Recorder("alpaca")
    rig = placed(recorder)
    run(rig.poll(2))
    fills = recorder.on("ReportExecution")
    assert [as_decimal(f.quantity) for f in fills] == [Decimal(4), Decimal(6)]
    assert [f.venue_execution_id for f in fills] == ["syn-ord-0001:4", "syn-ord-0001:10"]
    assert all(f.kind == ops.EXECUTION_KIND_INFERRED for f in fills)
    assert all(f.computed_settlement_date == "2026-09-30" for f in fills)
    assert all(as_decimal(f.price.amount) == Decimal(230) for f in fills)


def test_a_fill_heard_on_the_trade_updates_is_reported_as_stated() -> None:
    """The stream's fill, heard before the next read of the orders: its price
    and quantity as Alpaca stated them; the read after reports only the fill
    the brokerage made since, never the one heard again."""
    recorder = Recorder("alpaca")
    rig = placed(recorder)
    rig.broker.orders()  # the brokerage steps the order on, as time passes
    run(rig.updates.drain(rig.trader, rig.store))
    (fill,) = recorder.on("ReportExecution")
    assert fill.kind == ops.EXECUTION_KIND_VENUE_REPORTED
    assert fill.venue_execution_id == "syn-ord-0001:4"
    run(rig.trader.poll())
    assert [f.venue_execution_id for f in recorder.on("ReportExecution")] == [
        "syn-ord-0001:4",
        "syn-ord-0001:10",
    ]


def test_a_send_never_answered_is_asked_about_never_sent_again() -> None:
    recorder = Recorder("alpaca")
    rig = placed(recorder, quantity="200", symbol=synthetic_broker.LOST)
    assert events(recorder) == ["PLACEMENT_EVENT_OUTCOME_UNKNOWN"]
    run(rig.poll())
    assert events(recorder)[-1] == "PLACEMENT_EVENT_ACKNOWLEDGED"
    placed_here = [o for o in rig.brokerage.held["orders"] if o["id"].startswith("syn-ord-")]
    assert len(placed_here) == 1


def test_a_restart_never_sends_a_placement_twice(tmp_path: Path) -> None:
    sent = Noted(tmp_path / "trading" / "sent.json")
    brokerage = SyntheticBroker(clock, tmp_path / "synthetic-venue" / "orders.json")
    recorder = Recorder("alpaca")
    rig = placed(recorder, quantity="200", sent=sent, broker=brokerage)
    assert json.loads((tmp_path / "trading" / "sent.json").read_text()) == [
        "PLC-01JAE00000000000000000001"
    ]
    # Started again, the book still holding the placement queued.
    again = Recorder("alpaca")
    restarted = Rig(
        again,
        sent=Noted(tmp_path / "trading" / "sent.json"),
        broker=SyntheticBroker(clock, tmp_path / "synthetic-venue" / "orders.json"),
    )
    restarted.holds(placement(quantity="200"))
    run(restarted.trader.start())
    run(restarted.poll())
    orders = restarted.brokerage.held["orders"]
    assert len([o for o in orders if o["id"].startswith("syn-ord-")]) == 1
    assert events(again) == ["PLACEMENT_EVENT_ACKNOWLEDGED"]
    assert rig.trader.working


def test_a_replace_is_sent_and_its_new_order_followed() -> None:
    recorder = Recorder("alpaca")
    rig = placed(recorder, quantity="200")
    held = placement(
        quantity="200", state=ops.PLACEMENT_STATE_WORKING, venue_order_id="syn-ord-0001"
    )
    asked = asking(
        held,
        ops.PLACEMENT_CHANGE_KIND_REPLACE,
        limit_price=ops.Money(amount=held.limit_price.amount, currency_code="USD"),
        quantity=held.quantity,
    )
    asked.change_requests[0].placement_change.limit_price.CopyFrom(
        ops.Money(amount=ops.Decimal(low=22950, scale=2), currency_code="USD")
    )
    rig.holds(asked)
    run(rig.hear(asked))
    replaced = recorder.on("ReportPlacement")[-1]
    assert replaced.event == ops.PLACEMENT_EVENT_REPLACED
    assert replaced.venue_order_id == "syn-ord-0002"
    assert as_decimal(replaced.limit_price.amount) == Decimal("229.50")
    assert ("PATCH", "/v2/orders/syn-ord-0001") in rig.brokerage.asked


def test_a_replace_the_catalogue_lacks_is_refused_with_nothing_sent() -> None:
    recorder = Recorder("alpaca")
    rig = placed(recorder, quantity="200")
    held = placement(
        quantity="200", state=ops.PLACEMENT_STATE_WORKING, venue_order_id="syn-ord-0001"
    )
    asked = asking(
        held, ops.PLACEMENT_CHANGE_KIND_REPLACE, time_in_force=ops.TIME_IN_FORCE_GOOD_TILL_DATE
    )
    rig.holds(asked)
    run(rig.hear(asked))
    refused = recorder.on("ReportPlacement")[-1]
    assert refused.event == ops.PLACEMENT_EVENT_REJECTED
    assert refused.rejection_reason == ops.REJECTION_REASON_UNSUPPORTED
    assert not [a for a in rig.brokerage.asked if a[0] == "PATCH"]


def test_a_cancel_is_sent_and_the_next_read_reports_it() -> None:
    recorder = Recorder("alpaca")
    rig = placed(recorder, quantity="200")
    held = placement(
        quantity="200", state=ops.PLACEMENT_STATE_WORKING, venue_order_id="syn-ord-0001"
    )
    asked = asking(held, ops.PLACEMENT_CHANGE_KIND_CANCEL)
    rig.holds(asked)
    run(rig.hear(asked))
    assert ("DELETE", "/v2/orders/syn-ord-0001") in rig.brokerage.asked
    run(rig.poll())
    assert events(recorder)[-1] == "PLACEMENT_EVENT_CANCELLED"


def test_a_cancel_alpaca_refuses_ends_the_request_refused() -> None:
    recorder = Recorder("alpaca")
    rig = placed(recorder, quantity="1")
    run(rig.poll())  # filled
    held = placement(quantity="1", state=ops.PLACEMENT_STATE_WORKING, venue_order_id="syn-ord-0001")
    rig.trader.working[held.placement_id].ended = ""
    asked = asking(held, ops.PLACEMENT_CHANGE_KIND_CANCEL)
    rig.holds(asked)
    run(rig.hear(asked))
    refused = recorder.on("ReportPlacement")[-1]
    assert refused.event == ops.PLACEMENT_EVENT_REJECTED
    assert refused.rejection_reason == ops.REJECTION_REASON_VENUE_REJECTED
    assert refused.rejection_as_reported.text == "order is not cancelable"


def test_a_stop_cancels_at_alpaca_naming_the_stop() -> None:
    recorder = Recorder("alpaca")
    rig = placed(recorder, quantity="200")
    run(rig.instruct(ops.TRADING_INSTRUCTION_VALUE_STOP))
    (change,) = recorder.on("RequestPlacementChange")
    assert change.cause.entry_id == "ENT-stop-1"
    assert change.change.kind == ops.PLACEMENT_CHANGE_KIND_CANCEL
    assert ("DELETE", "/v2/orders/syn-ord-0001") in rig.brokerage.asked


def test_a_pause_holds_a_placement_back_until_lifted() -> None:
    recorder = Recorder("alpaca")
    rig = Rig(recorder)
    run(rig.instruct(ops.TRADING_INSTRUCTION_VALUE_PAUSE))
    held = placement()
    rig.holds(held)
    run(rig.hear(held))
    assert recorder.on("ReportPlacement") == []
    run(rig.instruct(ops.TRADING_INSTRUCTION_VALUE_ACTIVE))
    assert events(recorder) == ["PLACEMENT_EVENT_ACKNOWLEDGED"]


def test_enabling_trading_publishes_the_catalogue(tmp_path: Path) -> None:
    recorder = Recorder("alpaca")
    rig = Rig(recorder, enabled=False)
    rig.enabled = Noted(tmp_path / "trading" / "enabled.json")
    rig.trader = rig.make()
    run(rig.trader.start())
    (first,) = recorder.on("ReportVenueCatalogue")
    assert first.catalogue.venue_account_code == CODE and not first.catalogue.order_types
    run(rig.trader.enable(CODE, {"enabled_by": "local|ada"}))
    second = recorder.on("ReportVenueCatalogue")[-1]
    offered = {t.order_type: list(t.times_in_force) for t in second.catalogue.order_types}
    assert ops.ORDER_TYPE_LIMIT in offered and ops.TIME_IN_FORCE_GOOD_TILL_DATE not in [
        tif for tifs in offered.values() for tif in tifs
    ]
    assert second.catalogue.execution_acknowledgement == ops.EXECUTION_ACKNOWLEDGEMENT_NONE
    assert json.loads((tmp_path / "trading" / "enabled.json").read_text()) == [CODE]


def test_an_order_filled_before_the_account_was_watched_is_its_history() -> None:
    """The order the brokerage took in its own app on 2026-09-02, before the
    plugin first watched the account: no order, no fill reported (contract
    v23 reports only the fills after)."""
    recorder = Recorder("alpaca")
    rig = Rig(recorder)
    run(rig.trader.start())
    run(rig.poll(2))
    assert recorder.on("ReportExecution") == [] and recorder.on("ReportPlacement") == []
    assert synthetic_broker.OUTSIDE_ID in rig.trader.history


def test_an_order_placed_in_alpacas_app_has_its_fills_reported_on_no_placement(
    tmp_path: Path,
) -> None:
    """Contract v23: the order's fills reported on no placement, each once,
    a restart reporting none of them again (`trading/originated.json`)."""
    recorder = Recorder("alpaca")
    rig = Rig(recorder)
    rig.watched = Watched(tmp_path / "trading" / "originated.json")
    rig.trader = rig.make()
    run(rig.trader.start())
    run(rig.poll())
    venue_order = rig.brokerage.at_the_brokerage("AAPL", "5")
    run(rig.poll(3))
    fills = recorder.on("ReportExecution")
    assert [(f.placement_id, f.venue_order_id, f.side) for f in fills] == [
        ("", venue_order, ops.SIDE_BUY),
        ("", venue_order, ops.SIDE_BUY),
    ]
    assert sorted(as_decimal(f.quantity) for f in fills) == [Decimal(2), Decimal(3)]
    assert all(f.venue_client_order_id.startswith("alpaca-app-") for f in fills)
    rig.watched = Watched(tmp_path / "trading" / "originated.json")
    rig.trader = rig.make()
    run(rig.trader.start())
    run(rig.poll(2))
    assert len(recorder.on("ReportExecution")) == 2, "a restart reports no fill twice"


def test_a_dk_tells_the_venue_nothing_and_a_later_fill_is_unmatched_again() -> None:
    recorder = Recorder("alpaca")
    rig = Rig(recorder)
    run(rig.trader.start())
    run(rig.poll())
    venue_order = rig.brokerage.at_the_brokerage("AAPL", "5")
    run(rig.poll())
    (first,) = rig.executions
    answered = ops.Execution(
        execution_id=first.execution_id,
        venue_order_id=venue_order,
        state=ops.EXECUTION_STATE_DONT_KNOW,
        dk_reason=ops.DK_REASON_NO_MATCHING_ORDER,
    )
    asked = len(rig.brokerage.asked)
    recorder.answer("ExecutionRecorded", lambda _: ops.ExecutionRecordedEvent(execution=answered))
    run(recorder.receive(execution_recorded=rig.trader.on_execution))
    assert len(rig.brokerage.asked) == asked, "nothing sent to the venue on a DK"
    run(rig.poll())
    later = recorder.on("ReportExecution")[-1]
    assert later.placement_id == "" and later.venue_order_id == venue_order


def test_a_status_alpaca_lists_and_the_mapping_does_not_is_unknown_then_known() -> None:
    recorder = Recorder("alpaca")
    rig = placed(recorder, symbol=synthetic_broker.ODD)
    (unknown,) = recorder.on("ReportPlacement")
    assert unknown.unknown_reason == ops.UNKNOWN_REASON_VENUE_STATUS_NOT_MAPPED
    assert unknown.status_as_reported.code == "held"
    run(rig.poll())
    assert events(recorder)[1] == "PLACEMENT_EVENT_ACKNOWLEDGED"


def test_a_fills_price_from_two_averages() -> None:
    assert fill_price(Decimal(4), Decimal("10"), Decimal(10), Decimal("11.2")) == (
        Decimal("12"),
        False,
    )
    price, rounded = fill_price(Decimal(1), Decimal(1), Decimal(4), Decimal("1.1"))
    assert rounded and price == Decimal("1.13333333")
    assert execution_id("syn-ord-1", Decimal("4.0")) == "syn-ord-1:4"
