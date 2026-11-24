"""Evaluate one ready worker at a time. No coordinator-wide quote barrier."""

from collections import Counter
from contextlib import ExitStack
from dataclasses import dataclass, replace

from .guard import execution_id
from .models import Amount, DecisionContext, Direction, ExecutionRequest
from .validation import ERROR_ORDER, Checked, validate


@dataclass(frozen=True)
class Decision:
    relationship_id: str
    direction: Direction
    buy_request_id: str | None
    sell_request_id: str | None
    code: str | None = None
    primary_request_id: str | None = None
    buy_error: str | None = None
    sell_error: str | None = None
    net_profit: Amount | None = None
    qualifies: bool = False


@dataclass(frozen=True)
class Evaluation:
    decisions: tuple[Decision, ...]
    execution_request: ExecutionRequest | None


def compare(relationship, direction, buy, sell):
    decision = Decision(relationship.relationship_id, direction, buy.request_id, sell.request_id)
    errors = [leg for leg in (buy, sell) if leg.code is not None]
    if errors:
        primary = min(errors, key=lambda leg: ERROR_ORDER.index(leg.code))
        return replace(
            decision,
            code=primary.code,
            primary_request_id=primary.request_id,
            buy_error=buy.code,
            sell_error=sell.code,
        )
    buy_market, sell_market, terms = relationship.legs(direction)
    a, b = buy.leg, sell.leg
    if (
        not buy.bound
        or not sell.bound
        or a.mode != "SIMULATION"
        or b.mode != "SIMULATION"
        or a.market != buy_market.market
        or b.market != sell_market.market
        or (a.side, b.side) != ("BUY", "SELL")
    ):
        return replace(decision, code="INCOMPARABLE_QUOTES")
    dex, spot = relationship.dex.market, relationship.kucoin.market
    bases = (dex.base_id, spot.base_id)
    settlements = (dex.settlement_id, spot.settlement_id)
    if relationship.equivalent_bases != bases or relationship.one_to_one_settlements != settlements:
        return replace(decision, code="ASSET_MISMATCH")
    scale = max(a.base.scale, b.base.scale, terms.volume.scale)
    if not a.base.at(scale) == b.base.at(scale) == terms.volume.at(scale):
        return replace(decision, code="INCOMPARABLE_QUOTES")
    if abs(a.observed_ms - b.observed_ms) > relationship.max_skew_ms:
        return replace(decision, code="QUOTE_TIME_SKEW")
    scale = max(a.settlement.scale, b.settlement.scale, terms.threshold.scale)
    profit = b.settlement.at(scale) - a.settlement.at(scale)
    return replace(
        decision, net_profit=Amount(profit, scale), qualifies=profit >= terms.threshold.at(scale)
    )


class OpportunityDetector:
    """Register up to three active relationships before starting workers.

    evaluate(id, calls) takes {(Direction, "BUY"|"SELL"): Call(original, reply)}.
    Each invocation may supply either or both directions, including a previously
    retained original/reply pair. Missing legs fail only their own direction.

    context(relationship) is a context manager supplied by the orchestrator.
    It pins current source epochs, recovery/watermark state and trusted rate
    evidence through admission, and samples the shared clock once. Acquire feed
    state locks in source-ID order when markets are shared. Perform market I/O
    before calling evaluate. S3 rechecks eligibility before acting.
    """

    def __init__(self, guard, context):
        self._relationships = {}
        self._guard = guard
        self._context = context

    def register(self, relationship):
        rid = relationship.relationship_id
        if rid in self._relationships or len(self._relationships) >= 3:
            raise ValueError("duplicate relationship or three active workers already registered")
        # One source ID always identifies exactly one immutable market.
        known = {
            b.market.source_id: b.market for r in self._relationships.values() for b in (r.dex, r.kucoin)
        }
        for binding in (relationship.dex, relationship.kucoin):
            if known.get(binding.market.source_id, binding.market) != binding.market:
                raise ValueError("source ID reused for a different market")
        self._relationships[rid] = relationship

    def evaluate(self, relationship_id, calls):
        """Return both decisions and at most one newly admitted S3 request."""
        relationship = self._relationships[relationship_id]
        with ExitStack() as stack:
            try:
                context = stack.enter_context(self._context(relationship))
            except OSError:
                # A local clock or source-state outage makes only this worker unavailable.
                context = DecisionContext(None, {}, {})
            checked = {
                (d, side): validate(calls.get((d, side)), relationship, context)
                for d in Direction
                for side in ("BUY", "SELL")
            }
            ids = Counter(item.request_id for item in checked.values() if item.request_id is not None)
            for slot, item in checked.items():
                if ids[item.request_id] > 1:
                    checked[slot] = Checked(item.request_id, "INVALID_QUOTE")
            decisions = tuple(
                compare(relationship, d, checked[d, "BUY"], checked[d, "SELL"]) for d in Direction
            )
            eligible = [d for d in decisions if d.qualifies]
            request = None
            if eligible:
                # Compare at a common scale. Equal profits use declaration order.
                scale = max(d.net_profit.scale for d in eligible)
                best = max(eligible, key=lambda d: d.net_profit.at(scale))
                buy, sell = (checked[best.direction, side].leg for side in ("BUY", "SELL"))
                terms = relationship.legs(best.direction)[2]
                candidate = ExecutionRequest(
                    execution_id(relationship, best.direction, buy, sell),
                    relationship_id,
                    relationship.mapping_revision,
                    relationship.fee_policy_id,
                    best.direction,
                    buy,
                    sell,
                    best.net_profit,
                    terms.threshold,
                    context.now_ms,
                    min(buy.expires_ms, sell.expires_ms),
                )
                if self._guard._admit(candidate, relationship.dex.market.network):
                    request = candidate
            return Evaluation(decisions, request)
