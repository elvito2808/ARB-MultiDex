"""Use complete public depth snapshots. Never reconstruct a book from deltas."""

import json
import re
import time
from dataclasses import dataclass
from fractions import Fraction
from urllib.parse import urlencode, urlsplit
from uuid import uuid4

from arb_multidex.connections.feed import Feed, MarketError, invalid, unavailable
from arb_multidex.connections.http import TransportError, request_json
from arb_multidex.quotes.fees import SpotFeePolicy

STREAM_ERRORS = (
    TransportError,
    OSError,
    ImportError,
    MarketError,
    ValueError,
    KeyError,
    IndexError,
    TypeError,
    EOFError,
)
try:
    from websockets.exceptions import WebSocketException
except ImportError:
    pass  # REST and mock feeds work with the standard library.
else:
    STREAM_ERRORS += (WebSocketException,)


def open_socket(uri, timeout):
    from websockets.sync.client import connect

    return connect(uri, open_timeout=timeout, close_timeout=1, ping_interval=None, max_size=65536)


def decimal_fraction(value):
    if (
        not isinstance(value, str)
        or len(value) > 78
        or not re.fullmatch(r"[0-9]+(?:\.[0-9]+)?", value)
    ):
        raise invalid("invalid KuCoin decimal")
    result = Fraction(value)
    if result <= 0:
        raise invalid("nonpositive price or size")
    return result


@dataclass(frozen=True)
class Top:
    observed_ms: int
    bid: Fraction
    bid_size: Fraction
    ask: Fraction
    ask_size: Fraction

    @property
    def position(self):
        return self.observed_ms


def top(observed, bid, bid_size, ask, ask_size):
    if type(observed) is not int or observed <= 0:
        raise invalid("missing exchange timestamp")
    book = Top(observed, *map(decimal_fraction, (bid, bid_size, ask, ask_size)))
    if book.bid >= book.ask:
        raise invalid("locked or crossed book")
    return book


class KuCoin(Feed):
    policy_type = SpotFeePolicy

    def __init__(
        self,
        symbol,
        base_scale,
        settlement_scale,
        *,
        http=request_json,
        socket=open_socket,
        timeout=5,
        retry_seconds=10,
        stream_max_age_ms=None,
        monotonic=time.monotonic,
        **clock_options,
    ):
        if not isinstance(symbol, str) or not re.fullmatch(
            r"[A-Z0-9]{1,32}-[A-Z0-9]{1,32}", symbol
        ):
            raise ValueError("configure an explicit KuCoin BASE-QUOTE symbol")
        if any(type(s) is not int or not 0 <= s <= 38 for s in (base_scale, settlement_scale)):
            raise ValueError("invalid market scales")
        if not 0 < timeout <= 60 or not 0 < retry_seconds <= 300:
            raise ValueError("invalid connection timeouts")
        base, settlement = symbol.split("-")
        super().__init__(
            f"kucoin/spot/{symbol}",
            f"kucoin/{base}",
            f"kucoin/{settlement}",
            base_scale,
            settlement_scale,
            **clock_options,
        )
        if stream_max_age_ms is not None and (
            type(stream_max_age_ms) is not int or stream_max_age_ms <= 0
        ):
            raise ValueError("invalid WebSocket freshness limit")
        self.stream_max_age_ms = stream_max_age_ms
        self.symbol = symbol
        self.topic = f"/spotMarket/level2Depth5:{symbol}"
        self.http, self.socket = http, socket
        self.monotonic, self.timeout, self.retry_seconds = monotonic, timeout, retry_seconds
        self.ws = None
        self.retry_at = 0
        self.last_error = None

    def _api(self, path, method="GET"):
        reply = self.http("https://api.kucoin.com" + path, method=method, timeout=self.timeout)
        if not isinstance(reply, dict) or reply.get("code") != "200000":
            raise unavailable("KuCoin public API unavailable")
        return reply["data"]

    def _subscribe(self):
        bullet = self._api("/api/v1/bullet-public", method="POST")
        server = next(
            (
                s
                for s in bullet["instanceServers"]
                if isinstance(s, dict)
                and s.get("protocol") == "websocket"
                and urlsplit(s["endpoint"]).scheme == "wss"
            ),
            None,
        )
        if not server or not isinstance(bullet["token"], str) or not bullet["token"]:
            raise unavailable("missing public WebSocket endpoint or token")
        interval, timeout = server["pingInterval"], server["pingTimeout"]
        if any(type(v) is not int or not 0 < v <= 300000 for v in (interval, timeout)):
            raise unavailable("invalid server heartbeat")
        self.ping_interval, self.ping_timeout = interval / 2000, timeout / 1000
        self.ping_due = self.monotonic() + self.ping_interval
        self.pong_due = None
        args = urlencode({"token": bullet["token"], "connectId": uuid4().hex})
        sep = "&" if "?" in server["endpoint"] else "?"
        self.ws = self.socket(server["endpoint"] + sep + args, self.timeout)
        self.connected("websocket")
        self.ws.send(
            json.dumps(
                {
                    "id": uuid4().hex,
                    "type": "subscribe",
                    "topic": self.topic,
                    "privateChannel": False,
                    "response": True,
                }
            )
        )

    def _stream(self):
        if self.ws is None:
            self._subscribe()
        deadline = self.monotonic() + self.timeout
        latest = None
        drained = 0
        while True:
            now = self.monotonic()
            if latest is not None and (drained >= 128 or now >= deadline):
                return latest
            if now >= deadline or (self.pong_due is not None and now >= self.pong_due):
                raise unavailable("KuCoin stream timed out")
            if self.pong_due is None and now >= self.ping_due:
                self.ping_id = uuid4().hex
                self.ws.send(json.dumps({"id": self.ping_id, "type": "ping"}))
                self.pong_due = now + self.ping_timeout
            wake = min(deadline, self.ping_due if self.pong_due is None else self.pong_due)
            try:
                frame = json.loads(self.ws.recv(timeout=0 if latest is not None else wake - now))
            except TimeoutError:
                if latest is not None:
                    return latest
                continue
            drained += 1
            kind = frame["type"]
            if kind == "pong" and self.pong_due is not None and frame.get("id") == self.ping_id:
                self.pong_due = None
                self.ping_due = self.monotonic() + self.ping_interval
            if kind == "error":
                raise unavailable("KuCoin subscription rejected")
            if kind == "message" and frame["topic"] == self.topic and frame["subject"] == "level2":
                data = frame["data"]
                bids, asks = data["bids"], data["asks"]
                if not bids or not asks:
                    raise unavailable("empty top of book")
                if any(
                    not isinstance(level, list) or len(level) != 2 for level in (bids[0], asks[0])
                ):
                    raise invalid("malformed top level")
                update = top(data["timestamp"], *bids[0], *asks[0])
                if latest is not None and (
                    update.observed_ms < latest.observed_ms
                    or (update.observed_ms == latest.observed_ms and update != latest)
                ):
                    raise invalid("conflicting or regressed buffered book")
                latest = update

    def disconnect(self):
        super().disconnect()
        with self.lock:
            # A pending read may have entered REST recovery while close was waiting.
            super().disconnect()
            if self.ws is not None:
                try:
                    self.ws.close()
                except STREAM_ERRORS:
                    pass
                finally:
                    self.ws = None

    def read(self):
        with self.lock:
            if self.monotonic() >= self.retry_at:
                try:
                    book = self._stream()
                    if self.stream_max_age_ms is not None:
                        with self.state_lock:
                            now = self.now()
                            self.validate_observation(book, now)
                            if now - book.observed_ms >= self.stream_max_age_ms:
                                raise MarketError("STALE_QUOTE", "expired WebSocket observation")
                    self.last_error = None
                    return book
                except STREAM_ERRORS as exc:
                    self.last_error = type(exc).__name__
                    if self.ws is not None:
                        self.disconnect()
                    self.retry_at = self.monotonic() + self.retry_seconds
            try:
                self.connected("rest")
                data = self._api(
                    "/api/v1/market/orderbook/level1?" + urlencode({"symbol": self.symbol})
                )
                return top(
                    data["time"],
                    data["bestBid"],
                    data["bestBidSize"],
                    data["bestAsk"],
                    data["bestAskSize"],
                )
            except MarketError:
                self.disconnect()
                raise
            except (TransportError, OSError, ValueError, KeyError, TypeError) as exc:
                self.disconnect()
                raise unavailable("KuCoin public REST snapshot unavailable") from exc

    def price(self, snap, side, q):
        price, size = (snap.ask, snap.ask_size) if side == "BUY" else (snap.bid, snap.bid_size)
        volume = Fraction(q, 10**self.base_scale)
        if size < volume:
            raise unavailable("insufficient top-level depth")
        amount = price * volume * 10**self.settlement_scale
        if amount.denominator != 1:
            raise invalid("settlement amount is not representable")
        return amount.numerator, []
