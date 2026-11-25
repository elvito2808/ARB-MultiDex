"""Retry complete read transactions on configured endpoints only."""

import itertools
import re
import time
from functools import partial
from urllib.parse import urlsplit

from .feed import MarketError, invalid, unavailable
from .http import TransportError, request_json


def quantity(raw):
    if not isinstance(raw, str) or not re.fullmatch(r"0x(?:0|[1-9a-fA-F][0-9a-fA-F]{0,63})", raw):
        raise invalid("malformed RPC quantity")
    return int(raw, 16)


class ManagedRPC:
    def __init__(self, endpoints, *, attempts=2, timeout=5, http=request_json, sleep=time.sleep):
        if isinstance(endpoints, (str, bytes)):
            raise TypeError("configure RPC endpoints as a list")
        self.endpoints = tuple(endpoints)
        if not self.endpoints or any(
            urlsplit(url).scheme not in ("https", "http") or not urlsplit(url).hostname
            for url in self.endpoints
        ):
            raise ValueError("configure at least one HTTP(S) RPC endpoint")
        if type(attempts) is not int or not 1 <= attempts <= 10 or not 0 < timeout <= 60:
            raise ValueError("invalid RPC retry or timeout bound")
        self.attempts, self.timeout = attempts, timeout
        self.http, self.sleep = http, sleep
        self.index = 0
        self.ids = itertools.count(1)
        self.last_error = None

    def _call(self, endpoint, method, params):
        call_id = next(self.ids)
        response = self.http(
            endpoint,
            method="POST",
            timeout=self.timeout,
            body={"jsonrpc": "2.0", "id": call_id, "method": method, "params": params},
        )
        if (
            not isinstance(response, dict)
            or response.get("jsonrpc") != "2.0"
            or type(response.get("id")) is not int
            or response["id"] != call_id
        ):
            raise invalid("uncorrelated RPC response")
        if "error" in response:
            error = response["error"]
            message = str(error.get("message", "")).lower() if isinstance(error, dict) else ""
            category = "revert" if "revert" in message else "provider error"
            if "block" in message or "histor" in message:
                category = "historical state unavailable"
            raise unavailable(f"{method}: {category}")
        if response.get("result") is None:
            raise unavailable(f"missing RPC result for {method}")
        return response["result"]

    def snapshot(self, feed, read):
        for attempt in range(self.attempts):
            index = self.index
            call = partial(self._call, self.endpoints[index])
            try:
                if quantity(call("eth_chainId", [])) != 295:
                    raise invalid("wrong Hedera chain ID")
                feed.connected(index)
                result = read(call)
                self.last_error = None
                return result
            except (MarketError, TransportError) as exc:
                self.last_error = str(exc)
                feed.disconnect()
                self.index = (index + 1) % len(self.endpoints)
                if attempt + 1 == self.attempts:
                    if isinstance(exc, MarketError):
                        raise
                    raise unavailable(self.last_error) from exc
                self.sleep(min(0.05 * 2**attempt, 1))
