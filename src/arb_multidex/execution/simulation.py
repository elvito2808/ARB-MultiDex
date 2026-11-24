"""Project an admitted S2 request into simulated, venue-local asset movements."""

import json
from dataclasses import dataclass, replace
from pathlib import Path

from arb_multidex.dex.saucerswap import PAIR, SOURCE, USDC, WHBAR
from arb_multidex.opportunity import Amount, Direction, ExecutionRequest, Market
from arb_multidex.opportunity.validation import (
    Rejection,
    atoms,
    check_cost_fields,
    message,
    observation,
)


@dataclass(frozen=True)
class WorkerConfig:
    """One explicit 1:1 market mapping and opening inventory, in integer atoms.

    Inventory order is DEX base, settlement, gas, then KuCoin base, settlement.
    DEX balances share a network wallet. KuCoin balances share a spot account.
    """

    relationship_id: str
    dex: Market
    kucoin: Market
    gas_asset: str
    gas_scale: int
    volume: Amount
    minimum_profit: Amount
    opening: tuple[int, int, int, int, int]

    def __post_init__(self):
        if (
            not self.dex.network
            or not self.dex.pool
            or not self.kucoin.symbol
            or self.gas_asset in (self.dex.base_id, self.dex.settlement_id)
            or type(self.gas_scale) is not int
            or not 0 <= self.gas_scale <= 38
            or self.volume.units <= 0
            or self.minimum_profit.units < 0
            or len(self.opening) != 5
            or any(type(n) is not int or n < 0 for n in self.opening)
        ):
            raise ValueError("invalid simulated worker configuration")

    @property
    def assets(self):
        wallet = "dex/" + self.dex.network
        return (
            (wallet, self.dex.base_id, self.dex.base_scale),
            (wallet, self.dex.settlement_id, self.dex.settlement_scale),
            (wallet, self.gas_asset, self.gas_scale),
            ("kucoin", self.kucoin.base_id, self.kucoin.base_scale),
            ("kucoin", self.kucoin.settlement_id, self.kucoin.settlement_scale),
        )

    @classmethod
    def from_owner(cls, path, *, mock_sources=True):
        """Load the owner fixture and bind its named tokens to their S1 market IDs."""
        cfg = json.loads(Path(path).read_text())
        rel = cfg["relationship"]
        dex, spot = rel["dex"], rel["kucoin"]
        gas = cfg["gas_policy"]
        if (
            cfg["schema"] != "simulation-owner-v1"
            or cfg["mode"] != "SIMULATION"
            or cfg["minimum_net_profit"]["comparison"] != ">="
            or cfg["minimum_net_profit"]["asset"] != "USDT"
            or gas["mode"] != "SIMULATED_ONLY"
            or gas["estimate_required"] is not True
            or gas["reject_if_missing"] is not True
            or gas["separate_from_initial_hbar_buffer"] is not True
            or (
                dex["network"],
                dex["chainId"],
                dex["dex"],
                dex["pool"].lower(),
                dex["base"],
                dex["settlement"],
            )
            != ("hedera-mainnet", 295, "saucerswap-v1", PAIR, "WHBAR", "USDC")
            or (spot["symbol"], spot["base"], spot["settlement"]) != ("HBAR-USDT", "HBAR", "USDT")
        ):
            raise ValueError("unsupported owner simulation configuration")

        def amount(entry):
            return Amount(atoms(entry["units"], allow_zero=True), entry["scale"])

        prefix = "mock/" if mock_sources else ""
        balances = cfg["initial_balances"]
        opening = [
            amount(balances[venue][name])
            for venue, name in (
                ("dex", "WHBAR"),
                ("dex", "USDC"),
                ("dex", "HBAR"),
                ("kucoin", "HBAR"),
                ("kucoin", "USDT"),
            )
        ]
        if [a.scale for a in opening] != [8, 6, 8, 8, 6]:
            raise ValueError("unexpected owner balance scale")
        return cls(
            rel["id"],
            Market(
                prefix + SOURCE, dex["dex"], WHBAR, USDC, 8, 6, network=dex["network"], pool=PAIR
            ),
            Market(
                prefix + "kucoin/spot/HBAR-USDT",
                "kucoin-spot",
                "kucoin/HBAR",
                "kucoin/USDT",
                8,
                6,
                symbol=spot["symbol"],
            ),
            "hedera/295/HBAR",
            8,
            amount(cfg["quote_base_volume"]),
            amount(cfg["minimum_net_profit"]),
            tuple(a.units for a in opening),
        )


@dataclass(frozen=True)
class FailurePlan:
    """Final simulated receipts by attempt. Missing entries succeed. Failures consume no gas."""

    dex: tuple[str, ...] = ()
    kucoin: tuple[str, ...] = ()

    def __post_init__(self):
        for script, allowed in (
            (self.dex, {"SUCCESS", "FAILED", "UNKNOWN"}),
            (self.kucoin, {"SUCCESS", "FAILED", "UNKNOWN", "PARTIAL"}),
        ):
            if (
                type(script) is not tuple
                or len(script) > 3
                or any(x not in allowed for x in script)
            ):
                raise ValueError("invalid simulated receipt sequence")


def _gas(request, dex, config, original_quote):
    # S2's Leg stores final settlement, but drops includedCosts. Use the exact S1
    # envelope retained by the caller for that S2 evaluation, never a fresh quote.
    quote = message(original_quote)
    expected = {
        "status": "OK",
        "requestId": dex.request_id,
        "relationshipId": request.relationship_id,
        "mappingRevision": request.mapping_revision,
        "feePolicyId": request.fee_policy_id,
        "mode": "SIMULATION",
        "sourceId": dex.market.source_id,
        "connectionEpoch": dex.epoch,
        "side": dex.side,
        "baseAssetId": dex.market.base_id,
        "settlementAssetId": dex.market.settlement_id,
        "baseUnits": str(dex.base.units),
        "baseScale": dex.base.scale,
        "settlementScale": dex.settlement.scale,
        "settlementUnits": str(dex.settlement.units),
    }
    if (
        any(quote.get(k) != v for k, v in expected.items())
        or observation(quote["observedAt"]) != dex.observed_ms
    ):
        raise ValueError("DEX quote differs from accepted leg")
    # The owner DEX quote contains an embedded pool fee and one separate native gas cost.
    check_cost_fields(quote["includedCosts"])
    fees = [fee for fee in quote["includedCosts"] if fee["treatment"] != "EMBEDDED"]
    if len(fees) != 1:
        raise ValueError("missing or unsupported separate DEX cost")
    fee = fees[0]
    if (fee["costId"], fee["treatment"], fee["assetId"], fee["scale"]) != (
        "gas",
        "SEPARATE",
        config.gas_asset,
        config.gas_scale,
    ) or not fee["rateRef"]:
        raise ValueError("invalid simulated gas evidence")
    gas = atoms(fee["units"], allow_zero=True)
    denominator = atoms(fee["rateDenominator"])
    cost = (gas * atoms(fee["rateNumerator"]) + denominator - 1) // denominator
    gross = atoms(quote["grossSettlementUnits"])
    final = gross + cost if dex.side == "BUY" else gross - cost
    if cost != atoms(fee["settlementCostUnits"], allow_zero=True) or final != dex.settlement.units:
        raise ValueError("gas evidence disagrees with final settlement")
    return gross, gas, fee


def prepare(request: ExecutionRequest, config, dex_quote):
    """Validate the accepted terms and produce signed ledger entries per adapter."""
    if type(request.evaluated_ms) is not int or type(request.expires_ms) is not int:
        raise ValueError("invalid request timestamps")
    if request.status != "ACCEPTED" or request.direction not in tuple(Direction):
        raise ValueError("expected an accepted S2 execution request")
    if (request.mode, request.buy.mode, request.sell.mode) != ("SIMULATION",) * 3:
        raise ValueError("execution requires SIMULATION")
    if (request.buy.side, request.sell.side) != ("BUY", "SELL"):
        raise ValueError("invalid sides")
    dex, spot = (
        (request.buy, request.sell)
        if request.direction == Direction.BUY_DEX
        else (request.sell, request.buy)
    )
    for leg, market in ((dex, config.dex), (spot, config.kucoin)):
        received = leg.market
        # The owner JSON spells this Hedera EVM pool in checksum case; S1 uses lowercase.
        if market.pool == PAIR and isinstance(received.pool, str):
            received = replace(received, pool=received.pool.lower())
        if (
            received != market
            or leg.base.scale != market.base_scale
            or leg.settlement.scale != market.settlement_scale
            or leg.settlement.units <= 0
            or leg.base.units != config.volume.at(market.base_scale)
        ):
            raise ValueError("request differs from configured market or volume")
        if (
            type(leg.observed_ms) is not int
            or type(leg.expires_ms) is not int
            or leg.observed_ms > request.evaluated_ms
            or leg.expires_ms < request.expires_ms
        ):
            raise ValueError("invalid quote lifetime")
    scale = max(
        request.buy.settlement.scale,
        request.sell.settlement.scale,
        request.net_profit.scale,
        request.threshold.scale,
        config.minimum_profit.scale,
    )
    profit = request.sell.settlement.at(scale) - request.buy.settlement.at(scale)
    if profit != request.net_profit.at(scale) or profit < max(
        request.threshold.at(scale), config.minimum_profit.at(scale)
    ):
        raise ValueError("invalid or insufficient net profit")
    try:
        gross, gas, evidence = _gas(request, dex, config, dex_quote)
    except (KeyError, TypeError, ValueError, Rejection) as exc:
        raise ValueError("missing or invalid matching DEX gas evidence") from exc
    sign = 1 if dex.side == "BUY" else -1
    amounts = (
        sign * dex.base.units,
        -sign * gross,
        -gas,
        -sign * spot.base.units,
        sign * spot.settlement.units,
    )
    entries = [
        [account, asset, delta] for (account, asset, _), delta in zip(config.assets, amounts)
    ]
    return {"DEX": entries[:3], "KUCOIN": entries[3:]}, evidence


def simulate_dex(entries, outcome):
    """Settle the atomic swap and its native gas only on a confirmed success."""
    if outcome == "SUCCESS":
        return entries
    if outcome == "FAILED":
        return [[account, asset, 0] for account, asset, _ in entries]
    raise ValueError("a final simulated DEX receipt is required")


def simulate_kucoin(entries, outcome, filled):
    """Return incremental deltas. PARTIAL confirms half the remaining base atoms."""
    total = abs(entries[0][2])
    if outcome == "SUCCESS":
        end = total
    elif outcome == "PARTIAL":
        end = filled + (total - filled) // 2
    elif outcome == "FAILED":
        end = filled
    else:
        raise ValueError("a final simulated receipt is required")
    # Floor signed credits / round costs up. Cumulative differences allocate
    # every atom exactly once across partial fills.
    return [
        [account, asset, delta * end // total - delta * filled // total]
        for account, asset, delta in entries
    ]
