"""Offline test fixtures. All markets, mappings, volumes, fees and limits are synthetic."""

import json
import re
import sqlite3
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing, contextmanager, nullcontext
from copy import deepcopy
from dataclasses import FrozenInstanceError, replace
from pathlib import Path
from threading import Event

import pytest

from arb_multidex.opportunity import (
    Amount,
    Binding,
    Call,
    Cost,
    DecisionContext,
    Direction,
    ExecutionGuard,
    Market,
    OpportunityDetector,
    Rate,
    Relationship,
    SourceState,
    Terms,
)

FORWARD, REVERSE = Direction.BUY_KUCOIN, Direction.BUY_DEX
ROOT = Path(__file__).resolve().parents[3]
# Use the actual accepted contract vectors, including its rounded and embedded costs.
VECTORS = json.loads(
    re.search(r"```json\n(.*?)\n```", (ROOT / "docs/design/quote-contract.md").read_text(), re.DOTALL)[1]
)
T0 = 1795392000000  # 2026-11-23T00:00:00.000Z


def fixtures():
    spot = Binding(
        Market("sim-a", "spot", "sim-a/asset-x", "sim-a/asset-y", 2, 2, symbol="X-Y"),
        5000,
        (),
        (Cost("base-fee", "EMBEDDED"),),
    )
    dex = Binding(
        Market(
            "sim-b", "dex", "sim-b/asset-x", "sim-b/asset-y", 2, 2, network="sim-net", pool="sim-pool"
        ),
        5000,
        (Cost("gas", "SEPARATE", "sim-b/gas", 0, "test-oracle"),),
        (),
    )
    rel = Relationship(
        "sim-ab",
        "m1",
        "sim-fees-1",
        dex,
        spot,
        (dex.market.base_id, spot.market.base_id),
        (dex.market.settlement_id, spot.market.settlement_id),
        Terms(Amount(200, 2), Amount(300, 2)),
        Terms(Amount(200, 2), Amount(241, 2)),
        2000,
    )
    source = SourceState("e1", True, T0)
    rate = Rate("test-oracle", "sim-b/gas", 0, dex.market.settlement_id, 2, 1, 2, T0, T0 + 4500)
    context = DecisionContext(T0 + 2000, {"sim-a": source, "sim-b": source}, {"sim-rate-1": rate})
    slots = [(d, side) for d in Direction for side in ("BUY", "SELL")]
    return rel, context, {slot: Call(**deepcopy(vec)) for slot, vec in zip(slots, VECTORS)}


class DetectorTest(unittest.TestCase):
    def setUp(self):
        self.rel, self.ctx, self.calls = fixtures()
        self.guard = ExecutionGuard(":memory:")
        self.addCleanup(self.guard.close)

    def detector(self, rel=None, guard=None):
        engine = OpportunityDetector(guard or self.guard, lambda _: nullcontext(self.ctx))
        engine.register(rel or self.rel)
        return engine

    def run_case(self, rel=None):
        return self.detector(rel).evaluate(self.rel.relationship_id, self.calls)

    def change_echo(self, direction, slot_side, **kwargs):
        call = self.calls[direction, slot_side]
        call.request.update(kwargs)
        call.response.update(kwargs)

    def test_contract_vectors_and_s3_payload(self):
        result = self.run_case()
        f, r = result.decisions
        self.assertEqual([f.net_profit, r.net_profit], [Amount(325, 2), Amount(242, 2)])
        self.assertTrue(f.qualifies and r.qualifies)
        req = result.execution_request
        self.assertEqual(req.direction, FORWARD)
        self.assertEqual((req.buy.request_id, req.sell.request_id), ("q1", "q2"))
        self.assertEqual((req.buy.settlement, req.sell.settlement), (Amount(10125, 2), Amount(10450, 2)))
        self.assertEqual((req.buy.market.symbol, req.sell.market.pool), ("X-Y", "sim-pool"))
        self.assertEqual(
            (req.mode, req.status, req.evaluated_ms, req.expires_ms),
            ("SIMULATION", "ACCEPTED", T0 + 2000, T0 + 5000),
        )
        self.assertEqual(self.guard.pending(), (req.execution_id,))

    def test_inclusive_threshold_equality_and_one_atom(self):
        del self.calls[FORWARD, "BUY"]
        for threshold, accepted in ((242, True), (243, False), (241, True)):
            with self.subTest(threshold=threshold):
                terms = replace(self.rel.buy_dex, threshold=Amount(threshold, 2))
                # Each threshold case is an independent evaluation; do not reuse a
                # spent snapshot claim from the previous subtest.
                self.guard = ExecutionGuard(":memory:")
                self.addCleanup(self.guard.close)
                result = self.run_case(replace(self.rel, buy_dex=terms))
                self.assertEqual(result.decisions[1].qualifies, accepted)
                self.assertEqual(result.execution_request is not None, accepted)
                if accepted:
                    self.assertEqual(result.execution_request.expires_ms, T0 + 4500)

    def test_loss_and_zero_spread(self):
        del self.calls[REVERSE, "SELL"]
        for final, profit in (("1", -10124), ("10125", 0)):
            with self.subTest(final=final):
                self.calls[FORWARD, "SELL"].response.update(
                    settlementUnits=final, grossSettlementUnits=final
                )
                result = self.run_case()
                self.assertEqual(result.decisions[0].net_profit.units, profit)
                self.assertIsNone(result.execution_request)

    def test_mixed_scales_and_78_digit_amounts(self):
        big = 10**75 + 123
        spot = replace(
            self.rel.kucoin, market=replace(self.rel.kucoin.market, base_scale=3, settlement_scale=0)
        )
        terms = Terms(Amount(2, 0), Amount(9, 3))
        rel = replace(self.rel, kucoin=spot, buy_kucoin=terms)
        self.change_echo(FORWARD, "BUY", baseUnits="2000", baseScale=3, settlementScale=0)
        self.calls[FORWARD, "BUY"].response.update(
            grossSettlementUnits=str(big), settlementUnits=str(big)
        )
        self.calls[FORWARD, "SELL"].response.update(
            grossSettlementUnits=str(100 * big + 1), settlementUnits=str(100 * big + 1)
        )
        result = self.run_case(rel)
        self.assertEqual(result.execution_request.net_profit, Amount(10, 3))
        self.assertEqual(result.execution_request.buy.base, Amount(2000, 3))

    def test_request_builder_fresh_ids_and_direction_specific_volume(self):
        rel = replace(self.rel, buy_dex=Terms(Amount(3, 0), Amount(2, 1)))
        first = rel.request(REVERSE, "SELL")
        self.assertEqual(first["baseUnits"], "300")
        self.assertNotEqual(first["requestId"], rel.request(REVERSE, "SELL")["requestId"])
        expected = dict(VECTORS[0]["request"])
        generated = rel.request(FORWARD, "BUY")
        self.assertEqual(generated, expected | {"requestId": generated["requestId"]})
        with self.assertRaises(ValueError):
            replace(rel, buy_dex=Terms(Amount(1, 3), Amount(2, 1)))

    def test_every_echo_field_and_extra_fields(self):
        call = self.calls[FORWARD, "SELL"]
        for key in call.request.keys() | {"extra"}:
            with self.subTest(key=key):
                self.calls[FORWARD, "SELL"] = replace(call, response=call.response | {key: "other"})
                decision = self.run_case().decisions[0]
                self.assertEqual(
                    (decision.sell_request_id, decision.sell_error), ("q2", "INVALID_QUOTE")
                )

    def test_malformed_messages_and_exact_wire_types(self):
        call = self.calls[FORWARD, "BUY"]
        raw = json.dumps(call.response)
        cases = [
            b"\xff",
            "[1]",
            raw + "{}",
            raw + " " * 16384,
            raw.replace('"status":', '"status":"OK","status":'),
            raw.replace('"baseScale": 2', '"baseScale": 2e0'),
        ]
        for field, value in (
            ("settlementUnits", "0"),
            ("baseScale", True),
            ("baseScale", 2.0),
            ("settlementUnits", "01"),
            ("grossSettlementUnits", "１"),
            ("grossSettlementUnits", "1" * 79),
            ("connectionEpoch", []),
            ("observedAt", "2026-11-23T00:00:00Z"),
            ("observedAt", "2026-02-30T00:00:00.000Z"),
            ("observedAt", "2026-11-23T00:00:02.001Z"),
        ):
            cases.append(call.response | {field: value})
        for bad in cases:
            with self.subTest(bad=str(bad)[:40]):
                self.calls[FORWARD, "BUY"] = replace(call, response=bad)
                self.assertEqual(self.run_case().decisions[0].buy_error, "INVALID_QUOTE")
        self.calls[FORWARD, "BUY"] = Call(json.dumps(call.request), raw.encode())
        self.assertTrue(self.run_case().decisions[0].qualifies)

    def test_live_rejected_before_pair_mapping(self):
        self.change_echo(FORWARD, "BUY", mode="LIVE")
        result = self.run_case(replace(self.rel, equivalent_bases=None))
        self.assertEqual(result.decisions[0].buy_error, "INVALID_QUOTE")
        self.assertEqual(result.decisions[1].code, "ASSET_MISMATCH")

    def test_independent_errors_and_direction_attribution(self):
        buy, sell = (self.calls[FORWARD, side] for side in ("BUY", "SELL"))

        def error(call, code):
            return replace(
                call,
                response={
                    "status": "ERROR",
                    "code": code,
                    "requestId": call.request["requestId"],
                    "sourceId": call.request["sourceId"],
                },
            )

        self.calls[FORWARD, "BUY"] = error(buy, "STALE_QUOTE")
        self.calls[FORWARD, "SELL"] = error(sell, "SOURCE_UNAVAILABLE")
        result = self.run_case()
        self.assertEqual(result.execution_request.direction, REVERSE)
        f = result.decisions[0]
        self.assertEqual(f.primary_request_id, "q2")
        self.assertEqual(
            (f.code, f.buy_error, f.sell_error),
            ("SOURCE_UNAVAILABLE", "STALE_QUOTE", "SOURCE_UNAVAILABLE"),
        )
        for bad in (
            error(buy, "STALE_QUOTE").response,
            error(sell, "UNKNOWN").response,
            error(sell, "INVALID_QUOTE").response | {"extra": 0},
        ):
            self.calls[FORWARD, "SELL"] = replace(sell, response=bad)
            self.assertEqual(self.run_case().decisions[0].sell_error, "INVALID_QUOTE")

    def test_mapping_and_volume_gates(self):
        for rel in (
            replace(self.rel, equivalent_bases=None),
            replace(self.rel, one_to_one_settlements=None),
            replace(self.rel, equivalent_bases=("wrong", "wrong")),
        ):
            self.assertEqual(self.run_case(rel).decisions[0].code, "ASSET_MISMATCH")
        self.calls[FORWARD, "SELL"] = Call(**deepcopy(VECTORS[4]))
        self.assertEqual(self.run_case().decisions[0].code, "INCOMPARABLE_QUOTES")
        self.change_echo(FORWARD, "BUY", baseUnits="201")
        self.assertEqual(self.run_case().decisions[0].code, "INCOMPARABLE_QUOTES")

    def test_active_pair_binding_and_source_identity(self):
        for field in (
            "relationshipId",
            "mappingRevision",
            "feePolicyId",
            "sourceId",
            "side",
            "baseAssetId",
            "settlementAssetId",
            "baseScale",
            "settlementScale",
        ):
            with self.subTest(field=field):
                _, _, self.calls = fixtures()
                value = "SELL" if field == "side" else 3 if field.endswith("Scale") else "other"
                self.change_echo(FORWARD, "BUY", **{field: value})
                f = self.run_case().decisions[0]
                if field in ("baseAssetId", "settlementAssetId", "baseScale", "settlementScale"):
                    self.assertEqual(f.buy_error, "INVALID_QUOTE")
                else:
                    self.assertIsNotNone(f.code)
                    self.assertFalse(f.qualifies)

    def test_age_and_skew_equality(self):
        self.ctx = replace(self.ctx, now_ms=T0 + 4999)
        self.assertTrue(self.run_case(replace(self.rel, max_skew_ms=1000)).decisions[0].qualifies)
        self.assertEqual(
            self.run_case(replace(self.rel, max_skew_ms=999)).decisions[0].code, "QUOTE_TIME_SKEW"
        )
        self.ctx = replace(self.ctx, now_ms=T0 + 5000)
        self.assertEqual(self.run_case().decisions[0].buy_error, "STALE_QUOTE")

    def test_epoch_disconnect_recovery_and_revalidation(self):
        engine = self.detector()
        req = engine.evaluate(self.rel.relationship_id, self.calls).execution_request
        self.guard.resolve(req.execution_id)
        for state, expected in (
            (SourceState(None, False, T0), "SOURCE_UNAVAILABLE"),
            (SourceState("e2", True, T0), "INVALID_QUOTE"),
            (SourceState("e1", False, T0), "SOURCE_UNAVAILABLE"),
            (SourceState("e1", True, T0 + 1001), "INVALID_QUOTE"),
        ):
            self.ctx.sources["sim-b"] = state
            result = engine.evaluate(self.rel.relationship_id, self.calls)
            self.assertEqual(result.decisions[0].sell_error, expected)
            self.assertEqual(result.decisions[1].buy_error, expected)
            self.assertIsNone(result.execution_request)
        self.ctx.sources["sim-b"] = SourceState("e2", True, T0 + 2000)
        self.change_echo(FORWARD, "SELL", requestId="recovered-sell")
        self.calls[FORWARD, "SELL"].response.update(
            connectionEpoch="e2", observedAt="2026-11-23T00:00:02.000Z"
        )
        self.assertIsNotNone(engine.evaluate(self.rel.relationship_id, self.calls).execution_request)

    def test_fee_coverage_rounding_and_treatment(self):
        call = self.calls[REVERSE, "BUY"]
        gas = call.response["includedCosts"][0]
        malformed = [
            {},
            [{}],
            [gas] * 33,
            [gas | {"extra": "1"}],
            [gas | {"treatment": "EMBEDDED"}],
            [gas | {"scale": False}],
            [gas | {"units": "-1"}],
        ]
        unavailable = [
            [],
            [gas, gas],
            [gas | {"costId": "foreign"}],
            [gas | {"settlementCostUnits": "2"}],
            [gas | {"rateRef": "unknown"}],
            [gas | {"rateDenominator": "0"}],
        ]
        for cases, error in ((malformed, "INVALID_QUOTE"), (unavailable, "FEE_UNAVAILABLE")):
            for bad in cases:
                with self.subTest(costs=bad):
                    self.calls[REVERSE, "BUY"] = replace(
                        call, response=call.response | {"includedCosts": bad}
                    )
                    self.assertEqual(self.run_case().decisions[1].buy_error, error)

    def test_rate_provenance_age_and_units(self):
        original = self.ctx.rates["sim-rate-1"]
        for change in (
            {"source": "other"},
            {"asset_id": "other"},
            {"settlement_id": "other"},
            {"scale": 2},
            {"settlement_scale": 3},
            {"numerator": 2},
            {"observed_ms": self.ctx.now_ms + 1},
            {"expires_ms": self.ctx.now_ms},
        ):
            self.ctx.rates["sim-rate-1"] = replace(original, **change)
            self.assertEqual(self.run_case().decisions[1].buy_error, "FEE_UNAVAILABLE")

    def test_separate_sell_in_settlement_units_and_known_zero(self):
        market = self.rel.dex.market
        fee = Cost("test", "SEPARATE", market.settlement_id, market.settlement_scale)
        rel = replace(self.rel, dex=replace(self.rel.dex, sell_costs=(fee,)))
        reply = self.calls[FORWARD, "SELL"].response
        for units in (0, 7):
            cost = {
                "costId": "test",
                "treatment": "SEPARATE",
                "settlementCostUnits": str(units),
                "units": str(units),
                "assetId": market.settlement_id,
                "scale": 2,
                "rateNumerator": "1",
                "rateDenominator": "1",
            }
            reply.update(includedCosts=[cost], settlementUnits=str(10450 - units))
            self.assertEqual(self.run_case(rel).decisions[0].net_profit, Amount(325 - units, 2))
        cost["rateNumerator"] = "2"
        self.assertEqual(self.run_case(rel).decisions[0].sell_error, "FEE_UNAVAILABLE")

    def test_request_error_precedence(self):
        reply = self.calls[REVERSE, "BUY"].response
        reply["settlementUnits"] = "10006"
        self.ctx.sources["sim-b"] = SourceState("e1", False, T0)
        self.assertEqual(self.run_case().decisions[1].buy_error, "INVALID_QUOTE")
        reply["includedCosts"] = []
        self.ctx = replace(self.ctx, now_ms=T0 + 6000)
        self.assertEqual(self.run_case().decisions[1].buy_error, "SOURCE_UNAVAILABLE")
        self.ctx.sources["sim-b"] = SourceState("e1", True, T0)
        self.assertEqual(self.run_case().decisions[1].buy_error, "STALE_QUOTE")
        self.ctx = replace(self.ctx, now_ms=None)
        self.assertEqual(self.run_case().decisions[1].buy_error, "SOURCE_UNAVAILABLE")

    def test_retry_new_ids_concurrency_and_restart(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory, "journal.db")
            guard = ExecutionGuard(path)
            try:
                engine = self.detector(guard=guard)
                with ThreadPoolExecutor(max_workers=8) as pool:
                    results = list(
                        pool.map(
                            lambda _: engine.evaluate(self.rel.relationship_id, self.calls), range(24)
                        )
                    )
                requests = [r.execution_request for r in results if r.execution_request]
                self.assertEqual(len(requests), 1)
            finally:
                guard.close()
            reopened = ExecutionGuard(path)
            try:
                engine = self.detector(guard=reopened)
                for d, side in self.calls:
                    self.change_echo(d, side, requestId=d.value + side)
                self.assertIsNone(
                    engine.evaluate(self.rel.relationship_id, self.calls).execution_request
                )
                self.assertEqual(reopened.pending(), (requests[0].execution_id,))
                self.assertFalse(reopened.resolve("other"))
                self.assertTrue(reopened.resolve(requests[0].execution_id))
                self.assertIsNone(
                    engine.evaluate(self.rel.relationship_id, self.calls).execution_request
                )
            finally:
                reopened.close()

    def test_three_workers_invalid_one_and_network_guard(self):
        engine = self.detector()

        def another(name, network):
            dex = replace(
                self.rel.dex, market=replace(self.rel.dex.market, source_id=name, network=network)
            )
            rel = replace(self.rel, relationship_id=name, dex=dex)
            engine.register(rel)
            _, _, calls = fixtures()
            for (d, side), call in calls.items():
                request = rel.request(d, side)
                calls[d, side] = Call(request, call.response | request)
            self.ctx.sources[name] = self.ctx.sources["sim-b"]
            return calls

        second = another("second", "net2")
        third = another("third", "net3")
        with self.assertRaises(ValueError):
            another("fourth", "net4")
        first = engine.evaluate(self.rel.relationship_id, self.calls).execution_request
        req2 = engine.evaluate("second", second).execution_request
        req3 = engine.evaluate("third", third).execution_request
        self.assertEqual(len({first.execution_id, req2.execution_id, req3.execution_id}), 3)
        self.assertIsNone(engine.evaluate("second", {}).execution_request)
        self.assertTrue(engine.evaluate("third", third).decisions[0].qualifies)
        # A separate worker sharing the same network cannot acquire its pending claim.
        engine = self.detector()
        shared = another("shared", "sim-net")
        self.assertIsNone(engine.evaluate("shared", shared).execution_request)
        self.guard.resolve(first.execution_id)
        self.assertIsNotNone(engine.evaluate("shared", shared).execution_request)

    def test_unavailable_or_slow_worker_does_not_delay_another(self):
        waiting, release = Event(), Event()

        @contextmanager
        def context(relationship):
            if relationship.relationship_id == "slow":
                waiting.set()
                release.wait(5)
                raise OSError("test clock unavailable")
            yield self.ctx

        engine = OpportunityDetector(self.guard, context)
        engine.register(self.rel)
        engine.register(replace(self.rel, relationship_id="slow"))
        slow_calls = deepcopy(self.calls)
        for call in slow_calls.values():
            call.request["relationshipId"] = call.response["relationshipId"] = "slow"
        with ThreadPoolExecutor(max_workers=2) as pool:
            slow = pool.submit(engine.evaluate, "slow", slow_calls)
            try:
                self.assertTrue(waiting.wait(2))
                ready = pool.submit(engine.evaluate, self.rel.relationship_id, self.calls)
                self.assertIsNotNone(ready.result(timeout=2).execution_request)
                self.assertFalse(slow.done())
            finally:
                release.set()
            result = slow.result(timeout=2)
        self.assertEqual([d.code for d in result.decisions], ["SOURCE_UNAVAILABLE"] * 2)

    def test_duplicate_call_ids_rejected_at_request_level(self):
        self.change_echo(FORWARD, "SELL", requestId="q1")
        f, r = self.run_case().decisions
        self.assertEqual((f.buy_error, f.sell_error), ("INVALID_QUOTE", "INVALID_QUOTE"))
        self.assertTrue(r.qualifies)

    def test_pair_gate_defends_against_hypothetical_mixed_modes(self):
        from arb_multidex.opportunity.detector import compare
        from arb_multidex.opportunity.validation import validate

        a, b = (validate(self.calls[FORWARD, side], self.rel, self.ctx) for side in ("BUY", "SELL"))
        hypothetical = replace(b, leg=replace(b.leg, mode="LIVE"))
        self.assertEqual(compare(self.rel, FORWARD, a, hypothetical).code, "INCOMPARABLE_QUOTES")

    def test_canonical_zero_scale_and_wire_size_limit(self):
        call = self.calls[FORWARD, "BUY"]
        self.change_echo(FORWARD, "BUY", baseScale=0, baseUnits="2")
        rel = replace(
            self.rel,
            kucoin=replace(self.rel.kucoin, market=replace(self.rel.kucoin.market, base_scale=0)),
        )
        raw = json.dumps(call.response)
        for scale in ("-0", "0.0", "0e0", "false", "39"):
            self.calls[FORWARD, "BUY"] = replace(
                call, response=raw.replace('"baseScale": 0', '"baseScale": ' + scale)
            )
            self.assertEqual(self.run_case(rel).decisions[0].buy_error, "INVALID_QUOTE")
        self.calls[FORWARD, "BUY"] = replace(call, response=raw.ljust(16384))
        self.assertTrue(self.run_case(rel).decisions[0].qualifies)
        self.calls[FORWARD, "BUY"] = replace(call, response=raw.ljust(16385))
        self.assertEqual(self.run_case(rel).decisions[0].buy_error, "INVALID_QUOTE")

    def test_competing_directions_and_tied_error_priority(self):
        # More decimal places in the threshold must not improve a direction's rank.
        rel = replace(self.rel, buy_dex=replace(self.rel.buy_dex, threshold=Amount(2410, 3)))
        self.calls[REVERSE, "SELL"].response.update(
            grossSettlementUnits="10328", settlementUnits="10328"
        )
        decision = self.run_case(rel)
        self.assertEqual(decision.execution_request.direction, FORWARD)  # Both earned 3.25.
        self.assertTrue(all(d.qualifies for d in decision.decisions))
        self.guard.resolve(decision.execution_request.execution_id)
        self.change_echo(REVERSE, "SELL", requestId="new-sell")
        self.calls[REVERSE, "SELL"].response.update(
            grossSettlementUnits="10329", settlementUnits="10329", observedAt="2026-11-23T00:00:00.001Z"
        )
        self.assertEqual(self.run_case(rel).execution_request.direction, REVERSE)
        for side in ("BUY", "SELL"):
            self.calls[FORWARD, side].response["mode"] = "LIVE"
        rejected = self.run_case(rel).decisions[0]
        self.assertEqual(rejected.primary_request_id, rejected.buy_request_id)


def gas_fixture(direction):
    """Both directions use synthetic native gas at 1/2 settlement atom per gas atom."""
    rel, ctx, calls = fixtures()
    dex_side = "BUY" if direction == REVERSE else "SELL"
    gas = deepcopy(calls[REVERSE, "BUY"].response["includedCosts"][0])
    rel = replace(rel, dex=replace(rel.dex, sell_costs=rel.dex.buy_costs))
    calls = {slot: call for slot, call in calls.items() if slot[0] == direction}
    dex = calls[direction, dex_side].response
    # SELL receives gross minus three atoms. BUY pays gross plus three atoms.
    if dex_side == "SELL":
        dex["grossSettlementUnits"] = str(int(dex["settlementUnits"]) + 3)
    dex["includedCosts"] = [gas]
    return rel, ctx, calls, dex


def evaluate(guard, rel, ctx, calls):
    detector = OpportunityDetector(guard, lambda _: nullcontext(ctx))
    detector.register(rel)
    return detector.evaluate(rel.relationship_id, calls).execution_request


@pytest.mark.parametrize("direction", [FORWARD, REVERSE])
def test_admission_rejects_valid_same_final_substitution_after_restart(direction, tmp_path):
    rel, ctx, calls, dex = gas_fixture(direction)
    path = tmp_path / "guard.db"
    with closing(ExecutionGuard(path)) as guard:
        original = evaluate(guard, rel, ctx, calls)
        assert original is not None

    # A different fully reconciling quote for the same IDs, epochs, observation and profit.
    dex["includedCosts"][0].update(units="7", settlementCostUnits="4")
    delta = -1 if dex["side"] == "BUY" else 1
    dex["grossSettlementUnits"] = str(int(dex["grossSettlementUnits"]) + delta)
    with closing(ExecutionGuard(":memory:")) as alternate:
        substitute = evaluate(alternate, rel, ctx, calls)
        assert alternate.verify_pending(substitute) == substitute
    key = dex["side"].lower()
    accepted_leg, changed_leg = getattr(original, key), getattr(substitute, key)
    assert (original.execution_id, original.net_profit, accepted_leg.settlement) == (
        substitute.execution_id,
        substitute.net_profit,
        changed_leg.settlement,
    )
    assert changed_leg.gross_settlement.units == accepted_leg.gross_settlement.units + delta
    assert (accepted_leg.costs[0].native, changed_leg.costs[0].native) == (
        Amount(5, 0),
        Amount(7, 0),
    )

    with closing(ExecutionGuard(path)) as guard:
        assert guard.verify_pending(substitute) is None
        assert evaluate(guard, rel, ctx, calls) is None
        recovered = guard.read_pending(original.execution_id)
        assert recovered == guard.verify_pending(original) == original
        assert recovered is not original
        cost = getattr(recovered, key).costs[0]
        assert (cost.asset_id, cost.native, cost.settlement_units) == ("sim-b/gas", Amount(5, 0), 3)
        assert (cost.numerator, cost.denominator, cost.rate_ref) == (1, 2, "sim-rate-1")
        assert cost.rate == ctx.rates["sim-rate-1"]
        assert accepted_leg.quote_expires_ms == accepted_leg.observed_ms + rel.dex.max_age_ms
        assert original.expires_ms == accepted_leg.expires_ms == cost.rate.expires_ms
        assert cost.rate.observed_ms <= original.evaluated_ms < cost.rate.expires_ms
        with pytest.raises(FrozenInstanceError):
            cost.rate.source = "changed"
        assert guard.resolve(original.execution_id)
        assert guard.verify_pending(original) is None
        assert guard.read_pending(original.execution_id) is None
        assert evaluate(guard, rel, ctx, calls) is None


@pytest.mark.parametrize("direction", [FORWARD, REVERSE])
def test_verification_binds_each_field_and_the_trusted_rate(direction):
    rel, ctx, calls, dex = gas_fixture(direction)
    with closing(ExecutionGuard(":memory:")) as guard:
        request = evaluate(guard, rel, ctx, calls)
        key = dex["side"].lower()
        leg = getattr(request, key)
        gas = leg.costs[0]
        # An alternate trusted context authorizes the same conversion at a different observation.
        revised = replace(gas.rate, observed_ms=gas.rate.observed_ms + 1)
        ctx.rates[gas.rate_ref] = revised
        with closing(ExecutionGuard(":memory:")) as alternate:
            different_proof = evaluate(alternate, rel, ctx, calls)
        assert getattr(different_proof, key).costs[0].rate == revised
        assert guard.verify_pending(different_proof) is None

        changes = [
            replace(leg, costs=(replace(gas, **fields),))
            for fields in (
                {"rate_ref": "other"},
                {"numerator": 2},
                {"denominator": 3},
                {"asset_id": "other"},
                {"native": Amount(5, 1)},
                {"cost_id": "other"},
                {"settlement_units": 4},
                {"rate": None},
                {"rate": replace(gas.rate, source="other")},
                {"rate": replace(gas.rate, expires_ms=gas.rate.expires_ms + 1)},
                {"rate": replace(gas.rate, settlement_id="other")},
                {"rate": replace(gas.rate, scale=False)},
            )
        ]
        changes.extend(
            replace(leg, **fields)
            for fields in (
                {"epoch": "other"},
                {"market": replace(leg.market, source_id="other")},
                {"request_id": "other"},
                {"observed_ms": leg.observed_ms + 1},
                {"expires_ms": leg.expires_ms + 1},
                {"quote_expires_ms": leg.quote_expires_ms + 1},
                {"settlement": Amount(1, 2)},
                {"gross_settlement": Amount(2, 2)},
                {"costs": ()},
            )
        )
        for changed in changes:
            assert guard.verify_pending(replace(request, **{key: changed})) is None
        for fields in (
            {"execution_id": "unknown"},
            {"relationship_id": "other"},
            {"mapping_revision": "other"},
            {"fee_policy_id": "other"},
            {"direction": REVERSE if direction == FORWARD else FORWARD},
            {"buy": request.sell, "sell": request.buy},
            {"status": "PENDING"},
            {"mode": "LIVE"},
            {"net_profit": Amount(0, 0)},
            {"threshold": Amount(0, 0)},
            {"evaluated_ms": request.evaluated_ms - 1},
            {"expires_ms": request.expires_ms + 1},
        ):
            assert guard.verify_pending(replace(request, **fields)) is None
        assert guard.verify_pending(request) == request


def test_legacy_pending_claim_fails_closed_and_remains_spent(tmp_path):
    path = tmp_path / "legacy.db"
    rel, ctx, calls, _ = gas_fixture(REVERSE)
    with closing(ExecutionGuard(":memory:")) as shadow:
        request = evaluate(shadow, rel, ctx, calls)
    with closing(sqlite3.connect(path)) as db, db:
        db.execute("""CREATE TABLE opportunity_requests (
            execution_id TEXT PRIMARY KEY, relationship_id TEXT NOT NULL,
            network TEXT NOT NULL, pending INTEGER NOT NULL,
            buy_request_id TEXT NOT NULL, sell_request_id TEXT NOT NULL,
            UNIQUE(relationship_id, buy_request_id, sell_request_id))""")
        db.execute(
            "INSERT INTO opportunity_requests VALUES (?, ?, ?, 1, ?, ?)",
            (
                request.execution_id,
                rel.relationship_id,
                rel.dex.market.network,
                request.buy.request_id,
                request.sell.request_id,
            ),
        )
    with closing(ExecutionGuard(path)) as guard:
        assert guard.pending() == (request.execution_id,)
        assert guard.read_pending(request.execution_id) is None
        assert guard.verify_pending(request) is None
        assert evaluate(guard, rel, ctx, calls) is None
        assert guard.resolve(request.execution_id)
        assert evaluate(guard, rel, ctx, calls) is None


def test_evidence_write_failure_rolls_back_claim_and_allows_retry(tmp_path):
    path = tmp_path / "atomic.db"
    rel, ctx, calls, _ = gas_fixture(REVERSE)
    with closing(ExecutionGuard(path)) as guard, closing(sqlite3.connect(path)) as db:
        db.execute("""CREATE TRIGGER fail_write BEFORE INSERT ON opportunity_admissions
                      BEGIN SELECT RAISE(ABORT, 'fixture failure'); END""")
        with pytest.raises(sqlite3.IntegrityError, match="fixture failure"):
            evaluate(guard, rel, ctx, calls)
        assert guard.pending() == ()
        db.execute("DROP TRIGGER fail_write")
        request = evaluate(guard, rel, ctx, calls)
        assert request is not None and guard.verify_pending(request) == request


def test_concurrent_admissions_keep_only_winning_evidence(tmp_path):
    path = tmp_path / "contended.db"
    rel, ctx, calls, _ = gas_fixture(REVERSE)
    alternative = deepcopy(calls)
    dex = alternative[REVERSE, "BUY"].response
    dex["includedCosts"][0].update(units="7", settlementCostUnits="4")
    dex["grossSettlementUnits"] = "9999"
    with closing(ExecutionGuard(path)) as first, closing(ExecutionGuard(path)) as second:
        with ThreadPoolExecutor(max_workers=2) as pool:
            attempts = [
                pool.submit(evaluate, guard, rel, ctx, inputs)
                for guard, inputs in ((first, calls), (second, alternative))
            ]
        winners = [r for future in attempts if (r := future.result()) is not None]
        assert len(winners) == 1
        winner = winners[0]
        assert first.read_pending(winner.execution_id) == second.verify_pending(winner) == winner


def test_admission_recovers_embedded_and_settlement_costs_in_quote_order():
    rel, ctx, calls, dex = gas_fixture(REVERSE)
    market = rel.dex.market
    embedded = Cost("pool", "EMBEDDED")
    charge = Cost("settlement-fee", "SEPARATE", market.settlement_id, market.settlement_scale)
    rel = replace(rel, dex=replace(rel.dex, buy_costs=(embedded, charge, *rel.dex.buy_costs)))
    dex["includedCosts"].extend(
        (
            {"costId": "pool", "treatment": "EMBEDDED", "settlementCostUnits": "15"},
            {
                "costId": "settlement-fee",
                "treatment": "SEPARATE",
                "settlementCostUnits": "0",
                "units": "0",
                "assetId": market.settlement_id,
                "scale": 2,
                "rateNumerator": "2",
                "rateDenominator": "2",
            },
        )
    )
    with closing(ExecutionGuard(":memory:")) as guard:
        request = evaluate(guard, rel, ctx, calls)
        recovered = guard.verify_pending(request)
        gas, pool, fee = recovered.buy.costs
        assert (gas.cost_id, pool.cost_id, fee.cost_id) == ("gas", "pool", "settlement-fee")
        assert (pool.treatment, pool.settlement_units) == ("EMBEDDED", 15)
        assert (fee.native, fee.numerator, fee.denominator) == (Amount(0, 2), 2, 2)
        assert fee.rate_ref is None and fee.rate is None
        assert (
            guard.verify_pending(replace(request, buy=replace(request.buy, costs=(fee, gas, pool))))
            is None
        )
