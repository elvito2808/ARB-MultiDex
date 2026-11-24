import socket
from pathlib import Path

import pytest
import yaml

from arb_multidex.connections.mock import MockClock
from arb_multidex.quotes.config import build_market


def example():
    path = Path(__file__).resolve().parents[3] / "config" / "markets.example.yaml"
    return yaml.safe_load(path.read_text())["offline_fixtures"]


def test_reproducible_example_without_network(monkeypatch):
    def offline(*args, **kwargs):
        pytest.fail("mock attempted network access")

    monkeypatch.setattr(socket, "socket", offline)
    clock = MockClock()

    def run():
        results = []
        for market in example().values():
            p = build_market(market, clock=clock)
            for side in ("BUY", "SELL"):
                res = p.quote(p.request(side, side))
                assert res["sourceId"].startswith("mock/") and res["mode"] == "SIMULATION"
                results.append(res)
        return results

    assert run() == run()


@pytest.mark.parametrize("venue", ["dex", "kucoin"])
def test_unconfigured_public_provider_and_live_mode_rejected(venue):
    entry = example()[venue]
    with pytest.raises(ValueError):
        build_market(entry | {"provider": "public"})
    with pytest.raises(ValueError):
        build_market(entry | {"mode": "LIVE"})
