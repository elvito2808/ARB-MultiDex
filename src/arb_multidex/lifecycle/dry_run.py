"""Real public market data and explicitly estimated costs for simulated execution."""

import hashlib
import json
from pathlib import Path

from arb_multidex.app import decimal_amount, open_runtime
from arb_multidex.connections.real_markets import GAS_ASSET, RealMarkets
from arb_multidex.lifecycle.market import load_owner
from arb_multidex.opportunity import Rate
from arb_multidex.quotes.fees import MAX_UNITS
from arb_multidex.quotes.producer import unique_object

POLICY = "ESTIMATED_COSTS_DRY_RUN"
RATE_SOURCE = "dry-run/estimated-hbar-usdc"
ESTIMATE_LIMITS = {
    "pool_fee_bps": (0, 9999),
    "kucoin_fee_bps": (0, 9999),
    "buy_gas_tinybars": (1, MAX_UNITS),
    "sell_gas_tinybars": (1, MAX_UNITS),
    "gas_usdc_atoms_per_hbar": (1, MAX_UNITS),
}


class EstimatedDryRunMarkets(RealMarkets):
    """Bind configured estimates to SIMULATION quotes without a venue fee verifier."""

    gas_basis = "ESTIMATED"

    def __init__(self, worker, settings, *, provenance="REAL", **options):
        for name, (minimum, maximum) in ESTIMATE_LIMITS.items():
            value = settings.get(name)
            if value is None:
                raise ValueError(f"MISSING_ESTIMATE: {name}")
            if type(value) is not int or not minimum <= value <= maximum:
                raise ValueError(f"INVALID_ESTIMATE: {name}")
        estimates = {name: settings[name] for name in ESTIMATE_LIMITS}
        revision = hashlib.sha256(json.dumps(estimates, sort_keys=True).encode()).hexdigest()
        dex_age = settings["dex_max_age_ms"]
        self.provenance = provenance
        self.batch = None
        self.estimates = estimates

        def rate_lookup(feed, snapshot, side, quantity):
            observed = snapshot.observed_ms
            return f"{RATE_SOURCE}/{revision}/{observed}", Rate(
                RATE_SOURCE, GAS_ASSET, 8, feed.settlement_id, 6,
                estimates["gas_usdc_atoms_per_hbar"], 10**8, observed, observed + dex_age,
            )

        config = {
            "relationship_id": worker.relationship_id,
            "mapping_revision": "whbar-hbar-usdc-usdt-dry-run-v1",
            "fee_policy_id": f"{POLICY}/{revision}",
            "base_units": worker.volume.units,
            "minimum_net_profit_units": worker.minimum_profit.units,
            "equivalent_bases": True,
            "one_to_one_settlements": True,
            "max_skew_ms": settings["max_skew_ms"],
            "dex": {
                "rpc_endpoints": settings["rpc_endpoints"],
                "pool": worker.dex.pool,
                "max_age_ms": dex_age,
                "pool_fee_bps": estimates["pool_fee_bps"],
                "gas_bound": {
                    "buy_tinybars": estimates["buy_gas_tinybars"],
                    "sell_tinybars": estimates["sell_gas_tinybars"],
                    "reference": "dry-run/configured-gas-estimate",
                },
                "gas_rate_source": RATE_SOURCE,
            },
            "kucoin": {
                "symbol": worker.kucoin.symbol,
                "base_scale": worker.kucoin.base_scale,
                "settlement_scale": worker.kucoin.settlement_scale,
                "max_age_ms": settings["kucoin_max_age_ms"],
                "settlement_fee_bps": estimates["kucoin_fee_bps"],
            },
        }
        super().__init__(config, rate_lookup=rate_lookup, **options)

    def _quote_with_fee(self, producer, venue, req, observation, remember):
        if req["mode"] != "SIMULATION":
            return self._error(req, "INVALID_QUOTE"), None
        return self._quote_at_bps(
            producer, venue, req, observation, remember, self.fee_estimates[venue],
        ), None

    def _fee_details(self, producer, fee):
        venue = "dex" if producer.feed is self.dex else "kucoin"
        return {"feeEstimate": {"basis": "ESTIMATED", "basisPoints": self.fee_estimates[venue]}}

    def bind(self, worker):
        return self.relationship, self.producers

    def quotes(self, relation, producers):
        self.batch = self.collect()
        return self.batch.calls

    def context(self, relation):
        return super().context(self.batch)

    def market_report(self):
        if self.batch is None:
            return {}
        report = dict(self.batch.observations)
        for venue, observation in report.items():
            observation["fees"]["basis"] = "ESTIMATED"
            for side in observation["sides"].values():
                for wire, display in (
                    ("grossSettlementUnits", "grossSettlement"),
                    ("settlementUnits", "estimatedNetSettlement"),
                ):
                    if wire in side:
                        side[display] = decimal_amount(int(side[wire]), 6)
                if "grossSettlementUnits" in side:
                    # Dividing a scale-6 amount by 250 yields an exact scale-9 price.
                    side["grossPricePerHBAR"] = decimal_amount(
                        int(side["grossSettlementUnits"]) * 4, 9,
                    )
            observation["settlementAsset"] = "USDC" if venue == "dex" else "USDT"
        return report


def run_dry_run(path, state_dir, stream, **market_options):
    raw = json.loads(Path(path).read_text(encoding="utf-8"), object_pairs_hook=unique_object)
    if raw.get("simulation_enabled") is False:
        raise ValueError("LIVE_NOT_AUTHORIZED: LIVE execution is unavailable")
    if raw.get("simulation_enabled") is not True:
        raise ValueError("simulation_enabled must be the boolean true")
    if raw.get("admission_policy") != POLICY:
        raise ValueError(f"admission_policy must be {POLICY}")
    worker = load_owner(path, mock_sources=False)
    if worker.volume.units != 250 * 10**8 or worker.minimum_profit.units != 150_000:
        raise ValueError("expected 250 HBAR and minimum net profit of 0.15 USDT")
    markets = EstimatedDryRunMarkets(worker, raw["dry_run"], **market_options)
    try:
        with open_runtime(
            (worker,), state_dir, markets, "dry-run", stream, journal_prefix="dry-run-",
        ) as app:
            app.recover()
            report = app.simulate(worker.relationship_id)
            report.update(
                admission_policy=POLICY,
                cost_basis="ESTIMATED",
                settlement_comparison="USDC/USDT 1:1 (owner-approved assumption)",
                estimates=markets.estimates,
                observations=markets.market_report(),
            )
            return report
    finally:
        markets.close()
