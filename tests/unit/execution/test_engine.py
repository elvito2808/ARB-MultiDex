"""Offline acceptance tests with the real S2 types and owner simulation settings."""

import sqlite3
from concurrent.futures import ThreadPoolExecutor
from contextlib import nullcontext
from dataclasses import replace
from pathlib import Path
from threading import Barrier
from unittest.mock import patch

import pytest

from arb_multidex.connections.mock import SAMPLE_MS, MockClock, MockKuCoin, MockSaucerSwap, mock_gas
from arb_multidex.execution import Balance, FailurePlan, SimulationEngine, WorkerConfig
from arb_multidex.kucoin.market import top
from arb_multidex.opportunity import (
    Amount,
    Binding,
    Call,
    Cost,
    DecisionContext,
    Direction,
    ExecutionGuard,
    OpportunityDetector,
    Rate,
    Relationship,
    SourceState,
    Terms,
)
from arb_multidex.quotes import QuoteConfig, QuoteProducer
from arb_multidex.quotes.fees import GasPolicy, SpotFeePolicy

OWNER = Path(__file__).resolve().parents[3] / "config/simulation.owner.example.json"
BUY_DEX, SELL_DEX = Direction.BUY_DEX, Direction.BUY_KUCOIN
VOLUME = 25_000_000_000
GROSS = 12_000_000
GAS = 5
GAS_VALUE = 3
MIN = 150_000
FAIL = ("FAILED",) * 3


@pytest.fixture
def worker():
    return WorkerConfig.from_owner(OWNER)


@pytest.fixture
def guard(tmp_path):
    shared = ExecutionGuard(tmp_path / "execution.sqlite")
    yield shared
    shared.close()


@pytest.fixture
def factory(tmp_path, guard):
    engines = []

    def build(*workers, name="execution.sqlite", **options):
        engine = SimulationEngine(
            tmp_path / name,
            workers,
            clock=options.pop("clock", MockClock()),
            guard=options.pop("guard", guard),
            **options,
        )
        engines.append(engine)
        return engine

    yield build
    for engine in engines:
        engine.close()


@pytest.fixture
def handoff(guard):
    """Issue deterministic synthetic quotes through the real detector and durable guard."""
    issued = {}

    def issue(worker, direction=BUY_DEX, identifier="run", profit=MIN, *, rate_lifetime=5000):
        key = worker, direction, identifier, profit, rate_lifetime
        if key in issued:
            return issued[key]
        clock = MockClock()
        dex_buy = direction == BUY_DEX
        dex_final = GROSS + GAS_VALUE if dex_buy else GROSS - GAS_VALUE
        spot_final = dex_final + profit if dex_buy else dex_final - profit
        gas_cost = (Cost("gas", "SEPARATE", worker.gas_asset, 8, "mock/rate"),)
        # A lower detector threshold allows the S3 owner threshold to be tested independently.
        terms = Terms(worker.volume, Amount(min(profit, MIN), 6))
        rel = Relationship(
            worker.relationship_id,
            "m1",
            "fees",
            Binding(worker.dex, 5000, gas_cost, gas_cost),
            Binding(worker.kucoin, 5000, (), ()),
            (worker.dex.base_id, worker.kucoin.base_id),
            (worker.dex.settlement_id, worker.kucoin.settlement_id),
            terms,
            terms,
            100,
        )
        epoch = "mock-" + identifier
        gas = {
            "costId": "gas",
            "treatment": "SEPARATE",
            "assetId": worker.gas_asset,
            "units": str(GAS),
            "scale": 8,
            "rateNumerator": "1",
            "rateDenominator": "2",
            "settlementCostUnits": str(GAS_VALUE),
            "rateRef": "fixture/rate",
        }
        calls = {}
        for dex, final in ((True, dex_final), (False, spot_final)):
            side = "BUY" if dex == dex_buy else "SELL"
            original = rel.request(direction, side)
            response = dict(
                original,
                status="OK",
                connectionEpoch=epoch,
                observedAt="2026-11-23T22:49:17.000Z",
                grossSettlementUnits=str(GROSS if dex else final),
                settlementUnits=str(final),
                includedCosts=[gas] if dex else [],
            )
            calls[direction, side] = Call(original, response)
        rate = Rate(
            "mock/rate",
            worker.gas_asset,
            8,
            worker.dex.settlement_id,
            6,
            1,
            2,
            clock(),
            clock() + rate_lifetime,
        )
        ctx = DecisionContext(
            clock(),
            {m.source_id: SourceState(epoch, True, clock()) for m in (worker.dex, worker.kucoin)},
            {"fixture/rate": rate},
        )
        detector = OpportunityDetector(guard, lambda _: nullcontext(ctx))
        detector.register(rel)
        req = detector.evaluate(worker.relationship_id, calls).execution_request
        if req is not None:
            issued[key] = req
        return req

    return issue


def balances(engine, worker):
    return tuple(engine.balance(account, asset) for account, asset, _ in worker.assets)


@pytest.mark.parametrize("direction", [BUY_DEX, SELL_DEX])
def test_owner_balance_changes_exact_profit_boundary(worker, factory, handoff, direction):
    e = factory(worker)
    initial = (1000 * 10**8, 500 * 10**6, 10 * 10**8, 1000 * 10**8, 500 * 10**6)
    assert balances(e, worker) == tuple(Balance(n, 0) for n in initial)
    req = handoff(worker, direction)
    with (
        patch("socket.socket", side_effect=AssertionError("network access")),
        patch("subprocess.Popen", side_effect=AssertionError("external process")),
    ):
        result = e.execute(req)
    assert result.status == "COMPLETED"
    assert [(a.leg, a.status) for a in result.attempts] == [
        ("DEX", "SUCCESS"),
        ("KUCOIN", "SUCCESS"),
    ]
    if direction == BUY_DEX:
        deltas = (VOLUME, -GROSS, -GAS, -VOLUME, GROSS + GAS_VALUE + MIN)
    else:
        deltas = (-VOLUME, GROSS, -GAS, VOLUME, -(GROSS - GAS_VALUE - MIN))
    assert deltas[1] + deltas[4] - GAS_VALUE == MIN
    assert balances(e, worker) == tuple(Balance(n + delta, 0) for n, delta in zip(initial, deltas))


@pytest.mark.parametrize(
    "direction,index,required",
    [
        (SELL_DEX, 0, VOLUME),
        (BUY_DEX, 1, GROSS),
        (BUY_DEX, 2, GAS),
        (BUY_DEX, 3, VOLUME),
        (SELL_DEX, 4, GROSS - GAS_VALUE - MIN),
    ],
)
def test_one_atom_short_rejects_entire_reservation(
    worker, factory, handoff, direction, index, required
):
    opening = list(worker.opening)
    opening[index] = required - 1
    worker = replace(worker, opening=tuple(opening))
    e = factory(worker)
    req = handoff(worker, direction)
    result = e.execute(req)
    assert result.status == "REJECTED" and "insufficient" in result.reason
    assert result.attempts == ()
    assert balances(e, worker) == tuple(Balance(n, 0) for n in opening)


@pytest.mark.parametrize(
    "case",
    [
        "profit",
        "forged_profit",
        "volume",
        "pool",
        "expired",
        "future",
        "live",
        "live_dex",
        "live_kucoin",
        "unaccepted",
        "direction",
        "missing_gas",
        "wrong_quote",
        "wrong_rate",
        "missing_cost",
    ],
)
def test_invalid_handoffs_fail_before_reservation(worker, factory, handoff, case):
    e = factory(worker)
    req = handoff(worker, profit=MIN - 1 if case == "profit" else MIN)
    if case == "forged_profit":
        req = replace(req, net_profit=Amount(MIN + 1, 6))
    elif case == "volume":
        req = replace(req, buy=replace(req.buy, base=Amount(VOLUME + 1, 8)))
    elif case == "pool":
        req = replace(req, buy=replace(req.buy, market=replace(worker.dex, pool="unrelated")))
    elif case == "expired":
        req = replace(req, expires_ms=SAMPLE_MS)
    elif case == "future":
        req = replace(req, evaluated_ms=SAMPLE_MS + 1)
    elif case == "live":
        req = replace(req, mode="LIVE")
    elif case == "live_dex":
        req = replace(req, buy=replace(req.buy, mode="LIVE"))
    elif case == "live_kucoin":
        req = replace(req, sell=replace(req.sell, mode="LIVE"))
    elif case == "unaccepted":
        req = replace(req, status="PENDING")
    elif case == "direction":
        req = replace(req, direction="unknown")
    elif case == "missing_gas":
        req = replace(req, buy=replace(req.buy, costs=()))
    elif case == "wrong_quote":
        req = replace(req, buy=replace(req.buy, request_id="different"))
    elif case == "wrong_rate":
        req = replace(req, buy=replace(req.buy, costs=(replace(req.buy.costs[0], denominator=1),)))
    elif case == "missing_cost":
        req = replace(req, buy=replace(req.buy, gross_settlement=req.buy.settlement))
    result = e.execute(req)
    assert result.status == "REJECTED" and result.attempts == ()
    assert balances(e, worker) == tuple(Balance(n, 0) for n in worker.opening)


@pytest.mark.parametrize(
    "dex,spot,terminal,history",
    [
        (FAIL, (), "DEX_FAILED", ["DEX"] * 3),
        (("FAILED", "FAILED"), ("FAILED", "FAILED"), "COMPLETED", ["DEX"] * 3 + ["KUCOIN"] * 3),
        ((), FAIL, "INCOMPLETE_ARBITRAGE", ["DEX"] + ["KUCOIN"] * 3),
    ],
)
def test_retry_limits_and_confirmed_inventory(
    worker, factory, handoff, dex, spot, terminal, history
):
    e = factory(worker)
    req = handoff(worker)
    result = e.execute(req, plan=FailurePlan(dex, spot))
    assert result.status == terminal and [a.leg for a in result.attempts] == history
    assert all(a.number == history[: i + 1].count(a.leg) for i, a in enumerate(result.attempts))
    expect = list(worker.opening)
    if terminal != "DEX_FAILED":
        expect[:3] = [expect[0] + VOLUME, expect[1] - GROSS, expect[2] - GAS]
    if terminal == "COMPLETED":
        expect[3:] = [expect[3] - VOLUME, expect[4] + req.sell.settlement.units]
    assert balances(e, worker) == tuple(Balance(n, 0) for n in expect)
    assert e.execute(req) == result


@pytest.mark.parametrize("cut", range(9))
def test_reopen_at_each_durable_boundary(worker, factory, handoff, cut):
    req = handoff(worker)
    e = factory(worker)
    e.submit(req, plan=FailurePlan(("FAILED",), ("FAILED",)))
    for _ in range(cut):
        e.advance(req.execution_id)
    e.close()
    restarted = factory(worker)
    restarted.recover()
    result = restarted.execute(req)
    assert result.status == "COMPLETED"
    assert [(a.leg, a.number, a.status) for a in result.attempts] == [
        ("DEX", 1, "FAILED"),
        ("DEX", 2, "SUCCESS"),
        ("KUCOIN", 1, "FAILED"),
        ("KUCOIN", 2, "SUCCESS"),
    ]
    assert balances(restarted, worker)[2] == Balance(worker.opening[2] - GAS, 0)
    with pytest.raises(ValueError, match="different terms"):
        restarted.execute(replace(req, mode="LIVE"))


@pytest.mark.parametrize("venue", ["DEX", "KUCOIN"])
def test_unknown_holds_resources_until_exact_receipt(worker, factory, handoff, venue):
    req = handoff(worker)
    e = factory(worker)
    plan = FailurePlan(dex=("UNKNOWN",)) if venue == "DEX" else FailurePlan(kucoin=("UNKNOWN",))
    result = e.execute(req, plan=plan)
    assert result.status == "AWAITING_RECONCILIATION" and result.attempts[-1].leg == venue
    assert e.stop_worker(worker.relationship_id) == (result,)
    e.close()
    e = factory(worker)
    assert e.recover() == (result,)
    assert e.advance(req.execution_id) == result
    with pytest.raises(ValueError, match="unresolved"):
        e.manual_restart(worker.relationship_id)
    with pytest.raises(ValueError, match="matching unknown"):
        e.reconcile(req.execution_id, venue, 2, "FAILED")
    assert balances(e, worker)[3].reserved == VOLUME
    assert balances(e, worker)[2].reserved == (GAS if venue == "DEX" else 0)
    result = e.reconcile(req.execution_id, venue, 1, "FAILED")
    assert result.status == "COMPLETED"
    assert [a.status for a in result.attempts if a.leg == venue] == ["FAILED", "SUCCESS"]
    assert balances(e, worker)[2] == Balance(worker.opening[2] - GAS, 0)


@pytest.mark.parametrize("last", ["SUCCESS", "FAILED"])
def test_partial_fill_retries_only_remaining_inventory(worker, factory, handoff, last):
    e = factory(worker)
    req = handoff(worker, profit=MIN + 2)
    result = e.execute(req, plan=FailurePlan(kucoin=("PARTIAL", "FAILED", last)))
    assert result.status == ("COMPLETED" if last == "SUCCESS" else "INCOMPLETE_ARBITRAGE")
    filled = VOLUME if last == "SUCCESS" else VOLUME // 2
    credit = req.sell.settlement.units * filled // VOLUME
    assert balances(e, worker)[3:] == (
        Balance(worker.opening[3] - filled, 0),
        Balance(worker.opening[4] + credit, 0),
    )


def independent(worker, number):
    return replace(
        worker,
        relationship_id=f"rel-{number}",
        dex=replace(worker.dex, network=f"sim-net-{number}", source_id=f"sim-dex-{number}"),
    )


def test_three_workers_pause_isolation_and_manual_restart(worker, factory, handoff):
    workers = (worker, independent(worker, 2), independent(worker, 3))
    e = factory(*workers)

    def trade(w, n, incomplete=True):
        req = handoff(w, identifier=f"{w.relationship_id}-{n}")
        return e.execute(req, plan=FailurePlan(kucoin=FAIL if incomplete else ()))

    for i in range(4):
        assert trade(worker, i).status == "INCOMPLETE_ARBITRAGE"
        assert e.worker_state(worker.relationship_id).status == "RUNNING"
    assert trade(workers[1], 0).status == "INCOMPLETE_ARBITRAGE"
    assert trade(workers[1], 1, False).status == "COMPLETED"
    assert e.worker_state(workers[1].relationship_id).consecutive_incomplete == 0
    assert trade(worker, 4).status == "INCOMPLETE_ARBITRAGE"
    e.close()
    e = factory(*workers)
    assert e.recover() == ()
    state = e.worker_state(worker.relationship_id)
    assert (state.status, state.consecutive_incomplete) == ("TRADING_PAUSED", 5)
    assert trade(worker, 4).status == "INCOMPLETE_ARBITRAGE"
    assert trade(worker, 5).reason == "TRADING_PAUSED"
    assert trade(workers[1], 2, False).status == trade(workers[2], 0, False).status == "COMPLETED"
    e.manual_restart(worker.relationship_id)
    assert e.worker_state(worker.relationship_id).consecutive_incomplete == 0
    assert trade(worker, 6, False).status == "COMPLETED"


def test_graceful_stop_drains_recorded_execution(worker, factory, handoff):
    e = factory(worker)
    req = handoff(worker)
    e.submit(req)
    assert e.advance(req.execution_id).attempts[-1].status == "SUBMITTED"
    assert e.stop_worker(worker.relationship_id)[0].status == "COMPLETED"
    req = handoff(worker, identifier="later")
    assert e.execute(req).reason == "STOPPED"
    assert all(b.reserved == 0 for b in balances(e, worker))


def test_concurrent_reservations_and_duplicate_drivers(worker, factory, handoff):
    # Two independent engines and connections compete for one shared spot balance.
    worker = replace(worker, opening=(*worker.opening[:3], VOLUME, worker.opening[4]))
    workers = (worker, independent(worker, 2))
    engines = [factory(*workers) for _ in workers]
    calls = [handoff(w, identifier=w.relationship_id) for w in workers]
    start = Barrier(2)

    def submit(i):
        req = calls[i]
        start.wait()
        return engines[i].submit(req)

    with ThreadPoolExecutor(2) as pool:
        results = list(pool.map(submit, range(2)))
        assert sorted(r.status for r in results) == ["DEX", "REJECTED"]
        winner = next(i for i, r in enumerate(results) if r.status == "DEX")
        assert balances(engines[0], workers[winner])[3] == Balance(VOLUME, VOLUME)
        assert all(b.reserved == 0 for b in balances(engines[1], workers[1 - winner])[:3])
        req = calls[winner]
        finals = list(pool.map(lambda e: e.execute(req), engines))
    assert finals[0] == finals[1] and len(finals[0].attempts) == 2
    assert balances(engines[0], workers[winner])[3] == Balance(0, 0)


def test_unresolved_worker_allows_independent_but_blocks_same_network(worker, factory, handoff):
    same = replace(worker, relationship_id="same")
    other = independent(worker, 2)
    e = factory(worker, same, other)
    for w, expected in [
        (worker, "AWAITING_RECONCILIATION"),
        (same, "REJECTED"),
        (other, "COMPLETED"),
    ]:
        req = handoff(w, identifier=w.relationship_id)
        if w == same:
            assert req is None  # S2 holds this network while the DEX is unresolved.
            continue
        result = e.execute(
            req,
            plan=FailurePlan(dex=("UNKNOWN",)) if w == worker else FailurePlan(),
        )
        assert result.status == expected


@pytest.mark.parametrize("direction", [BUY_DEX, SELL_DEX])
def test_real_s1_s2_s3_handoff(worker, factory, guard, direction):
    clock = MockClock()
    cfg = QuoteConfig(worker.relationship_id, "m1", "fees", VOLUME, 5000)
    dex = QuoteProducer(MockSaucerSwap(clock), cfg, GasPolicy("mock/rate", mock_gas))
    spot = QuoteProducer(MockKuCoin("HBAR-USDT", 8, 6, clock), cfg, SpotFeePolicy(10))
    bid, ask = ("0.060", "0.061") if direction == BUY_DEX else ("0.040", "0.041")
    spot.feed.snapshot = top(SAMPLE_MS, bid, "300", ask, "300")
    dc = (Cost("pool-fee", "EMBEDDED"), Cost("gas", "SEPARATE", worker.gas_asset, 8, "mock/rate"))
    sc = (Cost("spot-fee", "SEPARATE", worker.kucoin.settlement_id, 6),)
    terms = Terms(worker.volume, worker.minimum_profit)
    rel = Relationship(
        worker.relationship_id,
        cfg.mapping_revision,
        cfg.fee_policy_id,
        Binding(worker.dex, 5000, dc, dc),
        Binding(worker.kucoin, 5000, sc, sc),
        (worker.dex.base_id, worker.kucoin.base_id),
        (worker.dex.settlement_id, worker.kucoin.settlement_id),
        terms,
        terms,
        100,
    )
    calls = {}
    for producer in (dex, spot):
        side = "BUY" if (producer is dex) == (direction == BUY_DEX) else "SELL"
        original = rel.request(direction, side)
        calls[direction, side] = Call(original, producer.quote(original))
    gas = mock_gas(dex.feed, dex.feed.snapshot, "BUY", VOLUME)
    rate = Rate(
        gas.rate_source,
        worker.gas_asset,
        8,
        worker.dex.settlement_id,
        6,
        gas.numerator,
        gas.denominator,
        gas.observed_ms,
        gas.valid_until_ms,
    )
    ctx = DecisionContext(
        clock(),
        {p.feed.source_id: SourceState(p.feed.epoch, True, clock()) for p in (dex, spot)},
        {gas.rate_ref: rate},
    )
    detector = OpportunityDetector(guard, lambda _: nullcontext(ctx))
    detector.register(rel)
    req = detector.evaluate(worker.relationship_id, calls).execution_request
    assert req is not None and guard.pending() == (req.execution_id,)
    e = factory(worker, guard=guard, clock=clock)
    assert e.execute(req).status == "COMPLETED"
    assert guard.pending() == ()
    assert detector.evaluate(worker.relationship_id, calls).execution_request is None


def test_recover_guard_claim_before_and_after_execution(worker, factory, handoff, guard):
    req = handoff(worker)
    e = factory(worker)
    e.close()
    restarted = factory(worker)
    (recovered,) = restarted.recover()
    assert recovered.status == "COMPLETED" and recovered.execution_id == req.execution_id
    assert guard.pending() == ()
    assert restarted.execute(req) == recovered
    req = handoff(worker, identifier="after")
    with (
        patch.object(guard, "resolve", side_effect=OSError("simulated crash")),
        pytest.raises(OSError, match="simulated crash"),
    ):
        restarted.execute(req)
    restarted.close()
    assert guard.pending() == (req.execution_id,)
    e = factory(worker)
    (result,) = e.recover()
    assert result.status == "COMPLETED" and len(result.attempts) == 2
    assert balances(e, worker)[2] == Balance(worker.opening[2] - 2 * GAS, 0)
    assert guard.pending() == ()


def test_expiry_after_submission_verifies_dex_without_stale_kucoin_order(worker, factory, handoff):
    clock = MockClock()
    e = factory(worker, clock=clock)
    req = handoff(worker)
    e.submit(req)
    assert e.advance(req.execution_id).attempts[-1].status == "SUBMITTED"
    clock.now_ms = req.expires_ms
    result = e.recover()[0]
    assert result.status == "INCOMPLETE_ARBITRAGE"
    assert [(a.leg, a.status) for a in result.attempts] == [("DEX", "SUCCESS")]
    assert balances(e, worker)[2] == Balance(worker.opening[2] - GAS, 0)
    assert all(b.reserved == 0 for b in balances(e, worker))


def test_settlement_error_rolls_back_attempt_balances_and_reservations(worker, factory, handoff):
    e = factory(worker)
    req = handoff(worker)
    e.submit(req)
    pending = e.advance(req.execution_id)
    before = balances(e, worker)
    from arb_multidex.execution import journal

    settle = journal.settle

    def interrupted(*args):
        settle(*args)
        raise OSError("simulated loss before recording receipt")

    with patch.object(journal, "settle", interrupted), pytest.raises(OSError):
        e.advance(req.execution_id)
    assert e.result(req.execution_id) == pending and balances(e, worker) == before
    e.close()
    restarted = factory(worker)
    (final,) = restarted.recover()
    assert final.status == "COMPLETED" and len(final.attempts) == 2
    assert balances(restarted, worker)[2] == Balance(worker.opening[2] - GAS, 0)


def test_three_simultaneous_relationships_with_exact_large_inventory(worker, factory, handoff):
    n = 10**70 + 7
    worker = replace(worker, opening=(n,) * 5)
    workers = [worker, independent(worker, 2), independent(worker, 3)]
    engines = [factory(*workers) for _ in workers]
    calls = [handoff(w, identifier=w.relationship_id) for w in workers]
    start = Barrier(3)

    def submit(i):
        start.wait()
        req = calls[i]
        return engines[i].submit(req)

    with ThreadPoolExecutor(3) as pool:
        assert [r.status for r in pool.map(submit, range(3))] == ["DEX"] * 3
        assert balances(engines[0], worker)[3] == Balance(n, 3 * VOLUME)
        finals = list(pool.map(lambda i: engines[i].execute(calls[i]), range(3)))
    assert [r.status for r in finals] == ["COMPLETED"] * 3
    assert balances(engines[0], worker)[3] == Balance(n - 3 * VOLUME, 0)


def assert_unjournaled(engine, execution_id):
    with engine.journal.transaction(write=False) as db:
        for table in ("sim_runs", "sim_holds", "sim_attempts", "sim_lost_handoffs"):
            assert not db.execute(f"SELECT 1 FROM {table} WHERE id=?", (execution_id,)).fetchone()


@pytest.mark.parametrize("direction", [BUY_DEX, SELL_DEX])
@pytest.mark.parametrize(
    "change", ["gross_and_gas", "rate_observation", "epoch", "market", "mapping", "fee_policy"]
)
def test_modified_evidence_preserves_genuine_admission(
    worker, factory, handoff, guard, direction, change
):
    e = factory(worker)
    req = handoff(worker, direction)
    side = "buy" if direction == BUY_DEX else "sell"
    leg = getattr(req, side)
    gas = leg.costs[0]
    if change == "gross_and_gas":
        gas = replace(gas, native=Amount(7, 8), settlement_units=4)
        gross = leg.gross_settlement.units + (-1 if direction == BUY_DEX else 1)
        leg = replace(leg, gross_settlement=Amount(gross, 6), costs=(gas,))
    elif change == "rate_observation":
        leg = replace(
            leg, costs=(replace(gas, rate=replace(gas.rate, observed_ms=gas.rate.observed_ms - 1)),)
        )
    elif change == "epoch":
        leg = replace(leg, epoch="unadmitted")
    elif change == "market":
        leg = replace(leg, market=replace(leg.market, pool="unadmitted"))
    changed = replace(req, **{side: leg})
    if change == "mapping":
        changed = replace(req, mapping_revision="other")
    if change == "fee_policy":
        changed = replace(req, fee_policy_id="other")
    assert changed.execution_id == req.execution_id and changed.net_profit == req.net_profit
    with patch.object(guard, "verify_pending", wraps=guard.verify_pending) as verified:
        assert e.execute(changed).status == "REJECTED"
        verified.assert_called_once_with(changed)
    assert_unjournaled(e, req.execution_id)
    assert guard.pending() == (req.execution_id,)
    assert e.execute(req).status == "COMPLETED"
    with pytest.raises(ValueError, match="different terms"):
        e.execute(changed)


def test_books_only_the_verified_object_and_replays_without_reverification(
    worker, factory, handoff, guard
):
    from arb_multidex.execution import engine as module

    e = factory(worker)
    request = handoff(worker)
    reads = []
    verify = guard.verify_pending

    def read(candidate):
        assert_unjournaled(e, candidate.execution_id)
        reads.append(verify(candidate))
        return reads[-1]

    with (
        patch.object(guard, "verify_pending", read),
        patch.object(module, "prepare", wraps=module.prepare) as prepare,
    ):
        completed = e.execute(request)
    assert completed.status == "COMPLETED"
    assert len(reads) == 1 and reads[0] is not request
    assert prepare.call_args.args[0] is reads[0]
    assert guard.pending() == ()
    with patch.object(
        guard, "verify_pending", side_effect=AssertionError("spent admission lookup")
    ):
        assert e.execute(request) == completed


@pytest.mark.parametrize("damage", ["missing", "malformed"])
def test_evidence_error_defers_and_keeps_claim_on_restart(
    worker, factory, handoff, guard, tmp_path, damage
):
    req = handoff(worker)
    other_worker = independent(worker, 3)
    other = handoff(other_worker)
    e = factory(worker, other_worker)
    with sqlite3.connect(tmp_path / "execution.sqlite") as db:
        if damage == "missing":
            db.execute(
                "DELETE FROM opportunity_admissions WHERE execution_id=?", (req.execution_id,)
            )
        else:
            db.execute(
                "UPDATE opportunity_admissions SET payload='{' WHERE execution_id=?",
                (req.execution_id,),
            )
    deferred = e.execute(req)
    assert deferred.status == "AWAITING_RECONCILIATION"
    assert_unjournaled(e, req.execution_id)
    e.close()
    restarted = factory(worker, other_worker)
    assert restarted.result(req.execution_id) == deferred
    outcomes = {r.execution_id: r for r in restarted.recover()}
    assert (
        outcomes[req.execution_id] == deferred
        and outcomes[other.execution_id].status == "COMPLETED"
    )
    assert guard.pending() == (req.execution_id,)
    assert_unjournaled(restarted, req.execution_id)


def test_only_migrated_legacy_claim_can_be_fenced(worker, factory, tmp_path):
    from arb_multidex.opportunity.guard import LegacyAdmission

    path = tmp_path / "legacy.sqlite"
    with sqlite3.connect(path) as db:
        db.execute("""CREATE TABLE opportunity_requests (
            execution_id TEXT PRIMARY KEY, relationship_id TEXT NOT NULL, network TEXT NOT NULL,
            pending INTEGER NOT NULL, buy_request_id TEXT NOT NULL, sell_request_id TEXT NOT NULL,
            UNIQUE(relationship_id, buy_request_id, sell_request_id))""")
        db.execute(
            "INSERT INTO opportunity_requests VALUES (?,?,?,1,'b','s')",
            ("legacy-run", worker.relationship_id, worker.dex.network),
        )
    guard = ExecutionGuard(path)
    try:
        assert isinstance(guard.read_pending("legacy-run"), LegacyAdmission)
        e = factory(worker, name=path.name, guard=guard)
        (result,) = e.recover()
        assert result.status == "REJECTED" and result.attempts == ()
        assert guard.pending() == () and guard.read_pending("legacy-run") is None
        e.close()
        assert factory(worker, name=path.name, guard=guard).result("legacy-run") == result
    finally:
        guard.close()


def test_resolved_between_enumeration_and_read_is_skipped(worker, factory, handoff, guard):
    req = handoff(worker)
    assert guard.resolve(req.execution_id)
    e = factory(worker)
    with (
        patch.object(guard, "pending", return_value=(req.execution_id,)),
        patch.object(guard, "resolve") as resolve,
    ):
        assert e.recover() == ()
        resolve.assert_not_called()
    assert_unjournaled(e, req.execution_id)
    assert e.submit(req).status == "REJECTED"
    assert_unjournaled(e, req.execution_id)


@pytest.mark.parametrize("reverse", [False, True])
@pytest.mark.parametrize("conflict", ["units", "scale"])
def test_conflicting_shared_inventory_rolls_back(worker, factory, tmp_path, reverse, conflict):
    other = independent(worker, 2)
    if conflict == "units":
        other = replace(other, opening=(*other.opening[:4], other.opening[4] + 1))
    else:
        other = replace(other, kucoin=replace(other.kucoin, settlement_scale=5))
    ordered = (other, worker) if reverse else (worker, other)
    with pytest.raises(ValueError, match="conflicting opening"):
        factory(*ordered)
    with sqlite3.connect(tmp_path / "execution.sqlite") as db:
        assert db.execute("SELECT count(*) FROM sim_balances").fetchone()[0] == 0


@pytest.mark.parametrize("reverse", [False, True])
def test_shared_inventory_seeded_once_and_reopened_in_either_order(
    worker, factory, handoff, reverse
):
    other = independent(worker, 2)
    ordered = (other, worker) if reverse else (worker, other)
    e = factory(*ordered)
    assert balances(e, worker)[3:] == tuple(Balance(n, 0) for n in worker.opening[3:])
    req = handoff(worker)
    completed = e.execute(req)
    filled = balances(e, worker)
    assert filled[3].recorded == worker.opening[3] - VOLUME
    e.close()
    restarted = factory(*reversed(ordered))
    assert balances(restarted, worker) == filled
    assert balances(restarted, other)[3:] == filled[3:]
    assert restarted.execute(req) == completed


def test_unknown_relationship_preserves_claim_and_isolates_workers(worker, factory, handoff, guard):
    req = handoff(independent(worker, 9))
    e = factory(worker)
    result = e.execute(req)
    assert (result.status, result.reason, result.attempts) == (
        "REJECTED",
        "unknown relationship",
        (),
    )
    assert_unjournaled(e, req.execution_id)
    assert e.execute(handoff(worker)).status == "COMPLETED"
    assert guard.pending() == (req.execution_id,)


@pytest.mark.parametrize("rate_lifetime", [100, 6000])
def test_recovered_admission_rechecks_current_quote_and_rate_deadlines(
    worker, factory, handoff, guard, rate_lifetime
):
    req = handoff(worker, rate_lifetime=rate_lifetime)
    assert req.expires_ms == SAMPLE_MS + min(rate_lifetime, 5000)
    clock = MockClock(req.expires_ms)
    e = factory(worker, clock=clock)
    (result,) = e.recover()
    assert result.status == "REJECTED" and "validity window" in result.reason
    assert result.attempts == () and guard.pending() == ()
    assert balances(e, worker) == tuple(Balance(n, 0) for n in worker.opening)


def test_recovered_admission_rechecks_available_balance(worker, factory, handoff, guard):
    req = handoff(worker)
    e = factory(replace(worker, opening=(*worker.opening[:2], GAS - 1, *worker.opening[3:])))
    (result,) = e.recover()
    assert result.execution_id == req.execution_id and result.status == "REJECTED"
    assert "insufficient" in result.reason and result.attempts == () and guard.pending() == ()


def test_replay_preserves_runtime_types_after_resolution(worker, factory, handoff):
    e = factory(worker)
    req = handoff(worker)
    assert e.execute(req).status == "COMPLETED"
    for changed in (
        replace(req, direction=req.direction.value),
        replace(req, buy=replace(req.buy, costs=list(req.buy.costs))),
    ):
        with pytest.raises(ValueError, match="different terms"):
            e.execute(changed)
    assert len(e.result(req.execution_id).attempts) == 2


def test_first_submission_requires_shared_guard(worker, tmp_path):
    with pytest.raises(ValueError, match="shared S2 admission guard"):
        SimulationEngine(tmp_path / "s3.db", [worker], guard=None)
