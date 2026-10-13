"""The connectivity suite (contract v22): every case Alpaca presents, from the
plugin's own trading code and the simulated brokerage.

Each case is mapped to what Alpaca's Trading API answers for it, through the
simulated brokerage (synthetic_broker.py: a symbol for each answer, a
quantity that works or fills), and run through this plugin's own Trader
(trading.py) over its own adapter (venue.py) and client (broker.py),
against the runner's recorder; only the sidecar is stood in for. SIMULATED
ONLY: nothing reaches Alpaca.

Seven cases are not presented, each with why (`NOT_PRESENTED`), as the
suite lets a case marked `about` or `about_field` be. And one required case
is not produced (`UNPRODUCED`): Alpaca states no fee on a placement that
filled nothing, so the plugin has no charge to report; whether the suite
marks the case `about` is the product owner's to rule.
"""

from __future__ import annotations

import asyncio

import pytest
from meridian.plugin.v1 import operations_pb2 as ops
from meridian.suites import Recorder, names, run, suite

from alpaca import synthetic_broker
from trading_cases import Producer, Rig, placed_and_filled, placement

NOT_PRESENTED = {
    "reads-a-calendar": (
        "Alpaca's fills state no settlement date, so the plugin asks the date read for each "
        "(ComputeDate); it reads no calendar's days"
    ),
    "records-a-locate": "Alpaca offers no locate API",
    "resolves-a-venue-symbol-by-the-facts-date": (
        "Alpaca offers no locate API, so the plugin resolves no locate's symbol"
    ),
    "resolves-an-executions-venue": "Alpaca states no fill's place of execution",
    "reports-a-venue-not-held": "Alpaca states no fill's place of execution",
    "reads-whether-releases-wait-for-compliance": (
        "it places what its ems released; whether releases wait for compliance changes "
        "nothing it sends"
    ),
    "hears-an-accounts-attributes-change": (
        "it places what its ems released; whether releases wait for compliance changes "
        "nothing it sends"
    ),
}

#: Required cases the plugin does not produce, each with the exact reason
#: Alpaca never presents it: left for the product owner to rule on.
UNPRODUCED = {
    "reports-a-charge": (
        "Alpaca's account activities state fees (FEE: TAF, REG and the like) by day, naming "
        "no order, and its orders carry no fee: Alpaca states no charge on a placement that "
        "filled nothing"
    ),
}


async def acknowledged(recorder: Recorder) -> None:
    rig = Rig(recorder)
    held = placement()
    rig.holds(held)
    await rig.hear(held)


async def another_instances(recorder: Recorder) -> None:
    rig = Rig(recorder)
    held = placement(instance="elsewhere")
    rig.holds(held)
    await rig.hear(held)
    await rig.poll()


async def not_offered(recorder: Recorder) -> None:
    """Good till date, which Alpaca takes none of."""
    rig = Rig(recorder)
    held = placement(tif=ops.TIME_IN_FORCE_GOOD_TILL_DATE)
    rig.holds(held)
    await rig.hear(held)


async def a_strategy(recorder: Recorder) -> None:
    rig = Rig(recorder)
    held = placement(strategy=ops.StrategyInstruction(kind=ops.STRATEGY_KIND_VWAP))
    rig.holds(held)
    await rig.hear(held)


def by_symbol(symbol: str, quantity: str = "10", polls: int = 0) -> Producer:
    async def produce(recorder: Recorder) -> None:
        rig = Rig(recorder, symbol)
        held = placement(quantity=quantity)
        rig.holds(held)
        await rig.hear(held)
        await rig.poll(polls)

    return produce


async def the_same_fill_twice(recorder: Recorder) -> None:
    """One fill read, then read again by the plugin started again before the
    book held it: the same derived ID both times."""
    rig = Rig(recorder)
    held = placement(quantity="1")
    rig.holds(held)
    await rig.hear(held)
    await rig.poll()
    worked = next(iter(rig.trader.working.values()))
    again = placement(
        quantity="1", state=ops.PLACEMENT_STATE_WORKING, venue_order_id=worked.venue_order_id
    )
    rig.holds(again)
    rig.executions.clear()
    rig.trader = rig.make()
    await rig.trader.start()
    await rig.poll()


async def busted(recorder: Recorder) -> None:
    rig = Rig(recorder, synthetic_broker.BUSTED)
    held = placement(quantity="1")
    rig.holds(held)
    await rig.hear(held)
    await rig.poll(3)


async def renamed(recorder: Recorder) -> None:
    """Filled under a symbol renamed while it worked, which names none of the
    deployment's records."""
    rig = Rig(recorder, synthetic_broker.RENAMED)
    recorder.answer(
        "ResolveIdentifier",
        lambda _: ops.ResolveIdentifierResult(found=False, miss_reason=ops.MISS_REASON_NOT_FOUND),
    )
    held = placement(quantity="1")
    rig.holds(held)
    await rig.hear(held)
    await rig.poll()


async def paused(recorder: Recorder) -> None:
    rig = Rig(recorder)
    await rig.instruct(ops.TRADING_INSTRUCTION_VALUE_PAUSE)
    held = placement()
    rig.holds(held)
    await rig.hear(held)


async def stopped(recorder: Recorder) -> None:
    rig = Rig(recorder)
    held = placement(quantity="200")
    rig.holds(held)
    await rig.hear(held)
    await rig.instruct(ops.TRADING_INSTRUCTION_VALUE_STOP)


async def started(recorder: Recorder) -> None:
    rig = Rig(recorder)
    await rig.trader.start()


async def caught_up(recorder: Recorder) -> None:
    rig = Rig(recorder)
    await rig.trader.start()
    await rig.poll()


PRODUCERS: dict[str, Producer] = {
    "acknowledges-a-placement-naming-it": acknowledged,
    "hears-no-placement-of-another-instance": another_instances,
    "sends-the-venue-account-code-its-ems-set": acknowledged,
    "rejects-what-the-venue-does-not-offer": not_offered,
    "rejects-a-strategy-its-broker-lacks": a_strategy,
    "venue-rejected-with-its-reason-as-reported": by_symbol(synthetic_broker.REJECTED),
    "unmapped-status-is-outcome-unknown": by_symbol(synthetic_broker.ODD),
    "resolves-an-unanswered-send-by-asking": by_symbol(synthetic_broker.LOST, "200", 1),
    "reports-a-fill-with-its-settlement-date": placed_and_filled,
    "derives-the-same-execution-id-twice": the_same_fill_twice,
    "reports-a-bust": busted,
    "sends-nothing-new-on-a-pause": paused,
    "cancels-at-the-venue-on-a-stop": stopped,
    "publishes-its-catalogue": started,
    "reads-the-instrument-it-places": acknowledged,
    "reads-its-placements-orders-on-start": started,
    "catches-up-placements-after-a-loss": caught_up,
    "reads-the-instructions-in-force-on-start": started,
    "reports-a-symbol-it-cannot-resolve": renamed,
}


def test_every_case_is_mapped_said_not_presented_or_said_unproduced() -> None:
    assert set(names("connectivity")) == set(PRODUCERS) | set(NOT_PRESENTED) | set(UNPRODUCED)
    assert not set(PRODUCERS) & set(NOT_PRESENTED)
    held = {case.name: case.about or case.about_field for case in suite("connectivity").cases}
    assert all(held[name] for name in NOT_PRESENTED), "a required case is never skipped"
    assert not any(held[name] for name in UNPRODUCED), "an about case is not presented instead"


def test_the_connectivity_suite_passes_but_for_the_cases_alpaca_never_presents() -> None:
    report = run("connectivity", PRODUCERS, not_presented=NOT_PRESENTED, instance_id="alpaca")
    assert set(report.failures) == set(UNPRODUCED), report.failures
    assert len(report.passed_cases) == 19
    assert sorted(report.not_presented) == sorted(NOT_PRESENTED)


@pytest.mark.xfail(strict=True, reason="reports-a-charge: " + UNPRODUCED["reports-a-charge"])
def test_every_required_case_passes() -> None:
    report = run("connectivity", PRODUCERS, not_presented=NOT_PRESENTED, instance_id="alpaca")
    assert report.passed, report.failures


def test_a_plugin_sending_on_a_pause_fails_the_suite() -> None:
    """The suite tests the outcome: a trader that ignores a pause fails the
    case that says so."""

    async def ignoring(recorder: Recorder) -> None:
        rig = Rig(recorder)
        await rig.instruct(ops.TRADING_INSTRUCTION_VALUE_PAUSE)
        rig.trader.instructions.clear()
        held = placement()
        rig.holds(held)
        await rig.hear(held)

    report = run(
        "connectivity",
        {**PRODUCERS, "sends-nothing-new-on-a-pause": ignoring},
        not_presented=NOT_PRESENTED,
        instance_id="alpaca",
    )
    assert set(report.failures) == {"sends-nothing-new-on-a-pause", *UNPRODUCED}


def test_a_read_only_account_rejects_every_order_unsupported() -> None:
    async def read_only(recorder: Recorder) -> None:
        rig = Rig(recorder, enabled=False)
        held = placement()
        rig.holds(held)
        await rig.hear(held)

    recorder = Recorder("alpaca")
    asyncio.run(read_only(recorder))
    (sent,) = recorder.on("ReportPlacement")
    assert sent.event == ops.PLACEMENT_EVENT_REJECTED
    assert sent.rejection_reason == ops.REJECTION_REASON_UNSUPPORTED
    assert recorder.on("ResolveInstrument") == [], "nothing read for what is not sent"
