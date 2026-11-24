"""Shared Quote wire contract.

Field names intentionally match docs/design/quote-contract.md exactly.
This module defines the stable Python typing boundary; runtime validation
belongs to the producer and consumer modules.
"""
from __future__ import annotations

from typing import Literal, NotRequired, TypedDict

QuoteMode = Literal["SIMULATION", "LIVE"]
QuoteSide = Literal["BUY", "SELL"]
QuoteErrorCode = Literal[
    "INVALID_QUOTE",
    "SOURCE_UNAVAILABLE",
    "STALE_QUOTE",
    "FEE_UNAVAILABLE",
]


class QuoteRequest(TypedDict):
    requestId: str
    relationshipId: str
    mappingRevision: str
    feePolicyId: str
    mode: QuoteMode
    sourceId: str
    side: QuoteSide
    baseAssetId: str
    settlementAssetId: str
    baseUnits: str
    baseScale: int
    settlementScale: int


class QuoteCost(TypedDict):
    costId: str
    settlementCostUnits: str
    treatment: Literal["EMBEDDED", "SEPARATE"]
    assetId: NotRequired[str]
    units: NotRequired[str]
    scale: NotRequired[int]
    rateNumerator: NotRequired[str]
    rateDenominator: NotRequired[str]
    rateRef: NotRequired[str]


class QuoteSuccess(TypedDict):
    status: Literal["OK"]
    requestId: str
    relationshipId: str
    mappingRevision: str
    feePolicyId: str
    mode: QuoteMode
    sourceId: str
    side: QuoteSide
    baseAssetId: str
    settlementAssetId: str
    baseUnits: str
    baseScale: int
    settlementScale: int
    grossSettlementUnits: str
    settlementUnits: str
    includedCosts: list[QuoteCost]
    observedAt: str
    connectionEpoch: str


class QuoteError(TypedDict):
    status: Literal["ERROR"]
    requestId: str | None
    sourceId: str
    code: QuoteErrorCode


QuoteResponse = QuoteSuccess | QuoteError
