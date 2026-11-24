"""Build one source from a config/markets.example.yaml market entry (already parsed)."""

from arb_multidex.connections.feed import utc_ms
from arb_multidex.connections.mock import MockClock, MockKuCoin, MockSaucerSwap, mock_gas
from arb_multidex.connections.rpc import ManagedRPC
from arb_multidex.dex import SaucerSwap
from arb_multidex.kucoin import KuCoin

from .fees import GasPolicy, SpotFeePolicy
from .producer import QuoteConfig, QuoteProducer


def build_market(entry, *, clock=None, gas_estimator=None):
    """Share a MockClock between simulated markets. Inject a trusted public Gas estimator if available."""
    if entry["mode"] != "SIMULATION" or entry["provider"] not in ("mock", "public"):
        raise ValueError("expected simulation mode and an explicit market data provider")
    mock = entry["provider"] == "mock"
    if clock is None:
        clock = MockClock() if mock else utc_ms
    cfg = QuoteConfig(
        entry["relationship_id"],
        entry["mapping_revision"],
        entry["fee_policy_id"],
        entry["base_units"],
        entry["max_age_ms"],
    )
    if entry["venue"] == "saucerswap-v1":
        if mock:
            feed = MockSaucerSwap(clock)
            policy = GasPolicy("mock/rate", mock_gas)
        else:
            feed = SaucerSwap(ManagedRPC(entry["rpc_endpoints"]), clock=clock)
            policy = GasPolicy(entry["gas_rate_source"], gas_estimator)
    elif entry["venue"] == "kucoin-spot":
        symbol = entry["mock_symbol"] if mock else entry["symbol"]
        args = (symbol, entry["base_scale"], entry["settlement_scale"])
        feed = MockKuCoin(*args, clock=clock) if mock else KuCoin(*args, clock=clock)
        policy = SpotFeePolicy(entry["settlement_fee_bps"])
    else:
        raise ValueError("unknown venue")
    return QuoteProducer(feed, cfg, policy)
