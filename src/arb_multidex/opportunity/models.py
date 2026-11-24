"""Explicit SIM-01 market bindings, decision inputs, and S3 handoff types."""

import re
from collections.abc import Mapping
from dataclasses import dataclass
from enum import Enum
from uuid import uuid4


def token(value):
    return isinstance(value, str) and re.fullmatch(r"[A-Za-z0-9._:/-]{1,128}", value) is not None


def valid_scale(value):
    return type(value) is int and 0 <= value <= 38


@dataclass(frozen=True)
class Amount:
    """An exact number of atoms, including signed profits."""

    units: int
    scale: int

    def __post_init__(self):
        if type(self.units) is not int or not valid_scale(self.scale):
            raise ValueError("invalid amount")

    def at(self, scale):
        if scale >= self.scale:
            return self.units * 10 ** (scale - self.scale)
        units, remainder = divmod(self.units, 10 ** (self.scale - scale))
        if remainder:
            raise ValueError("amount cannot be represented at this scale")
        return units


class Direction(str, Enum):
    BUY_KUCOIN = "BUY_KUCOIN_SELL_DEX"
    BUY_DEX = "BUY_DEX_SELL_KUCOIN"


@dataclass(frozen=True)
class Terms:
    volume: Amount
    threshold: Amount  # In the explicitly mapped 1:1 settlement currency.

    def __post_init__(self):
        if self.volume.units <= 0 or self.threshold.units < 0:
            raise ValueError("volume must be positive and threshold nonnegative")


@dataclass(frozen=True)
class Market:
    source_id: str
    venue: str
    base_id: str
    settlement_id: str
    base_scale: int
    settlement_scale: int
    network: str | None = None
    pool: str | None = None
    symbol: str | None = None

    def __post_init__(self):
        if (
            not all(map(token, (self.source_id, self.venue, self.base_id, self.settlement_id)))
            or self.base_id == self.settlement_id
            or not all(map(valid_scale, (self.base_scale, self.settlement_scale)))
        ):
            raise ValueError("invalid market identity or scales")


@dataclass(frozen=True)
class Cost:
    """Required cost. Foreign separate costs also require a trusted rate source."""

    cost_id: str
    treatment: str
    asset_id: str | None = None
    scale: int | None = None
    rate_source: str | None = None

    def __post_init__(self):
        if not token(self.cost_id) or self.treatment not in ("EMBEDDED", "SEPARATE"):
            raise ValueError("invalid required cost")
        if self.treatment == "SEPARATE" and not (token(self.asset_id) and valid_scale(self.scale)):
            raise ValueError("separate costs need a registered asset and scale")


@dataclass(frozen=True)
class Binding:
    market: Market
    max_age_ms: int
    buy_costs: tuple[Cost, ...]
    sell_costs: tuple[Cost, ...]

    def __post_init__(self):
        if type(self.max_age_ms) is not int or self.max_age_ms <= 0:
            raise ValueError("max_age_ms must be positive")
        for costs in (self.buy_costs, self.sell_costs):
            if (
                type(costs) is not tuple
                or len(costs) > 32
                or len({c.cost_id for c in costs}) != len(costs)
            ):
                raise ValueError("invalid required cost set")
            for c in costs:
                if c.treatment == "SEPARATE":
                    if c.asset_id == self.market.base_id:
                        raise ValueError("base costs must be embedded")
                    if c.asset_id == self.market.settlement_id:
                        if c.scale != self.market.settlement_scale:
                            raise ValueError("wrong settlement cost scale")
                    elif not token(c.rate_source):
                        raise ValueError("foreign cost requires a rate source")


@dataclass(frozen=True)
class Relationship:
    relationship_id: str
    mapping_revision: str
    fee_policy_id: str
    dex: Binding
    kucoin: Binding
    equivalent_bases: tuple[str, str] | None  # Ordered DEX, KuCoin.
    one_to_one_settlements: tuple[str, str] | None
    buy_kucoin: Terms
    buy_dex: Terms
    max_skew_ms: int

    def __post_init__(self):
        if not all(map(token, (self.relationship_id, self.mapping_revision, self.fee_policy_id))):
            raise ValueError("invalid relationship identity")
        dex, spot = self.dex.market, self.kucoin.market
        if dex.source_id == spot.source_id or not all(map(token, (dex.network, dex.pool, spot.symbol))):
            raise ValueError("configure distinct markets, DEX network/pool and KuCoin symbol")
        if type(self.max_skew_ms) is not int or self.max_skew_ms < 0:
            raise ValueError("max_skew_ms must be nonnegative")
        for terms in (self.buy_kucoin, self.buy_dex):
            for market in (dex, spot):
                if terms.volume.at(market.base_scale) >= 10**78:
                    raise ValueError("base volume exceeds the wire limit")

    def legs(self, direction):
        if direction == Direction.BUY_KUCOIN:
            return self.kucoin, self.dex, self.buy_kucoin
        if direction == Direction.BUY_DEX:
            return self.dex, self.kucoin, self.buy_dex
        raise ValueError("unknown direction")

    def request(self, direction, side):
        """Generate a fresh SIM-01 request. Retain it alongside its call's reply."""
        buy, sell, terms = self.legs(direction)
        market = {"BUY": buy, "SELL": sell}[side].market
        return {
            "requestId": uuid4().hex,
            "relationshipId": self.relationship_id,
            "mappingRevision": self.mapping_revision,
            "feePolicyId": self.fee_policy_id,
            "mode": "SIMULATION",
            "sourceId": market.source_id,
            "side": side,
            "baseAssetId": market.base_id,
            "settlementAssetId": market.settlement_id,
            "baseUnits": str(terms.volume.at(market.base_scale)),
            "baseScale": market.base_scale,
            "settlementScale": market.settlement_scale,
        }


@dataclass(frozen=True)
class Call:
    request: dict | bytes | str
    response: dict | bytes | str


@dataclass(frozen=True)
class SourceState:
    """Trusted current generation and earliest eligible snapshot (UTC epoch ms).

    Set the floor to the latest accepted market observation, also respecting the
    recovery instant. Set ready only after the producer validates recovery.
    """

    epoch: str | None
    ready: bool
    earliest_ms: int


@dataclass(frozen=True)
class Rate:
    source: str
    asset_id: str
    scale: int
    settlement_id: str
    settlement_scale: int
    numerator: int
    denominator: int
    observed_ms: int
    expires_ms: int


@dataclass(frozen=True)
class DecisionContext:
    """Trusted state for one decision. All timestamps use integer UTC epoch ms."""

    now_ms: int | None
    sources: Mapping[str, SourceState]
    rates: Mapping[str, Rate]  # Immutable rate observations indexed by rateRef.


@dataclass(frozen=True)
class AcceptedCost:
    """One component of the quote, expressed in the leg's settlement atoms."""

    cost_id: str
    treatment: str
    settlement_units: int


@dataclass(frozen=True)
class SeparateCost(AcceptedCost):
    asset_id: str
    native: Amount
    numerator: int
    denominator: int
    rate_ref: str | None
    rate: Rate | None  # Exact trusted observation for a foreign-asset conversion.


@dataclass(frozen=True)
class Leg:
    mode: str
    market: Market
    request_id: str
    side: str
    base: Amount
    settlement: Amount
    epoch: str
    observed_ms: int
    expires_ms: int  # Earliest of quote and required rate expiries.
    quote_expires_ms: int
    gross_settlement: Amount
    costs: tuple[AcceptedCost, ...]


@dataclass(frozen=True)
class ExecutionRequest:
    execution_id: str
    relationship_id: str
    mapping_revision: str
    fee_policy_id: str
    direction: Direction
    buy: Leg
    sell: Leg
    net_profit: Amount
    threshold: Amount
    evaluated_ms: int
    expires_ms: int
    mode: str = "SIMULATION"
    status: str = "ACCEPTED"
