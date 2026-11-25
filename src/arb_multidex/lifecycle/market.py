"""Bind the owner configuration to S1 producers and S2 decision evidence."""

import json
from contextlib import ExitStack, contextmanager
from pathlib import Path

from arb_multidex.connections.mock import SAMPLE_MS, MockClock, MockKuCoin, MockSaucerSwap, mock_gas
from arb_multidex.execution import WorkerConfig
from arb_multidex.kucoin.market import top
from arb_multidex.opportunity import (
    Binding,
    Call,
    Cost,
    DecisionContext,
    Direction,
    Rate,
    Relationship,
    SourceState,
    Terms,
)
from arb_multidex.quotes import QuoteConfig, QuoteProducer
from arb_multidex.quotes.fees import GasPolicy, SpotFeePolicy
from arb_multidex.quotes.producer import unique_object

SCENARIOS = ("profitable", "no-profit", "incomplete", "stale", "unavailable")
MAX_AGE_MS = 5000
RATE_SOURCE = "mock/rate"


def load_owner(path, *, mock_sources=True):
    raw = json.loads(Path(path).read_text(encoding="utf-8"), object_pairs_hook=unique_object)
    relationship = raw["relationship"]
    if (
        relationship["base_mapping"] != "WHBAR-to-HBAR explicit 1:1 simulation mapping"
        or relationship["settlement_mapping"] != (
            "USDC-to-USDT explicit 1:1 simulation policy; not guaranteed market parity"
        )
        or raw["quote_base_volume"]["asset"] != "HBAR-equivalent-WHBAR"
        or raw["quote_base_volume"]["scale"] != 8
        or raw["minimum_net_profit"]["scale"] != 6
    ):
        raise ValueError("invalid simulation mapping or amount")
    return WorkerConfig.from_owner(path, mock_sources=mock_sources)


class MockMarkets:
    """Shared S1 feeds. Worker lifecycle operations never disconnect them."""

    provenance = "MOCK"

    def __init__(self, scenario):
        self.clock = MockClock(SAMPLE_MS + (MAX_AGE_MS if scenario == "stale" else 0))
        self.dex = MockSaucerSwap(self.clock)
        self.spot = MockKuCoin("HBAR-USDT", 8, 6, self.clock)
        if scenario != "unavailable":
            bid, ask = ("0.052", "0.053") if scenario == "no-profit" else ("0.054", "0.055")
            self.spot.snapshot = top(SAMPLE_MS, bid, "300", ask, "300")
        self.feeds = {feed.source_id: feed for feed in (self.dex, self.spot)}

    def bind(self, worker):
        dex_costs = (
            Cost("pool-fee", "EMBEDDED"),
            Cost("gas", "SEPARATE", worker.gas_asset, worker.gas_scale, RATE_SOURCE),
        )
        spot_costs = (
            Cost("spot-fee", "SEPARATE", worker.kucoin.settlement_id, worker.kucoin.settlement_scale),
        )
        terms = Terms(worker.volume, worker.minimum_profit)
        relation = Relationship(
            worker.relationship_id, "owner-simulation-v1", "mock-pool-gas-spot-v1",
            Binding(worker.dex, MAX_AGE_MS, dex_costs, dex_costs),
            Binding(worker.kucoin, MAX_AGE_MS, spot_costs, spot_costs),
            (worker.dex.base_id, worker.kucoin.base_id),
            (worker.dex.settlement_id, worker.kucoin.settlement_id),
            terms, terms, 1000,
        )
        fees = (GasPolicy(RATE_SOURCE, mock_gas), SpotFeePolicy(10))
        producers = {
            market.source_id: QuoteProducer(
                self.feeds[market.source_id],
                QuoteConfig(
                    relation.relationship_id, relation.mapping_revision, relation.fee_policy_id,
                    worker.volume.at(market.base_scale), MAX_AGE_MS,
                ),
                policy,
            )
            for market, policy in zip((worker.dex, worker.kucoin), fees)
        }
        return relation, producers

    def quotes(self, relation, producers):
        calls = {}
        for direction in Direction:
            for side in ("BUY", "SELL"):
                request = relation.request(direction, side)
                calls[direction, side] = Call(
                    request, producers[request["sourceId"]].quote(request)
                )
        return calls

    @contextmanager
    def context(self, relation):
        ids = (relation.dex.market.source_id, relation.kucoin.market.source_id)
        with ExitStack() as stack:
            sources = {}
            for source in sorted(ids):
                feed = self.feeds[source]
                stack.enter_context(feed.state_lock)
                sources[source] = SourceState(
                    feed.epoch, feed.last is not None and not feed.recovering,
                    max(feed.recovered_at or 0, feed.last.observed_ms if feed.last else 0),
                )
            rates = {}
            if self.dex.last is not None:
                market = relation.dex.market
                gas = mock_gas(self.dex, self.dex.last, "BUY", relation.buy_dex.volume.units)
                requirement = relation.dex.buy_costs[1]
                rates[gas.rate_ref] = Rate(
                    gas.rate_source, requirement.asset_id, requirement.scale,
                    market.settlement_id, market.settlement_scale, gas.numerator, gas.denominator,
                    gas.observed_ms, gas.valid_until_ms,
                )
            yield DecisionContext(self.clock(), sources, rates)
