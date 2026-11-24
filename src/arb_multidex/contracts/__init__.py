"""Verified SaucerSwap V1 read interfaces. All methods have zero inputs."""

import re
from dataclasses import dataclass


@dataclass(frozen=True)
class Getter:
    selector: str
    outputs: tuple[str, ...]

    def decode(self, data):
        size = 64 * len(self.outputs)
        if not isinstance(data, str) or not re.fullmatch(rf"0x[0-9a-fA-F]{{{size}}}", data):
            raise ValueError("incorrect ABI return length or encoding")
        values = []
        for i, kind in enumerate(self.outputs):
            word = int(data[2 + 64 * i : 66 + 64 * i], 16)
            bits = 160 if kind == "address" else int(kind[4:])
            if word.bit_length() > bits:
                raise ValueError(f"nonzero ABI padding for {kind}")
            values.append(f"0x{word:040x}" if kind == "address" else word)
        return tuple(values)


PAIR_ABI = {
    "token0": Getter("0x0dfe1681", ("address",)),
    "token1": Getter("0xd21220a7", ("address",)),
    "factory": Getter("0xc45a0155", ("address",)),
    "getReserves": Getter("0x0902f1ac", ("uint112", "uint112", "uint32")),
}
TOKEN_ABI = {"decimals": Getter("0x313ce567", ("uint8",))}
