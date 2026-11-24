from dataclasses import replace

import pytest

from arb_multidex.connections.feed import MarketError
from arb_multidex.connections.http import TransportError
from arb_multidex.connections.mock import SAMPLE_MS, MockClock, mock_gas
from arb_multidex.connections.rpc import ManagedRPC
from arb_multidex.dex.saucerswap import FACTORY, PAIR, USDC, WHBAR, SaucerSwap
from arb_multidex.quotes import QuoteConfig, QuoteProducer
from arb_multidex.quotes.fees import GasPolicy


def words(*values):
    return "0x" + "".join(f"{value:064x}" for value in values)


class Relay:
    def __init__(self):
        self.calls = []
        self.chain = "0x127"
        self.returns = {
            (PAIR, "0x0dfe1681"): words(int(USDC, 16)),
            (PAIR, "0xd21220a7"): words(int(WHBAR, 16)),
            (PAIR, "0xc45a0155"): words(int(FACTORY, 16)),
            (PAIR, "0x0902f1ac"): words(198803970955, 379049637493682, 1795474043),
            (USDC, "0x313ce567"): words(6),
            (WHBAR, "0x313ce567"): words(8),
        }
        self.block = {
            "number": "0x61f13c6",
            "hash": "0x" + "ab" * 32,
            "timestamp": hex(SAMPLE_MS // 1000),
        }
        self.after = self.block
        self.fail_at = None

    def __call__(self, endpoint, *, method, body, timeout):
        name, params = body["method"], body["params"]
        self.calls.append((endpoint, name, params))
        if self.fail_at == len(self.calls):
            raise TransportError("timeout")
        if name == "eth_chainId":
            value = self.chain
        elif name == "eth_getBlockByNumber":
            value = self.block if params[0] == "latest" else self.after
        else:
            assert name == "eth_call"
            target = params[0]
            value = self.returns[(target["to"], target["data"])]
        return {"jsonrpc": "2.0", "id": body["id"], "result": value}


def reader(relay, retries=1):
    rpc = ManagedRPC(
        ["http://first.invalid", "http://next.invalid"],
        attempts=retries,
        http=relay,
        sleep=lambda _: None,
    )
    return SaucerSwap(rpc, clock=MockClock())


def test_read_verified_block_and_abi_at_single_height():
    relay = Relay()
    snap = reader(relay).read()
    assert (snap.base, snap.settlement, snap.last_update_s, snap.observed_ms) == (
        379049637493682,
        198803970955,
        1795474043,
        SAMPLE_MS,
    )
    assert [c[1] for c in relay.calls] == [
        "eth_chainId",
        "eth_getBlockByNumber",
        *(["eth_call"] * 6),
        "eth_getBlockByNumber",
    ]
    assert all(p[1] == "0x61f13c6" for _, method, p in relay.calls if method == "eth_call")
    assert relay.calls[-1][2] == ["0x61f13c6", False]


def test_wrong_chain_cannot_read_contracts():
    relay = Relay()
    relay.chain = "0x1"
    with pytest.raises(MarketError, match="wrong Hedera"):
        reader(relay, retries=2).read()
    assert [method for _, method, _ in relay.calls] == ["eth_chainId"] * 2


@pytest.mark.parametrize(
    "target,selector,bad",
    [
        (PAIR, "0x0dfe1681", words(int(WHBAR, 16))),
        (PAIR, "0xd21220a7", words(int(USDC, 16))),
        (PAIR, "0xc45a0155", words(1)),
        (WHBAR, "0x313ce567", words(6)),
        (USDC, "0x313ce567", words(8)),
        (PAIR, "0x0dfe1681", words(2**160 + int(USDC, 16))),
        (PAIR, "0x0902f1ac", "0x"),
        (PAIR, "0x0902f1ac", words(1000, 0, 1)),
        (PAIR, "0x0902f1ac", words(1, 2**112, 1)),
        (PAIR, "0x0902f1ac", words(1, 2, 2**32)),
    ],
)
def test_invalid_identity_order_scale_or_abi(target, selector, bad):
    relay = Relay()
    relay.returns[target, selector] = bad
    with pytest.raises(MarketError) as error:
        reader(relay).read()
    assert error.value.code == "INVALID_QUOTE"


@pytest.mark.parametrize(
    "change", [{"hash": "0x" + "cc" * 32}, {"number": "0x2"}, {"timestamp": "0x1"}]
)
def test_changed_pinned_block(change):
    relay = Relay()
    relay.after = relay.block | change
    with pytest.raises(MarketError, match="pinned block changed"):
        reader(relay).read()


def test_failover_restarts_snapshot_and_rotates_epoch():
    relay = Relay()
    feed = reader(relay, retries=2)
    feed.read()
    original_epoch = feed.epoch
    relay.fail_at = len(relay.calls) + 5
    assert feed.read().position == 102699974
    assert feed.epoch != original_epoch
    retried = [m for endpoint, m, _ in relay.calls if endpoint == "http://next.invalid"]
    assert retried == [
        "eth_chainId",
        "eth_getBlockByNumber",
        *(["eth_call"] * 6),
        "eth_getBlockByNumber",
    ]


@pytest.mark.parametrize(
    "reply,code",
    [
        ({"error": {"code": -32000}}, "SOURCE_UNAVAILABLE"),
        ({"id": True, "result": "0x127"}, "INVALID_QUOTE"),
        ({"result": None}, "SOURCE_UNAVAILABLE"),
    ],
)
def test_bad_rpc_envelope(reply, code):
    feed = reader(lambda url, **kw: {"jsonrpc": "2.0", "id": kw["body"]["id"], **reply})
    with pytest.raises(MarketError) as error:
        feed.read()
    assert error.value.code == code


def test_rpc_to_quote_failover_requires_a_new_valid_block():
    relay = Relay()
    feed = reader(relay, retries=2)
    p = QuoteProducer(
        feed, QuoteConfig("demo", "r1", "verified-gas", 10**8, 5000), GasPolicy("fixture")
    )
    req = p.request("one", "BUY")
    assert p.quote(req)["code"] == "FEE_UNAVAILABLE"

    p = QuoteProducer(
        feed, replace(p.config, fee_policy_id="mock-gas"), GasPolicy("mock/rate", mock_gas)
    )
    first = p.quote(p.request("two", "SELL"))
    assert first["grossSettlementUnits"] == "52290"
    relay.fail_at = len(relay.calls) + 3
    assert p.quote(p.request("three", "SELL"))["code"] == "INVALID_QUOTE"
    feed.clock.now_ms += 1000
    relay.block.update(
        number="0x61f13c7", hash="0x" + "ef" * 32, timestamp=hex(feed.clock.now_ms // 1000)
    )
    last = p.quote(p.request("four", "BUY"))
    assert (
        last["grossSettlementUnits"] == "52606"
        and last["connectionEpoch"] != first["connectionEpoch"]
    )
