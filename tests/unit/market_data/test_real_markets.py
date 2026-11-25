from dataclasses import replace
from pathlib import Path

import pytest
import yaml

from arb_multidex.connections.http import TransportError
from arb_multidex.connections.real_markets import GAS_ASSET, RealMarkets, VenueFee
from arb_multidex.contracts import PAIR_ABI, TOKEN_ABI
from arb_multidex.dex.saucerswap import FACTORY, USDC, WHBAR
from arb_multidex.opportunity import Direction, Rate
from arb_multidex.opportunity.validation import validate

NOW = 1795554000000
POOL = "0x" + "12" * 20
ENDPOINT = "https://relay.invalid/api"
SOURCE = "test/trusted-hbar-usdc"
FEE_SOURCES = {"dex": "test/pool-fees", "kucoin": "test/spot-fees"}
BASE, SETTLEMENT = 100_000 * 10**8, 5_000 * 10**6


def words(*values):
    return "0x" + "".join(f"{value:064x}" for value in values)


class PublicTransports:
    def __init__(self):
        self.methods = []
        self.chain = 295
        self.now = NOW
        self.book_time = NOW
        self.ask_size = "251"
        self.block_time = NOW
        self.base_reserve = BASE
        self.settlement_reserve = SETTLEMENT

    def rpc(self, url, *, method, body, timeout):
        assert url == ENDPOINT and method == "POST"
        name, params = body["method"], body["params"]
        self.methods.append(name)
        if name == "eth_chainId":
            result = hex(self.chain)
        elif name == "eth_getBlockByNumber":
            assert params in (["latest", False], ["0x7b", False])
            result = {"number": "0x7b", "hash": words(123),
                      "timestamp": hex(self.block_time // 1000)}
        else:
            assert name == "eth_call"
            call, tag = params
            assert tag == "0x7b"
            fields = {
                (POOL, PAIR_ABI["token0"].selector): (int(USDC, 16),),
                (POOL, PAIR_ABI["token1"].selector): (int(WHBAR, 16),),
                (POOL, PAIR_ABI["factory"].selector): (int(FACTORY, 16),),
                (POOL, PAIR_ABI["getReserves"].selector): (
                    self.settlement_reserve, self.base_reserve, NOW // 1000,
                ),
                (USDC, TOKEN_ABI["decimals"].selector): (6,),
                (WHBAR, TOKEN_ABI["decimals"].selector): (8,),
            }
            result = words(*fields[call["to"], call["data"]])
        return {"jsonrpc": "2.0", "id": body["id"], "result": result}

    def kucoin(self, url, *, method, timeout):
        if url.endswith("/bullet-public"):
            assert method == "POST"
            raise TransportError("stream bootstrap unavailable")
        assert method == "GET"
        assert url == "https://api.kucoin.com/api/v1/market/orderbook/level1?symbol=HBAR-USDT"
        return {
            "code": "200000",
            "data": {"time": self.book_time, "bestBid": "0.054", "bestBidSize": "255",
                     "bestAsk": "0.055", "bestAskSize": self.ask_size},
        }


def gas_rate(feed, snap, side, quantity):
    return "test/rate/1", Rate(SOURCE, GAS_ASSET, 8, USDC, 6, 1, 2000, NOW - 10, NOW + 500)


def fixture_fee_lookup(req):
    dex = req["sourceId"].startswith("hedera/")
    venue = "dex" if dex else "kucoin"
    return VenueFee(
        FEE_SOURCES[venue], f"test/{venue}/fees/{req['side']}", req["sourceId"],
        "configured-venue-fees-and-gas-v1", req["side"], USDC if dex else "kucoin/USDT",
        25 if dex else 10, NOW - 10, NOW + 10_000,
    )


def provider(
    transports, *, rate_lookup=gas_rate, spot_fee=10, pool_fee=25,
    settlement_scale=6, volume=250, fee_lookup=fixture_fee_lookup, missing_fee_for=None,
):
    path = Path(__file__).resolve().parents[3] / "config" / "markets.example.yaml"
    config = yaml.safe_load(path.read_text())["real_markets"]
    config.update(one_to_one_settlements=True, base_units=volume * 10**8)
    config["dex"].update(
        rpc_endpoints=[ENDPOINT], pool=POOL, pool_fee_bps=pool_fee, gas_rate_source=SOURCE,
        fee_evidence_source=FEE_SOURCES["dex"],
        gas_bound={"buy_tinybars": 100001, "sell_tinybars": 80000, "reference": "test/route-1"},
    )
    config["kucoin"].update(
        settlement_fee_bps=spot_fee, settlement_scale=settlement_scale,
        fee_evidence_source=FEE_SOURCES["kucoin"],
    )

    def lookup(req):
        venue = "dex" if req["sourceId"].startswith("hedera/") else "kucoin"
        if missing_fee_for == venue:
            return None
        return fee_lookup(req)

    return RealMarkets(
        config, clock=lambda: transports.now, rate_lookup=rate_lookup,
        fee_lookup=None if fee_lookup is None else lookup,
        rpc_options={"http": transports.rpc, "attempts": 1},
        kucoin_options={"http": transports.kucoin},
    )


def test_reference_market_rejects_wrong_kucoin_settlement_scale():
    transports = PublicTransports()
    with pytest.raises(ValueError, match="USDT settlement scale 6"):
        provider(transports, settlement_scale=5)
    assert not transports.methods


@pytest.mark.parametrize(
    ("age_ms", "rpc_unavailable", "expected"),
    [
        (4999, False, "FEE_UNAVAILABLE"),
        (5000, False, "STALE_QUOTE"),
        (5001, False, "STALE_QUOTE"),
        (-1, False, "INVALID_QUOTE"),
        (5000, True, "SOURCE_UNAVAILABLE"),
    ],
)
def test_missing_pool_fee_respects_quote_error_precedence(age_ms, rpc_unavailable, expected):
    class UnavailableRPC(PublicTransports):
        def rpc(self, *args, **kwargs):
            raise TransportError("RPC unavailable")

    transports = UnavailableRPC() if rpc_unavailable else PublicTransports()
    transports.now = transports.block_time + age_ms
    markets = provider(transports, pool_fee=None, missing_fee_for="dex")
    report = markets.inspect("dex")
    assert {side: details["error"] for side, details in report["sides"].items()} == {
        "BUY": expected, "SELL": expected,
    }


@pytest.mark.parametrize(
    ("age_ms", "sell_error"),
    [(4999, "FEE_UNAVAILABLE"), (5000, "STALE_QUOTE"), (5001, "STALE_QUOTE")],
)
def test_insufficient_buy_reserve_precedes_missing_pool_fee_and_age(age_ms, sell_error):
    transports = PublicTransports()
    transports.base_reserve = 249 * 10**8
    transports.now = transports.block_time + age_ms
    markets = provider(transports, pool_fee=None, missing_fee_for="dex")

    report = markets.inspect("dex")

    assert report["baseVolume"] == "250"
    assert report["sides"]["BUY"]["error"] == "SOURCE_UNAVAILABLE"
    assert report["sides"]["SELL"]["error"] == sell_error
    assert not report["sourceReady"]


@pytest.mark.parametrize("age_ms", [4999, 5000, 5001])
def test_buy_settlement_capacity_precedes_missing_fee_and_age(age_ms):
    transports = PublicTransports()
    transports.settlement_reserve = 2**112 - 1
    transports.now += age_ms
    batch = provider(transports, pool_fee=None, missing_fee_for="dex").collect()

    assert batch.calls[Direction.BUY_DEX, "BUY"].response["code"] == "SOURCE_UNAVAILABLE"
    sell = batch.calls[Direction.BUY_KUCOIN, "SELL"].response
    assert sell["code"] == ("FEE_UNAVAILABLE" if age_ms < 5000 else "STALE_QUOTE")


def test_configured_estimates_without_evidence_are_diagnostic_only():
    batch = provider(PublicTransports(), pool_fee=0, spot_fee=10, fee_lookup=None).collect()

    assert all(call.response["code"] == "FEE_UNAVAILABLE" for call in batch.calls.values())
    assert not batch.fee_deadlines
    for report in batch.observations.values():
        assert report["observedAt"] and report["availableDepth"]
        assert report["fees"]["basis"] == "CONFIGURED_ESTIMATE"
        assert all(side["grossPrice"] and "feeEvidence" not in side
                   for side in report["sides"].values())


@pytest.mark.parametrize("change", [
    {"market": "wrong/pool"}, {"source": "test/untrusted"}, {"side": "unknown"},
    {"fee_policy_id": "other/account"}, {"settlement_asset": "wrong/asset"},
    {"reference": ""}, {"reference": "mock/example"}, {"basis_points": True},
    {"basis_points": 10_000}, {"valid_until_ms": NOW}, {"observed_ms": NOW + 1},
])
def test_fee_evidence_must_match_the_binding_and_be_current(change):
    def lookup(req):
        return replace(fixture_fee_lookup(req), **change)

    batch = provider(PublicTransports(), fee_lookup=lookup).collect()
    assert all(call.response["code"] == "FEE_UNAVAILABLE" for call in batch.calls.values())


def test_one_unverified_side_does_not_change_the_other_quote():
    def lookup(req):
        if req["side"] == "SELL":
            return {"verified": True, "basis_points": 10}
        return fixture_fee_lookup(req)

    batch = provider(PublicTransports(), fee_lookup=lookup).collect()
    for direction in Direction:
        assert batch.calls[direction, "BUY"].response["status"] == "OK"
        assert batch.calls[direction, "SELL"].response["code"] == "FEE_UNAVAILABLE"


def test_fee_expiry_is_checked_at_publication_and_when_s2_reuses_the_batch():
    transports = PublicTransports()

    def short_lived(req):
        return replace(fixture_fee_lookup(req), valid_until_ms=NOW + 1)

    markets = provider(transports, fee_lookup=short_lived)
    batch = markets.collect()
    assert all(call.response["status"] == "OK" for call in batch.calls.values())
    transports.now += 1
    with markets.context(batch) as context:
        for call in batch.calls.values():
            assert validate(call, markets.relationship, context).code == "SOURCE_UNAVAILABLE"

    transports = PublicTransports()

    def slow_gas(*args):
        transports.now += 1
        return gas_rate(*args)

    batch = provider(transports, fee_lookup=short_lived, rate_lookup=slow_gas).collect()
    assert all(call.response["code"] == "FEE_UNAVAILABLE" for call in batch.calls.values())
    assert not batch.observations["dex"]["sourceReady"]


def test_both_directions_one_verified_snapshot_per_market_and_exact_costs():
    transports = PublicTransports()
    markets = provider(transports, pool_fee=30, spot_fee=None)
    batch = markets.collect()
    assert batch.provenance == "REAL"
    assert len(batch.calls) == 4
    assert transports.methods == [
        "eth_chainId", "eth_getBlockByNumber", *(["eth_call"] * 6), "eth_getBlockByNumber",
    ]
    assert batch.observations["dex"]["pool"] == POOL
    assert batch.observations["kucoin"]["transport"] == "rest"
    assert all(o["baseVolume"] == "250" and o["provenance"] == "REAL"
               for o in batch.observations.values())
    with markets.context(batch) as context:
        legs = {key: validate(call, markets.relationship, context).leg
                for key, call in batch.calls.items()}
    buy_dex, sell_dex = legs[Direction.BUY_DEX, "BUY"], legs[Direction.BUY_KUCOIN, "SELL"]
    q = 250 * 10**8
    buy_denominator = 9975 * (BASE - q)
    buy_gross = -(-(10000 * SETTLEMENT * q) // buy_denominator)
    sell_gross = 9975 * q * SETTLEMENT // (10000 * BASE + 9975 * q)
    assert buy_dex.gross_settlement.units == buy_gross
    assert buy_dex.settlement.units == buy_gross + 51
    assert sell_dex.gross_settlement.units == sell_gross
    assert sell_dex.settlement.units == sell_gross - 40
    assert buy_dex.costs[0].treatment == "EMBEDDED"
    assert buy_dex.costs[1].rate == batch.rates["test/rate/1"]
    assert legs[Direction.BUY_KUCOIN, "BUY"].settlement.units == 13_763_750
    assert legs[Direction.BUY_DEX, "SELL"].settlement.units == 13_486_500
    assert all(leg.base.units == q for leg in legs.values())
    assert all(side["feeEvidence"]["source"] == FEE_SOURCES[venue]
               for venue, report in batch.observations.items()
               for side in report["sides"].values())
    assert all(leg.epoch == batch.observations[venue]["connectionEpoch"]
               for venue, leg in (("dex", buy_dex), ("dex", sell_dex)))

    transports.now += 500
    with markets.context(batch) as context:
        assert validate(batch.calls[Direction.BUY_DEX, "BUY"], markets.relationship,
                        context).code == "FEE_UNAVAILABLE"


def test_insufficient_depth_and_expired_observation():
    transports = PublicTransports()
    markets = provider(transports, volume=252)
    report = markets.inspect("kucoin")
    assert not transports.methods
    assert report["baseVolume"] == "252"
    assert report["sides"]["BUY"]["error"] == "SOURCE_UNAVAILABLE"
    assert report["sides"]["SELL"]["valid"]
    transports.now += 5000
    report = markets.inspect("kucoin")
    assert report["sides"]["SELL"]["error"] == "STALE_QUOTE"
    assert not any(side["valid"] for side in report["sides"].values())


@pytest.mark.parametrize("defect", ["missing", "expired", "wrong-asset", "mock-ref"])
def test_gas_missing_or_invalid_fails_even_with_public_prices(defect):
    def lookup(*args):
        ref, rate = gas_rate(*args)
        if defect == "expired":
            rate = replace(rate, expires_ms=NOW)
        if defect == "wrong-asset":
            rate = replace(rate, settlement_id="kucoin/USDT")
        if defect == "mock-ref":
            ref = "mock/rate/1"
        return ref, rate

    markets = provider(PublicTransports(), rate_lookup=None if defect == "missing" else lookup)
    report = markets.inspect("dex")
    assert report["chainId"] == 295 and report["connected"]
    assert all(side["error"] == "FEE_UNAVAILABLE" and side["grossPrice"]
               for side in report["sides"].values())


def test_bad_chain_and_unknown_spot_fee_are_independent_failures():
    transports = PublicTransports()
    transports.chain = 1
    markets = provider(transports, spot_fee=None, missing_fee_for="kucoin")
    batch = markets.collect()
    assert transports.methods == ["eth_chainId"]
    assert "wrong Hedera chain ID" in batch.observations["dex"]["error"]
    assert batch.observations["kucoin"]["availableDepth"] == {"BUY": "251", "SELL": "255"}
    assert all(side["error"] == "FEE_UNAVAILABLE"
               for side in batch.observations["kucoin"]["sides"].values())


def test_conflicting_gas_rate_reference_invalidates_both_dex_quotes():
    def lookup(feed, snap, side, quantity):
        ref, rate = gas_rate(feed, snap, side, quantity)
        return ref, replace(rate, numerator=2) if side == "SELL" else rate

    markets = provider(PublicTransports(), rate_lookup=lookup)
    batch = markets.collect()
    assert not batch.rates
    assert all(side["error"] == "FEE_UNAVAILABLE"
               for side in batch.observations["dex"]["sides"].values())
