"""One producer binds a fixed-volume relationship to a shared market feed."""

import json
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from arb_multidex.connections.feed import MarketError, invalid
from arb_multidex.connections.http import TransportError

from .fees import MAX_UNITS, fee_error, identifier

WIRE_LIMIT = 16384
UNIX_EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)


def unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON key")
        result[key] = value
    return result


def unsigned_integer(value):
    # Preserve the other fields for error correlation, including for an invalid -0 scale.
    return None if value.startswith("-") or len(value) > 2 else int(value)


def parse_request(raw):
    if isinstance(raw, dict):
        raw = json.dumps(raw, allow_nan=False)
    if isinstance(raw, str):
        raw = raw.encode("utf-8")
    if not isinstance(raw, bytes) or len(raw) > WIRE_LIMIT:
        raise ValueError("invalid JSON message")
    value = json.loads(
        raw.decode("utf-8"), object_pairs_hook=unique_object, parse_int=unsigned_integer
    )
    if not isinstance(value, dict):
        raise TypeError("expected one JSON object")
    return value


@dataclass(frozen=True)
class QuoteConfig:
    relationship_id: str
    mapping_revision: str
    fee_policy_id: str
    base_units: int
    max_age_ms: int

    def __post_init__(self):
        if (
            not all(
                identifier(x)
                for x in (self.relationship_id, self.mapping_revision, self.fee_policy_id)
            )
            or type(self.base_units) is not int
            or not 0 < self.base_units <= MAX_UNITS
            or type(self.max_age_ms) is not int
            or self.max_age_ms <= 0
        ):
            raise ValueError("invalid quote configuration")


class QuoteProducer:
    def __init__(self, feed, config, fees):
        if not isinstance(fees, feed.policy_type):
            raise TypeError("fee policy does not cover this market")
        self.feed, self.config, self.fees = feed, config, fees

    def request(self, request_id, side):
        """Build the canonical request for this binding. The caller owns request IDs."""
        feed, cfg = self.feed, self.config
        return {
            "requestId": request_id,
            "relationshipId": cfg.relationship_id,
            "mappingRevision": cfg.mapping_revision,
            "feePolicyId": cfg.fee_policy_id,
            "mode": "SIMULATION",
            "sourceId": feed.source_id,
            "side": side,
            "baseAssetId": feed.base_id,
            "settlementAssetId": feed.settlement_id,
            "baseUnits": str(cfg.base_units),
            "baseScale": feed.base_scale,
            "settlementScale": feed.settlement_scale,
        }

    def quote(self, raw, *, observation=None, on_gas=None):
        """Accept a UTF-8 JSON message or its decoded dict. Return one SIM-01 envelope."""
        rid = source = None
        try:
            req = parse_request(raw)
            rid = req.get("requestId") if identifier(req.get("requestId")) else None
            source = req.get("sourceId") if identifier(req.get("sourceId")) else None
            if (
                rid is None
                or req.get("side") not in ("BUY", "SELL")
                or type(req.get("baseScale")) is not int
                or type(req.get("settlementScale")) is not int
                or req != self.request(rid, req["side"])
            ):
                raise invalid("request differs from market binding")
            with self.feed.lock:
                return self._produce(req, observation, on_gas)
        except MarketError as exc:
            code = exc.code
        except (ValueError, TypeError, RecursionError, OverflowError):
            code = "INVALID_QUOTE"
        return {"status": "ERROR", "requestId": rid, "sourceId": source, "code": code}

    def capture(self):
        """Capture once for a group of requests sharing this feed's lock."""
        with self.feed.lock:
            snap = self.feed.read()
            with self.feed.state_lock:
                return snap, self.feed.epoch

    def _produce(self, req, observation, on_gas):
        feed, cfg = self.feed, self.config
        snap, generation = observation if observation is not None else self.capture()
        with feed.state_lock:
            if generation != feed.epoch:
                raise invalid("connection changed before pricing")
            feed.validate_observation(snap, feed.now())
        observed = UNIX_EPOCH + timedelta(milliseconds=snap.observed_ms)
        side, q = req["side"], cfg.base_units
        gross, embedded = feed.price(snap, side, q)
        if type(gross) is not int or not 0 < gross <= MAX_UNITS:
            raise invalid("unrepresentable gross")
        try:
            evidence = self.fees.evidence(feed, snap, side, q)
        except (OSError, TransportError, MarketError, ValueError, TypeError, KeyError):
            evidence = None
        with feed.state_lock:
            result = self._publish(req, snap, observed, generation, gross, embedded, evidence)
            if on_gas is not None:
                on_gas(evidence)
            return result

    def _publish(self, req, snap, observed, generation, gross, embedded, evidence):
        feed = self.feed
        now = feed.now()
        if generation != feed.epoch:
            raise invalid("connection changed before publication")
        feed.validate_observation(snap, now)
        stale = now - snap.observed_ms >= self.config.max_age_ms
        try:
            separate = self.fees.costs(feed, gross, now, evidence)
        except MarketError:
            if stale:
                raise MarketError("STALE_QUOTE", "expired observation")
            raise fee_error()
        charge = sum(int(cost["settlementCostUnits"]) for cost in separate)
        final = gross + charge if req["side"] == "BUY" else gross - charge
        if not 0 < final <= MAX_UNITS:
            raise invalid("unrepresentable final settlement")
        if stale:
            raise MarketError("STALE_QUOTE", "expired observation")
        result = {
            **req,
            "status": "OK",
            "grossSettlementUnits": str(gross),
            "settlementUnits": str(final),
            "includedCosts": embedded + separate,
            "observedAt": observed.isoformat(timespec="milliseconds").replace("+00:00", "Z"),
            "connectionEpoch": generation,
        }
        feed.accept(snap)
        return result
