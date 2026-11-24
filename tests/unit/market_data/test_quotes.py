import json
from dataclasses import replace
from threading import Event, Thread

import pytest

from arb_multidex.connections.mock import SAMPLE_MS, MockClock, MockKuCoin, MockSaucerSwap, mock_gas
from arb_multidex.quotes import QuoteConfig, QuoteProducer
from arb_multidex.quotes.fees import GasPolicy, SpotFeePolicy


def producer():
    clock = MockClock()
    config = QuoteConfig("sim", "r1", "test-fees", 10**8, 5000)
    return QuoteProducer(MockSaucerSwap(clock), config, GasPolicy("mock/rate", mock_gas))


def quote(p, side="BUY", rid="q1"):
    return p.quote(p.request(rid, side))


@pytest.mark.parametrize(
    "side,gross,final", [("BUY", "52606", "52609"), ("SELL", "52290", "52287")]
)
def test_complete_sim01_envelope(side, gross, final):
    p = producer()
    req = p.request("sample", side)
    result = p.quote(json.dumps(req).encode())
    assert result == {
        **req,
        "status": "OK",
        "settlementUnits": final,
        "grossSettlementUnits": gross,
        "connectionEpoch": "mock-1",
        "observedAt": "2026-11-23T22:49:17.000Z",
        "includedCosts": [
            {"costId": "pool-fee", "treatment": "EMBEDDED", "settlementCostUnits": "157"},
            {
                "costId": "gas",
                "treatment": "SEPARATE",
                "settlementCostUnits": "3",
                "assetId": "hedera/295/HBAR",
                "units": "5",
                "scale": 8,
                "rateNumerator": "1",
                "rateDenominator": "2",
                "rateRef": f"mock/rate/{SAMPLE_MS}",
            },
        ],
    }
    assert p.quote({**req, "requestId": "again"}) == {**result, "requestId": "again"}


@pytest.mark.parametrize(
    "fields",
    [
        {"mode": "LIVE"},
        {"baseUnits": "0100000000"},
        {"baseUnits": 100000000},
        {"baseUnits": "100000001"},
        {"side": "buy"},
        {"baseScale": 8.0},
        {"settlementScale": False},
        {"sourceId": "other"},
        {"feePolicyId": "old"},
        {"mappingRevision": "old"},
        {"relationshipId": "other"},
        {"baseAssetId": "HBAR"},
        {"settlementAssetId": "USDT"},
        {"requestId": "space id"},
        {"extra": 1},
        {"baseUnits": "１00000000"},
    ],
)
def test_first_error_is_correlated_and_reads_nothing(fields):
    p = producer()
    req = p.request("request", "SELL") | fields
    assert p.quote(req) == {
        "status": "ERROR",
        "requestId": None if "requestId" in fields else "request",
        "sourceId": req["sourceId"],
        "code": "INVALID_QUOTE",
    }
    assert p.feed.epoch is None


@pytest.mark.parametrize("raw", [b"\xff", b"[1]", b'{"x":1,"x":2}', b" " * 16385, b"{}{}"])
def test_wire_rejections(raw):
    assert producer().quote(raw) == {
        "status": "ERROR",
        "code": "INVALID_QUOTE",
        "requestId": None,
        "sourceId": None,
    }


@pytest.mark.parametrize(
    "age,expected",
    [(-1, "INVALID_QUOTE"), (4999, "OK"), (5000, "STALE_QUOTE"), (5001, "STALE_QUOTE")],
)
def test_observation_age_is_block_time(age, expected):
    p = producer()
    p.feed.clock.now_ms += age
    result = quote(p)
    assert result.get("code", result["status"]) == expected


@pytest.mark.parametrize(
    "change",
    [
        {"numerator": 0},
        {"denominator": 0},
        {"tinybars": -1},
        {"tinybars": True},
        {"valid_until_ms": SAMPLE_MS},
        {"observed_ms": SAMPLE_MS + 1},
        {"rate_source": "unapproved"},
        {"rate_ref": "invalid ref"},
        {"denominator": 10**78},
    ],
)
def test_unverified_required_gas_prevents_success(change):
    p = producer()
    p.fees = GasPolicy("mock/rate", lambda *args: replace(mock_gas(*args), **change))
    assert quote(p)["code"] == "FEE_UNAVAILABLE"
    assert p.feed.last is None


def test_known_zero_is_reported_and_unknown_is_never_zero():
    p = producer()
    p.fees = GasPolicy("mock/rate", lambda *args: replace(mock_gas(*args), tinybars=0))
    result = quote(p)
    assert result["includedCosts"][1]["settlementCostUnits"] == "0"
    assert result["settlementUnits"] == result["grossSettlementUnits"]
    p.fees = GasPolicy("mock/rate")
    assert quote(p)["code"] == "FEE_UNAVAILABLE"
    p.feed.clock.now_ms += 5000
    assert quote(p)["code"] == "STALE_QUOTE"


def test_final_validation_after_slow_estimator_and_clock_outage():
    p = producer()

    def estimate(*args):
        p.feed.clock.now_ms += 5000
        return mock_gas(*args)

    p.fees = GasPolicy("mock/rate", estimate)
    assert quote(p)["code"] == "STALE_QUOTE"
    p.feed.clock = lambda: None
    assert quote(p)["code"] == "SOURCE_UNAVAILABLE"


def test_recovery_needs_new_observation_and_full_fee_validation():
    p = producer()
    assert quote(p)["status"] == "OK"
    orig = p.feed.last
    p.feed.snapshot = replace(orig, base=orig.base + 1)
    assert quote(p)["code"] == "INVALID_QUOTE"
    p.feed.snapshot = replace(orig, position=orig.position - 1)
    assert quote(p)["code"] == "INVALID_QUOTE"
    p.feed.disconnect()
    assert p.feed.epoch is None
    p.feed.snapshot = orig
    assert quote(p)["code"] == "INVALID_QUOTE"
    p.feed.clock.now_ms += 1000
    p.feed.snapshot = replace(orig, position=orig.position + 1, observed_ms=SAMPLE_MS + 1000)
    p.fees = GasPolicy("mock/rate")
    assert quote(p)["code"] == "FEE_UNAVAILABLE"
    assert p.feed.recovering and p.feed.last is orig
    p.fees = GasPolicy("mock/rate", mock_gas)
    assert quote(p)["connectionEpoch"] == "mock-2"
    assert not p.feed.recovering


def test_regressed_snapshot_and_snapshot_before_recovery():
    p = producer()
    quote(p)
    p.feed.clock.now_ms += 2000
    p.feed.snapshot = replace(p.feed.snapshot, observed_ms=SAMPLE_MS - 1)
    assert quote(p)["code"] == "INVALID_QUOTE"
    p.feed.disconnect()
    p.feed.snapshot = replace(p.feed.snapshot, observed_ms=SAMPLE_MS + 1000, position=102699975)
    assert quote(p)["code"] == "INVALID_QUOTE"


def test_reentrant_disconnect_discards_inflight_quote():
    p = producer()

    def estimate(*args):
        p.feed.disconnect()
        return mock_gas(*args)

    p.fees = GasPolicy("mock/rate", estimate)
    assert quote(p)["code"] == "INVALID_QUOTE"
    assert p.feed.last is None


def test_disconnect_cancels_inflight_fee_lookup():
    p = producer()
    entered, release = Event(), Event()
    results = {}

    def estimate(*args):
        entered.set()
        assert release.wait(2)
        return mock_gas(*args)

    p.fees = GasPolicy("mock/rate", estimate)
    first = Thread(target=lambda: results.update(q=quote(p)))
    first.start()
    assert entered.wait(2)
    disconnect = Thread(target=p.feed.disconnect)
    disconnect.start()
    disconnect.join(2)
    assert p.feed.epoch is None
    release.set()
    first.join(2)
    assert results["q"]["requestId"] == "q1" and results["q"]["code"] == "INVALID_QUOTE"
    assert p.feed.epoch is None
    assert p.feed.last is None


def test_other_market_stays_available_and_fee_type_is_bound():
    dex = producer()
    spot = MockKuCoin("TEST-USDT", 8, 6, dex.feed.clock)
    with pytest.raises(TypeError):
        QuoteProducer(dex.feed, dex.config, SpotFeePolicy(0))
    p = QuoteProducer(spot, dex.config, SpotFeePolicy(10))
    dex.feed.disconnect()
    assert quote(p, "SELL")["settlementUnits"] == "51948"


def test_error_precedence_zero_net_and_insufficient_depth_before_age():
    p = producer()
    p.fees = GasPolicy(
        "mock/rate",
        lambda *args: replace(mock_gas(*args), tinybars=104580, valid_until_ms=SAMPLE_MS + 10000),
    )
    p.feed.clock.now_ms += 5000
    assert quote(p, "SELL")["code"] == "INVALID_QUOTE"
    p.feed.snapshot = replace(p.feed.snapshot, base=10**8)
    assert quote(p)["code"] == "SOURCE_UNAVAILABLE"
