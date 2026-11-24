import pytest

from arb_multidex.connections.feed import MarketError
from arb_multidex.dex import pool_amount


@pytest.mark.parametrize("side,expected", [("BUY", 52606), ("SELL", 52290)])
def test_discovery_amounts(side, expected):
    assert pool_amount(side, 10**8, 379049637493682, 198803970955) == expected


@pytest.mark.parametrize(
    "q,b,s", [(1, 1001, 1994), (123, 10000, 23456), (2**100, 2**111 - 1, 2**110)]
)
def test_minimal_input_and_maximal_output_at_full_precision(q, b, s):
    buy = pool_amount("BUY", q, b, s)
    assert (buy - 1) * 997 * (b - q) < 1000 * s * q <= buy * 997 * (b - q)
    sell = pool_amount("SELL", q, b, s)
    assert sell * (1000 * b + 997 * q) <= 997 * q * s < (sell + 1) * (1000 * b + 997 * q)


def test_exact_division_uses_invariant_minimum():
    assert pool_amount("BUY", 1, 1001, 997) == 1


@pytest.mark.parametrize(
    "side,q,b,s,code",
    [
        ("BUY", 1, 1, 2, "SOURCE_UNAVAILABLE"),
        ("BUY", 3, 2, 2, "SOURCE_UNAVAILABLE"),
        ("SELL", 1, 1001, 997, "INVALID_QUOTE"),
        ("SELL", 0, 1, 2, "INVALID_QUOTE"),
        ("SELL", True, 1, 2, "INVALID_QUOTE"),
        ("SELL", 1, 0, 2, "INVALID_QUOTE"),
        ("BUY", 2**256, 2, 2, "INVALID_QUOTE"),
    ],
)
def test_invalid_inputs(side, q, b, s, code):
    with pytest.raises(MarketError) as error:
        pool_amount(side, q, b, s)
    assert error.value.code == code
