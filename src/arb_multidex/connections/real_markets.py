"""Read-only market data for S4, with SIM-01 calls and separate REAL provenance.

Pass parsed config/markets.example.yaml's real_markets section to RealMarkets.
collect() supplies S2's calls. Evaluate under context(batch) to read current
source generations and the exact rates used by those calls. The SIM-01 mode
field is part of the existing wire contract. Execution mode belongs to S4/S3.
Use inspect("dex") or inspect("kucoin") to query either venue independently.
"""

from collections.abc import Mapping
from contextlib import ExitStack, contextmanager
from dataclasses import dataclass
from datetime import timedelta
from fractions import Fraction
from types import MappingProxyType

from arb_multidex.dex.saucerswap import FACTORY, USDC, WHBAR, SaucerSwap
from arb_multidex.kucoin import KuCoin
from arb_multidex.opportunity import (
    Amount,
    Binding,
    Call,
    Cost,
    DecisionContext,
    Direction,
    Market,
    Rate,
    Relationship,
    SourceState,
    Terms,
)
from arb_multidex.quotes import QuoteConfig, QuoteProducer
from arb_multidex.quotes.fees import MAX_UNITS, GasEstimate, GasPolicy, SpotFeePolicy, identifier
from arb_multidex.quotes.producer import UNIX_EPOCH

from .feed import MarketError, utc_ms
from .rpc import ManagedRPC

GAS_ASSET = "hedera/295/HBAR"


@dataclass(frozen=True)
class MarketBatch:
    calls: Mapping[tuple[Direction, str], Call]
    observations: Mapping[str, dict]
    rates: Mapping[str, Rate]
    provenance = "REAL"


@dataclass(frozen=True)
class GasBound:
    """Owner-supplied positive upper bound for the intended route in tinybars."""

    buy_tinybars: int
    sell_tinybars: int
    reference: str

    def __post_init__(self):
        if (
            not identifier(self.reference)
            or self.reference.startswith("mock/")
            or any(type(v) is not int or not 0 < v <= MAX_UNITS
                   for v in (self.buy_tinybars, self.sell_tinybars))
        ):
            raise ValueError("invalid gas bound")


def _utc(ms):
    return (UNIX_EPOCH + timedelta(milliseconds=ms)).isoformat(
        timespec="milliseconds"
    ).replace("+00:00", "Z")


def _source_state(feed):
    return SourceState(
        feed.epoch,
        feed.last is not None and not feed.recovering and feed.epoch == feed.last_epoch,
        max(feed.recovered_at or 0, feed.last.observed_ms if feed.last else 0),
    )


class RealMarkets:
    """Own shared public feeds. Use explicit offline fixtures for synthetic data.

    rate_lookup(feed, snapshot, side, base_atoms) returns (rate_ref, S2 Rate).
    The configured trusted source must supply a time-bound HBAR-to-USDC rate
    in settlement atoms per tinybar. Supply a conservative bound verified for
    the intended swap route and volume. Missing evidence prevents publication.
    """

    def __init__(
        self, config, *, clock=utc_ms, rate_lookup=None, rpc_options=None, kucoin_options=None
    ):
        dex, spot = config["dex"], config["kucoin"]
        if spot["symbol"] != "HBAR-USDT" or spot["base_scale"] != 8:
            raise ValueError("expected KuCoin HBAR-USDT with HBAR scale 8")
        if type(spot["settlement_scale"]) is not int or spot["settlement_scale"] != 6:
            raise ValueError("expected KuCoin HBAR-USDT with USDT settlement scale 6")
        self.clock = clock
        self.dex = SaucerSwap(
            ManagedRPC(dex["rpc_endpoints"], **(rpc_options or {})),
            pool=dex["pool"], fee_bps=dex["pool_fee_bps"], clock=clock,
        )
        self.kucoin = KuCoin(
            spot["symbol"], spot["base_scale"], spot["settlement_scale"], clock=clock,
            stream_max_age_ms=spot["max_age_ms"], **(kucoin_options or {}),
        )
        self.feeds = {"dex": self.dex, "kucoin": self.kucoin}
        self.bound = GasBound(**dex["gas_bound"]) if dex["gas_bound"] is not None else None
        self.rate_source = dex["gas_rate_source"]
        if self.rate_source is not None and (
            not identifier(self.rate_source) or self.rate_source.startswith("mock/")
        ):
            raise ValueError("invalid gas rate source")
        self.rate_lookup = rate_lookup
        spot_bps = spot["settlement_fee_bps"]
        if spot_bps is not None and (type(spot_bps) is not int or not 0 <= spot_bps < 10000):
            raise ValueError("invalid spot fee")
        self.policies = {
            "dex": GasPolicy(self.rate_source, self._gas),
            "kucoin": SpotFeePolicy(spot_bps),
        }
        d, k = self.dex, self.kucoin
        dex_market = Market(
            d.source_id, "saucerswap-v1", d.base_id, d.settlement_id, 8, 6,
            network="hedera-mainnet", pool=d.pool,
        )
        spot_market = Market(
            k.source_id, "kucoin-spot", k.base_id, k.settlement_id,
            k.base_scale, k.settlement_scale, symbol=k.symbol,
        )
        dex_costs = (
            Cost("pool-fee", "EMBEDDED"),
            Cost("gas", "SEPARATE", GAS_ASSET, 8, self.rate_source or "unconfigured"),
        )
        spot_costs = (Cost("spot-fee", "SEPARATE", k.settlement_id, k.settlement_scale),)
        terms = Terms(Amount(config["base_units"], 8), Amount(config["minimum_net_profit_units"], 6))
        self.relationship = Relationship(
            config["relationship_id"], config["mapping_revision"], config["fee_policy_id"],
            Binding(dex_market, dex["max_age_ms"], dex_costs, dex_costs),
            Binding(spot_market, spot["max_age_ms"], spot_costs, spot_costs),
            (d.base_id, k.base_id) if config["equivalent_bases"] is True else None,
            (d.settlement_id, k.settlement_id) if config["one_to_one_settlements"] is True else None,
            terms, terms, config["max_skew_ms"],
        )
        self.producers = {
            venue: QuoteProducer(
                feed,
                QuoteConfig(
                    self.relationship.relationship_id, self.relationship.mapping_revision,
                    self.relationship.fee_policy_id, terms.volume.at(feed.base_scale),
                    config[venue]["max_age_ms"],
                ),
                self.policies[venue],
            )
            for venue, feed in self.feeds.items()
        }

    def _gas(self, feed, snap, side, quantity):
        if self.bound is None or self.rate_lookup is None or self.rate_source is None:
            return None
        ref, rate = self.rate_lookup(feed, snap, side, quantity)
        if (
            not isinstance(rate, Rate) or not identifier(ref) or ref.startswith("mock/")
            or (rate.asset_id, rate.scale) != (GAS_ASSET, 8)
            or (rate.settlement_id, rate.settlement_scale) != (feed.settlement_id, 6)
        ):
            return None
        units = self.bound.buy_tinybars if side == "BUY" else self.bound.sell_tinybars
        return GasEstimate(
            units, rate.numerator, rate.denominator, ref, rate.source,
            rate.observed_ms, rate.expires_ms,
        )

    def _requests(self):
        requests = {venue: {} for venue in self.feeds}
        for direction in Direction:
            for side in ("BUY", "SELL"):
                request = self.relationship.request(direction, side)
                venue = "dex" if request["sourceId"] == self.dex.source_id else "kucoin"
                requests[venue][direction, side] = request
        return requests

    def _collect(self, venue, requests):
        producer = self.producers[venue]
        feed, cfg = producer.feed, producer.config
        diagnostic = {
            "market": venue, "source": feed.source_id, "provenance": "REAL",
            "baseVolume": str(Fraction(cfg.base_units, 10**feed.base_scale)),
            "observedAt": None, "availableDepth": None, "sides": {},
        }
        if venue == "dex":
            diagnostic["fees"] = {"poolBps": self.dex.fee_bps, "basis": "CONFIGURED_ESTIMATE"}
            diagnostic["pool"] = self.dex.pool
        else:
            diagnostic["fees"] = {
                "settlementBps": producer.fees.basis_points, "basis": "CONFIGURED_ESTIMATE",
            }
        rates, calls = {}, {}
        conflicting_refs = set()

        def remember(gas):
            if isinstance(gas, GasEstimate):
                rate = Rate(
                    gas.rate_source, GAS_ASSET, 8, feed.settlement_id, feed.settlement_scale,
                    gas.numerator, gas.denominator, gas.observed_ms, gas.valid_until_ms,
                )
                previous = rates.setdefault(gas.rate_ref, rate)
                if previous != rate:
                    # A reference must identify one immutable observation.
                    conflicting_refs.add(gas.rate_ref)

        with feed.lock:
            try:
                observation = producer.capture()
            except MarketError as exc:
                diagnostic["error"] = str(exc)
                for key, req in requests.items():
                    calls[key] = Call(req, self._error(req, exc.code))
                    diagnostic["sides"][req["side"]] = {"valid": False, "error": exc.code}
            else:
                snap, epoch = observation
                with feed.state_lock:
                    diagnostic["capturedEpoch"] = epoch
                    self._snapshot_details(venue, diagnostic, snap)
                for key, req in requests.items():
                    reply = producer.quote(req, observation=observation, on_gas=remember)
                    calls[key] = Call(req, reply)
                for key, call in calls.items():
                    req, reply = call.request, call.response
                    if any(cost.get("rateRef") in conflicting_refs
                           for cost in reply.get("includedCosts", ())):
                        reply = self._error(req, "FEE_UNAVAILABLE")
                        calls[key] = Call(req, reply)
                    diagnostic["sides"][req["side"]] = self._side_details(
                        producer, snap, req["side"], reply
                    )
            with feed.state_lock:
                state = _source_state(feed)
                diagnostic.update(
                    checkedAt=_utc(feed.now()), connectionEpoch=state.epoch,
                    connected=state.epoch is not None, sourceReady=state.ready,
                    transport=feed.route if venue == "kucoin" else "hedera-json-rpc",
                )
                if venue == "kucoin" and self.kucoin.last_error:
                    diagnostic["websocketError"] = self.kucoin.last_error
        return calls, diagnostic, {
            ref: rate for ref, rate in rates.items() if ref not in conflicting_refs
        }

    @staticmethod
    def _error(req, code):
        return {
            "status": "ERROR", "requestId": req["requestId"], "sourceId": req["sourceId"],
            "code": code,
        }

    @staticmethod
    def _snapshot_details(venue, diagnostic, snap):
        # Keep raw times available even when a malformed provider timestamp cannot be formatted.
        try:
            diagnostic["observedAt"] = _utc(snap.observed_ms)
        except OverflowError:
            diagnostic["observedAt"] = None
        diagnostic["observedMs"] = snap.observed_ms
        if venue == "dex":
            diagnostic.update(
                chainId=295, factory=FACTORY, baseToken=WHBAR, settlementToken=USDC,
                baseScale=8, settlementScale=6, blockNumber=snap.position,
                blockHash=snap.block_hash, reserveUpdateSeconds=snap.last_update_s,
                receivedAt=_utc(snap.receipt_ms),
                availableDepth={
                    "baseReserveAtoms": snap.base, "settlementReserveAtoms": snap.settlement,
                },
            )
        else:
            diagnostic.update(
                bestBid=str(snap.bid), bestAsk=str(snap.ask),
                availableDepth={"BUY": str(snap.ask_size), "SELL": str(snap.bid_size)},
            )

    def _side_details(self, producer, snap, side, reply):
        result = {"valid": reply["status"] == "OK"}
        if result["valid"]:
            result.update(
                costs=reply["includedCosts"], settlementUnits=reply["settlementUnits"],
                grossSettlementUnits=reply["grossSettlementUnits"],
            )
            for cost in reply["includedCosts"]:
                if cost["costId"] == "gas":
                    result["gas"] = {
                        "basis": "CONFIGURED_UPPER_BOUND", "reference": self.bound.reference,
                        "tinybars": cost["units"], "rateRef": cost["rateRef"],
                        "rateSource": self.rate_source,
                    }
        else:
            result["error"] = reply["code"]
        try:
            gross, embedded = producer.feed.price(snap, side, producer.config.base_units)
        except MarketError as exc:
            result["pricingError"] = str(exc)
        else:
            result.update(
                grossSettlementUnits=str(gross), embeddedCosts=embedded,
                grossPrice=str(Fraction(
                    gross * 10**producer.feed.base_scale,
                    producer.config.base_units * 10**producer.feed.settlement_scale,
                )),
            )
        return result

    def inspect(self, venue):
        """Query one venue, returning diagnostics even when costs prevent a valid quote."""
        return self._collect(venue, self._requests()[venue])[1]

    def collect(self):
        """Capture both sides of each venue from one snapshot per venue."""
        calls, observations, rates = {}, {}, {}
        for venue, requests in self._requests().items():
            venue_calls, observations[venue], venue_rates = self._collect(venue, requests)
            calls.update(venue_calls)
            rates.update(venue_rates)
        return MarketBatch(
            MappingProxyType(calls), MappingProxyType(observations), MappingProxyType(rates)
        )

    @contextmanager
    def context(self, batch):
        """Hold current source states stable while S2 evaluates this batch."""
        with ExitStack() as stack:
            sources = {}
            for feed in sorted(self.feeds.values(), key=lambda feed: feed.source_id):
                stack.enter_context(feed.state_lock)
                sources[feed.source_id] = _source_state(feed)
            yield DecisionContext(self.clock(), sources, batch.rates)

    def close(self):
        for feed in self.feeds.values():
            feed.disconnect()
