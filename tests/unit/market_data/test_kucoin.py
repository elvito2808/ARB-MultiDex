import json
from dataclasses import replace

import pytest

from arb_multidex.connections.http import TransportError
from arb_multidex.connections.mock import SAMPLE_MS, MockClock
from arb_multidex.kucoin import KuCoin
from arb_multidex.kucoin.market import top
from arb_multidex.quotes import QuoteConfig, QuoteProducer
from arb_multidex.quotes.fees import SpotFeePolicy


class PublicAPI:
    def __init__(self):
        self.calls = []
        self.time = SAMPLE_MS
        self.rest_down = False

    def __call__(self, url, *, method, timeout):
        self.calls.append((method, url))
        if url.endswith("/bullet-public"):
            assert method == "POST"
            data = {
                "token": "public-token",
                "instanceServers": [
                    {
                        "endpoint": "wss://fixture.invalid/path",
                        "protocol": "websocket",
                        "pingInterval": 20000,
                        "pingTimeout": 10000,
                    }
                ],
            }
        else:
            assert method == "GET" and url.endswith("level1?symbol=XYZ-USDT")
            if self.rest_down:
                raise TransportError("unavailable")
            data = {
                "time": self.time,
                "bestAsk": "2.1",
                "bestAskSize": "4",
                "bestBid": "2.0",
                "bestBidSize": "3",
            }
        return {"code": "200000", "data": data}


class Socket:
    def __init__(self, topic):
        self.topic = topic
        self.frames = [{"type": "welcome"}, {"type": "ack"}]
        self.sent = []
        self.closed = False

    def snapshot(self, stamp=SAMPLE_MS):
        self.frames.append(
            {
                "topic": self.topic,
                "type": "message",
                "subject": "level2",
                "data": {"asks": [["2.1", "4"]], "bids": [["2.0", "3"]], "timestamp": stamp},
            }
        )

    def send(self, frame):
        self.sent.append(json.loads(frame))

    def recv(self, timeout):
        assert timeout >= 0
        if timeout == 0 and not self.frames:
            raise TimeoutError
        if not self.frames:
            raise EOFError
        return json.dumps(self.frames.pop(0))

    def close(self):
        self.closed = True


def market():
    clock, api = MockClock(), PublicAPI()
    ws = Socket("/spotMarket/level2Depth5:XYZ-USDT")
    feed = KuCoin(
        "XYZ-USDT",
        8,
        6,
        http=api,
        socket=lambda url, timeout: ws,
        clock=clock,
        monotonic=lambda: (clock.now_ms - SAMPLE_MS) / 1000,
    )
    p = QuoteProducer(feed, QuoteConfig("xyz", "r1", "fees", 10**8, 5000), SpotFeePolicy(10))
    return p, clock, api, ws


def test_public_protocol_subscription_and_bid_ask_fee():
    p, _clock, api, ws = market()
    for side, final in (("BUY", "2102100"), ("SELL", "1998000")):
        ws.snapshot()
        req = p.request(side, side)
        res = p.quote(req)
        assert res["settlementUnits"] == final
        assert res["sourceId"] == "kucoin/spot/XYZ-USDT"
        assert res["includedCosts"][0]["assetId"] == "kucoin/USDT"
    assert api.calls == [("POST", "https://api.kucoin.com/api/v1/bullet-public")]
    assert ws.sent[0]["topic"] == ws.topic and not ws.sent[0]["privateChannel"]


def test_stream_loss_rest_cooldown_and_return_to_stream_rotate_epochs():
    p, clock, api, ws = market()
    req = p.request("first", "BUY")
    ws.snapshot()
    first = p.quote(req)
    clock.now_ms += 100
    api.time = clock.now_ms
    fallback = p.quote(req | {"requestId": "fallback"})
    assert ws.closed and p.feed.route == "rest"
    assert fallback["connectionEpoch"] != first["connectionEpoch"]
    assert p.quote(req)["connectionEpoch"] == fallback["connectionEpoch"]
    assert len([u for _, u in api.calls if "bullet" in u]) == 1
    clock.now_ms += 10000
    ws.snapshot(clock.now_ms)
    resumed = p.quote(req)
    assert p.feed.route == "websocket"
    assert resumed["connectionEpoch"] not in {first["connectionEpoch"], fallback["connectionEpoch"]}


def test_rest_replay_after_socket_loss_and_full_outage():
    p, clock, api, ws = market()
    req = p.request("k", "BUY")
    ws.snapshot()
    assert p.quote(req)["status"] == "OK"
    clock.now_ms += 1
    assert p.quote(req)["code"] == "INVALID_QUOTE"
    api.rest_down = True
    assert p.quote(req)["code"] == "SOURCE_UNAVAILABLE"
    assert p.feed.epoch is None


def test_ping_and_pong_and_unrelated_topic():
    p, clock, _api, ws = market()
    ws.snapshot()
    p.feed.read()
    clock.now_ms += 10000
    ws.frames.append({"type": "message", "topic": "other"})
    ws.snapshot(clock.now_ms)
    p.feed.read()
    ping = ws.sent[-1]
    assert ping["type"] == "ping"
    ws.frames.append({"type": "pong", "id": ping["id"]})
    ws.snapshot(clock.now_ms)
    p.feed.read()
    assert p.feed.pong_due is None and p.feed.route == "websocket"


@pytest.mark.parametrize(
    "side,units,fee,expected",
    [
        ("BUY", 400000001, 10, "SOURCE_UNAVAILABLE"),
        ("SELL", 300000001, 10, "SOURCE_UNAVAILABLE"),
        ("BUY", 1, 10, "INVALID_QUOTE"),
        ("BUY", 100000000, None, "FEE_UNAVAILABLE"),
        ("SELL", 100000000, 0, "OK"),
    ],
)
def test_depth_exact_representability_and_fee(side, units, fee, expected):
    p, _, _, ws = market()
    p = QuoteProducer(p.feed, replace(p.config, base_units=units), SpotFeePolicy(fee))
    ws.snapshot()
    res = p.quote(p.request("k", side))
    assert res.get("code", res["status"]) == expected
    if fee == 0:
        assert res["includedCosts"][0]["units"] == "0"


@pytest.mark.parametrize(
    "bid,ask", [("2.1", "2"), ("2", "2"), (2.0, "3"), ("NaN", "3"), ("1e-2", "3"), ("0", "3")]
)
def test_invalid_book(bid, ask):
    from arb_multidex.connections.feed import MarketError

    with pytest.raises(MarketError):
        top(SAMPLE_MS, bid, "1", ask, "1")


@pytest.mark.parametrize("symbol", [None, "", "XYZ", "XYZ-USDT,ABC-USDT"])
def test_public_symbol_is_explicit(symbol):
    with pytest.raises(ValueError):
        KuCoin(symbol, 8, 6)


def test_slow_poller_drains_buffered_snapshots_without_refreshing_their_age():
    p, clock, _api, ws = market()
    ws.snapshot()
    clock.now_ms += 5000
    ws.snapshot(clock.now_ms - 100)
    result = p.quote(p.request("latest", "BUY"))
    assert result["status"] == "OK" and p.feed.route == "websocket"
    assert p.feed.last.observed_ms == clock.now_ms - 100


def test_stale_websocket_uses_valid_rest_and_rotates_epoch():
    p, clock, api, ws = market()
    p.feed.stream_max_age_ms = 5000
    ws.snapshot()
    first = p.quote(p.request("first", "BUY"))
    clock.now_ms += 5000
    ws.snapshot()
    api.time = clock.now_ms
    second = p.quote(p.request("rest", "BUY"))
    assert second["status"] == "OK" and p.feed.route == "rest"
    assert ws.closed and second["connectionEpoch"] != first["connectionEpoch"]
