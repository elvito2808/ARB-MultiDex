import re
from dataclasses import dataclass, field

from arb_multidex.connections.feed import Feed, invalid, unavailable
from arb_multidex.connections.rpc import quantity
from arb_multidex.contracts import PAIR_ABI, TOKEN_ABI
from arb_multidex.quotes.fees import GasPolicy, fee_error

PAIR = "0xdb34c1ef944883f0e5a2fc18b6c1978b088bd31d"
FACTORY = "0x0000000000000000000000000000000000103780"
WHBAR = "0x0000000000000000000000000000000000163b5a"
USDC = "0x000000000000000000000000000000000006f89a"
SOURCE = f"hedera/295/saucerswap-v1/{PAIR}"


@dataclass(frozen=True)
class Reserves:
    position: int  # Pinned block height
    block_hash: str
    observed_ms: int  # RPC block time, floor to integer seconds
    settlement: int
    base: int
    last_update_s: int  # Pair's uint32 timestamp, independent of block time
    receipt_ms: int = field(compare=False)


def pool_amount(side, q, base, settlement, *, fee_bps=30):
    """Price q WHBAR atoms. BUY uses the invariant minimum (integer ceiling)."""
    if (
        side not in ("BUY", "SELL")
        or type(q) is not int
        or not 0 < q < 2**256
        or type(fee_bps) is not int
        or not 0 <= fee_bps < 10000
    ):
        raise invalid("invalid base quantity, side or fee")
    if any(type(n) is not int or not 0 < n < 2**112 for n in (base, settlement)):
        raise invalid("invalid pool reserves")
    effective = 10000 - fee_bps
    if side == "BUY":
        if q >= base:
            raise unavailable("insufficient base liquidity")
        numerator = 10000 * settlement * q
        denominator = effective * (base - q)
        required = -(-numerator // denominator)
        if settlement + required >= 2**112:
            raise unavailable("settlement exceeds pair reserve capacity")
        return required
    if base + q >= 2**112:
        raise unavailable("base exceeds pair reserve capacity")
    output = effective * q * settlement // (10000 * base + effective * q)
    if not output:
        raise invalid("SELL rounds to zero")
    return output


def _header(call, tag):
    block = call("eth_getBlockByNumber", [tag, False])
    if not isinstance(block, dict):
        raise invalid("malformed block")
    number = quantity(block.get("number"))
    digest = block.get("hash")
    if not isinstance(digest, str) or not re.fullmatch(r"0x[0-9a-fA-F]{64}", digest):
        raise invalid("malformed block hash")
    return number, digest.lower(), quantity(block.get("timestamp")) * 1000


class SaucerSwap(Feed):
    policy_type = GasPolicy

    def __init__(self, rpc, *, pool=PAIR, fee_bps=30, **clock_options):
        if not isinstance(pool, str) or not re.fullmatch(r"0x[0-9a-fA-F]{40}", pool):
            raise ValueError("configure a SaucerSwap V1 pool address")
        if fee_bps is not None and (type(fee_bps) is not int or not 0 <= fee_bps < 10000):
            raise ValueError("invalid pool fee")
        self.pool, self.fee_bps = pool.lower(), fee_bps
        super().__init__(f"hedera/295/saucerswap-v1/{self.pool}", WHBAR, USDC, 8, 6,
                         **clock_options)
        self.rpc = rpc

    def read(self):
        with self.lock:
            return self.rpc.snapshot(self, self._read)

    def _read(self, call):
        height, digest, observed = _header(call, "latest")
        tag = hex(height)

        def get(target, abi):
            raw = call("eth_call", [{"to": target, "data": abi.selector}, tag])
            try:
                return abi.decode(raw)
            except ValueError as exc:
                raise invalid(str(exc)) from exc

        for name, expected in (("token0", USDC), ("token1", WHBAR), ("factory", FACTORY)):
            if get(self.pool, PAIR_ABI[name]) != (expected,):
                raise invalid(f"unexpected pair {name}")
        for token, decimals in ((USDC, 6), (WHBAR, 8)):
            if get(token, TOKEN_ABI["decimals"]) != (decimals,):
                raise invalid("unexpected token decimals")
        settlement, base, last_update = get(self.pool, PAIR_ABI["getReserves"])
        if not settlement or not base:
            raise invalid("empty pool reserves")
        if _header(call, tag) != (height, digest, observed):
            raise invalid("pinned block changed during snapshot")
        return Reserves(height, digest, observed, settlement, base, last_update, self.now())

    def price(self, snap, side, q):
        if self.fee_bps is None:
            if side == "BUY" and q >= snap.base:
                raise unavailable("insufficient base liquidity")
            raise fee_error()
        gross = pool_amount(side, q, snap.base, snap.settlement, fee_bps=self.fee_bps)
        without_fee = pool_amount(side, q, snap.base, snap.settlement, fee_bps=0)
        embedded = {
            "costId": "pool-fee",
            "treatment": "EMBEDDED",
            "settlementCostUnits": str(abs(gross - without_fee)),
        }
        return gross, [embedded]
