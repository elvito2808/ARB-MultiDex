"""Offline acceptance tests with the real S2 types and owner simulation settings."""

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
    ExecutionRequest,
    Leg,
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
def factory(tmp_path):
    engines = []

    def build(*workers, name="execution.sqlite", **options):
        engine = SimulationEngine(
            tmp_path / name, workers, clock=options.pop("clock", MockClock()), **options
        )
        engines.append(engine)
        return engine

    yield build
    for engine in engines:
        engine.close()


def handoff(worker, direction=BUY_DEX, identifier="run", profit=MIN):
    dex_buy = direction == BUY_DEX
    dex_final = GROSS + GAS_VALUE if dex_buy else GROSS - GAS_VALUE
    spot_final = dex_final + profit if dex_buy else dex_final - profit
    clock = MockClock()
    base = Amount(VOLUME, 8)
    dex = Leg(
        "SIMULATION",
        worker.dex,
        identifier + "-dex",
        "BUY" if dex_buy else "SELL",
        base,
        Amount(dex_final, 6),
        "mock-1",
        clock(),
        clock() + 5000,
    )
    spot = Leg(
        "SIMULATION",
        worker.kucoin,
        identifier + "-spot",
        "SELL" if dex_buy else "BUY",
        base,
        Amount(spot_final, 6),
        "mock-1",
        clock(),
        clock() + 5000,
    )
    request = ExecutionRequest(
        identifier,
        worker.relationship_id,
        "m1",
        "fees",
        direction,
        dex if dex_buy else spot,
        spot if dex_buy else dex,
        Amount(profit, 6),
        worker.minimum_profit,
        clock(),
        clock() + 5000,
    )
    producer = QuoteProducer(
        MockSaucerSwap(clock),
        QuoteConfig(worker.relationship_id, "m1", "fees", VOLUME, 5000),
        GasPolicy("mock/rate", mock_gas),
    )
    quote = producer.quote(producer.request(dex.request_id, dex.side))
    quote.update(
        sourceId=worker.dex.source_id,
        settlementUnits=str(dex_final),
        grossSettlementUnits=str(GROSS),
    )
    return request, quote


def balances(engine, worker):
    return tuple(engine.balance(account, asset) for account, asset, _ in worker.assets)


@pytest.mark.parametrize("direction", [BUY_DEX, SELL_DEX])
def test_owner_balance_changes_exact_profit_boundary(worker, factory, direction):
    e = factory(worker)
    initial = (1000 * 10**8, 500 * 10**6, 10 * 10**8, 1000 * 10**8, 500 * 10**6)
    assert balances(e, worker) == tuple(Balance(n, 0) for n in initial)
    req, quote = handoff(worker, direction)
    side = "buy" if direction == BUY_DEX else "sell"
    dex_leg = getattr(req, side)
    checksum_pool = "0xdB34c1Ef944883f0e5A2fC18B6C1978B088bD31d"
    req = replace(
        req, **{side: replace(dex_leg, market=replace(dex_leg.market, pool=checksum_pool))}
    )
    with (
        patch("socket.socket", side_effect=AssertionError("network access")),
        patch("subprocess.Popen", side_effect=AssertionError("external process")),
    ):
        result = e.execute(req, dex_quote=quote)
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
def test_one_atom_short_rejects_entire_reservation(worker, factory, direction, index, required):
    opening = list(worker.opening)
    opening[index] = required - 1
    worker = replace(worker, opening=tuple(opening))
    e = factory(worker)
    req, quote = handoff(worker, direction)
    result = e.execute(req, dex_quote=quote)
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
def test_invalid_handoffs_fail_before_reservation(worker, factory, case):
    e = factory(worker)
    req, quote = handoff(worker, profit=MIN - 1 if case == "profit" else MIN)
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
        quote = None
    elif case == "wrong_quote":
        quote["requestId"] = "different"
    elif case == "wrong_rate":
        quote["includedCosts"][1]["rateDenominator"] = "1"
    elif case == "missing_cost":
        quote["includedCosts"] = []
    result = e.execute(req, dex_quote=quote)
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
def test_retry_limits_and_confirmed_inventory(worker, factory, dex, spot, terminal, history):
    e = factory(worker)
    req, quote = handoff(worker)
    result = e.execute(req, dex_quote=quote, plan=FailurePlan(dex, spot))
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
def test_reopen_at_each_durable_boundary(worker, factory, cut):
    req, quote = handoff(worker)
    e = factory(worker)
    e.submit(req, dex_quote=quote, plan=FailurePlan(("FAILED",), ("FAILED",)))
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
def test_unknown_holds_resources_until_exact_receipt(worker, factory, venue):
    req, quote = handoff(worker)
    e = factory(worker)
    plan = FailurePlan(dex=("UNKNOWN",)) if venue == "DEX" else FailurePlan(kucoin=("UNKNOWN",))
    result = e.execute(req, dex_quote=quote, plan=plan)
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
def test_partial_fill_retries_only_remaining_inventory(worker, factory, last):
    e = factory(worker)
    req, quote = handoff(worker, profit=MIN + 2)
    result = e.execute(req, dex_quote=quote, plan=FailurePlan(kucoin=("PARTIAL", "FAILED", last)))
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


def test_three_workers_pause_isolation_and_manual_restart(worker, factory):
    workers = (worker, independent(worker, 2), independent(worker, 3))
    e = factory(*workers)

    def trade(w, n, incomplete=True):
        req, quote = handoff(w, identifier=f"{w.relationship_id}-{n}")
        return e.execute(req, dex_quote=quote, plan=FailurePlan(kucoin=FAIL if incomplete else ()))

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


def test_graceful_stop_drains_recorded_execution(worker, factory):
    e = factory(worker)
    req, quote = handoff(worker)
    e.submit(req, dex_quote=quote)
    assert e.advance(req.execution_id).attempts[-1].status == "SUBMITTED"
    assert e.stop_worker(worker.relationship_id)[0].status == "COMPLETED"
    req, quote = handoff(worker, identifier="later")
    assert e.execute(req, dex_quote=quote).reason == "STOPPED"
    assert all(b.reserved == 0 for b in balances(e, worker))


def test_concurrent_reservations_and_duplicate_drivers(worker, factory):
    # Two independent engines and connections compete for one shared spot balance.
    worker = replace(worker, opening=(*worker.opening[:3], VOLUME, worker.opening[4]))
    workers = (worker, independent(worker, 2))
    engines = [factory(*workers) for _ in workers]
    calls = [handoff(w, identifier=w.relationship_id) for w in workers]
    start = Barrier(2)

    def submit(i):
        req, quote = calls[i]
        start.wait()
        return engines[i].submit(req, dex_quote=quote)

    with ThreadPoolExecutor(2) as pool:
        results = list(pool.map(submit, range(2)))
        assert sorted(r.status for r in results) == ["DEX", "REJECTED"]
        winner = next(i for i, r in enumerate(results) if r.status == "DEX")
        assert balances(engines[0], workers[winner])[3] == Balance(VOLUME, VOLUME)
        assert all(b.reserved == 0 for b in balances(engines[1], workers[1 - winner])[:3])
        req, quote = calls[winner]
        finals = list(pool.map(lambda e: e.execute(req, dex_quote=quote), engines))
    assert finals[0] == finals[1] and len(finals[0].attempts) == 2
    assert balances(engines[0], workers[winner])[3] == Balance(0, 0)


def test_unresolved_worker_allows_independent_but_blocks_same_network(worker, factory):
    same = replace(worker, relationship_id="same")
    other = independent(worker, 2)
    e = factory(worker, same, other)
    for w, expected in [
        (worker, "AWAITING_RECONCILIATION"),
        (same, "REJECTED"),
        (other, "COMPLETED"),
    ]:
        req, quote = handoff(w, identifier=w.relationship_id)
        result = e.execute(
            req,
            dex_quote=quote,
            plan=FailurePlan(dex=("UNKNOWN",)) if w == worker else FailurePlan(),
        )
        assert result.status == expected


@pytest.mark.parametrize("direction", [BUY_DEX, SELL_DEX])
def test_real_s1_s2_s3_handoff(worker, factory, tmp_path, direction):
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
    guard = ExecutionGuard(tmp_path / "execution.sqlite")
    try:
        detector = OpportunityDetector(guard, lambda _: nullcontext(ctx))
        detector.register(rel)
        req = detector.evaluate(worker.relationship_id, calls).execution_request
        assert req is not None and guard.pending() == (req.execution_id,)
        e = factory(worker, guard=guard, clock=clock)
        dex_quote = calls[direction, "BUY" if direction == BUY_DEX else "SELL"].response
        assert e.execute(req, dex_quote=dex_quote).status == "COMPLETED"
        assert guard.pending() == ()
        assert detector.evaluate(worker.relationship_id, calls).execution_request is None
    finally:
        guard.close()


def test_recover_guard_claim_before_and_after_execution(worker, factory, tmp_path):
    guard = ExecutionGuard(tmp_path / "execution.sqlite")
    try:
        req, quote = handoff(worker)
        assert guard.admit(req, worker.dex.network)
        e = factory(worker, guard=guard)
        # Crash between S2 admission and S3 receipt of the request.
        (lost,) = e.recover()
        assert lost.execution_id == req.execution_id and lost.status == "REJECTED"
        assert guard.pending() == ()
        assert e.result(req.execution_id) == e.execute(req, dex_quote=quote) == lost
        assert e.recover() == ()
        req, quote = handoff(worker, identifier="after")
        assert guard.admit(req, worker.dex.network)
        with (
            patch.object(guard, "resolve", side_effect=OSError("simulated crash")),
            pytest.raises(OSError, match="simulated crash"),
        ):
            e.execute(req, dex_quote=quote)
        e.close()
        assert guard.pending() == (req.execution_id,)
        restarted = factory(worker, guard=guard)
        (result,) = restarted.recover()
        assert result.status == "COMPLETED" and len(result.attempts) == 2
        assert balances(restarted, worker)[2] == Balance(worker.opening[2] - GAS, 0)
        assert guard.pending() == ()
    finally:
        guard.close()


def test_expiry_after_submission_verifies_dex_without_stale_kucoin_order(worker, factory):
    clock = MockClock()
    e = factory(worker, clock=clock)
    req, quote = handoff(worker)
    e.submit(req, dex_quote=quote)
    assert e.advance(req.execution_id).attempts[-1].status == "SUBMITTED"
    clock.now_ms = req.expires_ms
    result = e.recover()[0]
    assert result.status == "INCOMPLETE_ARBITRAGE"
    assert [(a.leg, a.status) for a in result.attempts] == [("DEX", "SUCCESS")]
    assert balances(e, worker)[2] == Balance(worker.opening[2] - GAS, 0)
    assert all(b.reserved == 0 for b in balances(e, worker))


def test_settlement_error_rolls_back_attempt_balances_and_reservations(worker, factory):
    e = factory(worker)
    req, quote = handoff(worker)
    e.submit(req, dex_quote=quote)
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


def test_three_simultaneous_relationships_with_exact_large_inventory(worker, factory):
    n = 10**70 + 7
    worker = replace(worker, opening=(n,) * 5)
    workers = [worker, independent(worker, 2), independent(worker, 3)]
    engines = [factory(*workers) for _ in workers]
    calls = [handoff(w, identifier=w.relationship_id) for w in workers]
    start = Barrier(3)

    def submit(i):
        start.wait()
        req, quote = calls[i]
        return engines[i].submit(req, dex_quote=quote)

    with ThreadPoolExecutor(3) as pool:
        assert [r.status for r in pool.map(submit, range(3))] == ["DEX"] * 3
        assert balances(engines[0], worker)[3] == Balance(n, 3 * VOLUME)
        finals = list(pool.map(lambda i: engines[i].execute(calls[i][0]), range(3)))
    assert [r.status for r in finals] == ["COMPLETED"] * 3
    assert balances(engines[0], worker)[3] == Balance(n - 3 * VOLUME, 0)
