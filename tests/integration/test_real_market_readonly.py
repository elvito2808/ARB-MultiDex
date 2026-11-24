"""Opt-in public probes: ARB_REAL_MARKET_READONLY=1 PYTHONPATH=src pytest -s this_file."""

import json
import os
from pathlib import Path

import pytest
import yaml

from arb_multidex.connections.real_markets import RealMarkets


@pytest.mark.skipif(
    os.getenv("ARB_REAL_MARKET_READONLY") != "1", reason="opt-in public read-only connectivity"
)
@pytest.mark.parametrize("venue", ["dex", "kucoin"])
def test_public_market_readonly(venue):
    path = Path(__file__).resolve().parents[2] / "config" / "markets.example.yaml"
    settings = yaml.safe_load(path.read_text())["real_markets"]
    endpoint = os.getenv("ARB_HEDERA_READONLY_RPC")
    if endpoint:
        settings["dex"]["rpc_endpoints"] = [endpoint]
    provider = RealMarkets(settings)
    try:
        report = provider.inspect(venue)
    finally:
        provider.close()
    print(json.dumps(report, sort_keys=True))
    assert report["provenance"] == "REAL"
    if report["observedAt"] is None:
        assert report["error"]
        pytest.skip(report["error"])
    assert report["connectionEpoch"] and report["availableDepth"]
    assert set(report["sides"]) == {"BUY", "SELL"}
    # Public observations remain useful even when stale or when owner costs are unavailable.
    assert all(side.get("grossPrice") or side.get("pricingError")
               for side in report["sides"].values())
