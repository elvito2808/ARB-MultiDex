"""Deterministic simulation providers. Advancing the clock never refreshes market data."""

from itertools import count

from arb_multidex.dex.saucerswap import Reserves, SaucerSwap
from arb_multidex.kucoin.market import KuCoin, top
from arb_multidex.quotes.fees import GasEstimate

SAMPLE_MS = 1795474157000


class MockClock:
    def __init__(self, now_ms=SAMPLE_MS):
        self.now_ms = now_ms

    def __call__(self):
        return self.now_ms


def _epochs():
    numbers = count(1)
    return lambda: f"mock-{next(numbers)}"


class MockSaucerSwap(SaucerSwap):
    def __init__(self, clock):
        super().__init__(None, clock=clock, epoch_id=_epochs())
        self.source_id = "mock/" + self.source_id
        self.snapshot = Reserves(
            102699974,
            "0x" + "01" * 32,
            SAMPLE_MS,
            198803970955,
            379049637493682,
            1795474043,
            SAMPLE_MS,
        )

    def read(self):
        with self.lock:
            self.connected()
            return self.snapshot


class MockKuCoin(KuCoin):
    def __init__(self, symbol, base_scale, settlement_scale, clock):
        super().__init__(symbol, base_scale, settlement_scale, clock=clock, epoch_id=_epochs())
        self.source_id = "mock/" + self.source_id
        self.snapshot = top(SAMPLE_MS, "0.052", "200", "0.053", "100")

    def read(self):
        with self.lock:
            self.connected()
            return self.snapshot


def mock_gas(feed, snap, side, quantity):
    """A synthetic five-tinybar cost at 1/2 settlement atom per tinybar."""
    return GasEstimate(
        5,
        1,
        2,
        f"mock/rate/{snap.observed_ms}",
        "mock/rate",
        snap.observed_ms,
        snap.observed_ms + 5000,
    )
