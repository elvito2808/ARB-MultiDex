"""Explicit simulation fee policies, evaluated at publication time."""

import re
from collections.abc import Callable
from dataclasses import dataclass

from arb_multidex.connections.feed import Feed, MarketError

MAX_UNITS = 10**78 - 1
ID = re.compile(r"[A-Za-z0-9._:/-]{1,128}")


def identifier(value):
    return isinstance(value, str) and ID.fullmatch(value) is not None


def fee_error():
    return MarketError("FEE_UNAVAILABLE", "required fee or conversion unavailable")


@dataclass(frozen=True)
class GasEstimate:
    """Trusted estimator result in HBAR tinybars. Rate is USDC atoms per tinybar."""

    tinybars: int
    numerator: int
    denominator: int
    rate_ref: str
    rate_source: str
    observed_ms: int
    valid_until_ms: int


@dataclass(frozen=True)
class GasPolicy:
    """Bind the estimator to this market, side, volume and validation instant."""

    rate_source: str | None
    estimate: Callable[[Feed, object, str, int], GasEstimate | None] | None = None

    def evidence(self, feed, snap, side, quantity):
        return self.estimate(feed, snap, side, quantity) if self.estimate else None

    def costs(self, feed, gross, now, gas):
        if (
            not isinstance(gas, GasEstimate)
            or not identifier(self.rate_source)
            or gas.rate_source != self.rate_source
            or not identifier(gas.rate_ref)
            or any(
                type(v) is not int
                for v in (
                    gas.tinybars,
                    gas.numerator,
                    gas.denominator,
                    gas.observed_ms,
                    gas.valid_until_ms,
                )
            )
            or not 0 <= gas.tinybars <= MAX_UNITS
            or not 1 <= gas.numerator <= MAX_UNITS
            or not 1 <= gas.denominator <= MAX_UNITS
            or not gas.observed_ms <= now < gas.valid_until_ms
        ):
            raise fee_error()
        amount = -(-gas.tinybars * gas.numerator // gas.denominator)
        if amount > MAX_UNITS:
            raise fee_error()
        return [
            {
                "costId": "gas",
                "treatment": "SEPARATE",
                "settlementCostUnits": str(amount),
                "assetId": "hedera/295/HBAR",
                "scale": 8,
                "units": str(gas.tinybars),
                "rateNumerator": str(gas.numerator),
                "rateDenominator": str(gas.denominator),
                "rateRef": gas.rate_ref,
            }
        ]


@dataclass(frozen=True)
class SpotFeePolicy:
    """Explicit simulation schedule for a fee charged in the settlement asset."""

    basis_points: int | None

    def evidence(self, feed, snap, side, quantity):
        return self.basis_points

    def costs(self, feed, gross, now, evidence):
        bps = evidence
        if type(bps) is not int or not 0 <= bps < 10000:
            raise fee_error()
        amount = -(-gross * bps // 10000)
        return [
            {
                "costId": "spot-fee",
                "treatment": "SEPARATE",
                "settlementCostUnits": str(amount),
                "assetId": feed.settlement_id,
                "scale": feed.settlement_scale,
                "units": str(amount),
                "rateNumerator": "1",
                "rateDenominator": "1",
            }
        ]
