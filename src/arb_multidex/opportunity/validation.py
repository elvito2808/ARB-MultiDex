"""Validate the original invocation, the SIM-01 envelope and current eligibility."""

import json
import re
from dataclasses import dataclass
from datetime import datetime

from .models import Amount, Leg, Rate, SourceState, token, valid_scale

ERROR_ORDER = ("INVALID_QUOTE", "SOURCE_UNAVAILABLE", "STALE_QUOTE", "FEE_UNAVAILABLE")
IDS = (
    "requestId",
    "relationshipId",
    "mappingRevision",
    "feePolicyId",
    "sourceId",
    "baseAssetId",
    "settlementAssetId",
)
REQUEST = {*IDS, "mode", "side", "baseUnits", "baseScale", "settlementScale"}
SUCCESS = REQUEST | {
    "status",
    "settlementUnits",
    "grossSettlementUnits",
    "includedCosts",
    "observedAt",
    "connectionEpoch",
}
COMMON_COST = {"costId", "settlementCostUnits", "treatment"}
SEPARATE_COST = COMMON_COST | {"assetId", "units", "scale", "rateNumerator", "rateDenominator"}
EPOCH = datetime(1970, 1, 1)


class Rejection(Exception):
    def __init__(self, code="INVALID_QUOTE"):
        super().__init__(code)
        self.code = code


def unique_fields(pairs):
    fields = dict(pairs)
    if len(fields) != len(pairs):
        raise Rejection()
    return fields


def unsigned_number(raw):
    # JSON integers are used only for scales. Preserve invalid lexical forms
    # as an invalid scale (in particular -0, which int() would normalize).
    return int(raw) if not raw.startswith("-") and len(raw) <= 2 else None


def invalid_constant(raw):
    raise Rejection()


def message(raw):
    if isinstance(raw, dict):
        raw = json.dumps(raw, separators=(",", ":"), allow_nan=False)
    if isinstance(raw, str):
        raw = raw.encode("utf-8")
    if not isinstance(raw, bytes) or len(raw) > 16384:
        raise Rejection()
    value = json.loads(
        raw.decode("utf-8"),
        object_pairs_hook=unique_fields,
        parse_int=unsigned_number,
        parse_constant=invalid_constant,
    )
    if not isinstance(value, dict):
        raise Rejection()
    return value


def atoms(raw, *, allow_zero=False):
    if allow_zero and raw == "0":
        return 0
    if not isinstance(raw, str) or not re.fullmatch(r"[1-9][0-9]{0,77}", raw):
        raise ValueError("invalid units")
    return int(raw)


def observation(raw):
    if not isinstance(raw, str) or not re.fullmatch(
        r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}\.[0-9]{3}Z", raw
    ):
        raise Rejection()
    elapsed = datetime.fromisoformat(raw[:-1]) - EPOCH
    return (elapsed.days * 86400 + elapsed.seconds) * 1000 + elapsed.microseconds // 1000


def check_cost_fields(records):
    """Malformed wire data precedes availability, age and fee-policy errors."""
    if not isinstance(records, list) or len(records) > 32:
        raise Rejection()
    for record in records:
        if not isinstance(record, dict) or not token(record.get("costId")):
            raise Rejection()
        atoms(record.get("settlementCostUnits"), allow_zero=True)
        if record.get("treatment") == "EMBEDDED":
            if record.keys() != COMMON_COST:
                raise Rejection()
        elif record.get("treatment") == "SEPARATE":
            if record.keys() - (SEPARATE_COST | {"rateRef"}):
                raise Rejection()
            if "units" in record:
                atoms(record["units"], allow_zero=True)
            if "scale" in record and not valid_scale(record["scale"]):
                raise Rejection()
        else:
            raise Rejection()


def separate_cost(record, rule, market, context):
    foreign = rule.asset_id != market.settlement_id
    fields = SEPARATE_COST | {"rateRef"} if foreign else SEPARATE_COST
    if (
        record.keys() != fields
        or record["assetId"] != rule.asset_id
        or not valid_scale(record["scale"])
        or record["scale"] != rule.scale
    ):
        raise ValueError("wrong cost asset or fields")
    units = atoms(record["units"], allow_zero=True)
    num, den = atoms(record["rateNumerator"]), atoms(record["rateDenominator"])
    expires = None
    if foreign:
        ref = record["rateRef"]
        rate = context.rates.get(ref) if token(ref) else None
        if (
            not isinstance(rate, Rate)
            or rate.source != rule.rate_source
            or (rate.asset_id, rate.scale) != (rule.asset_id, rule.scale)
            or (rate.settlement_id, rate.settlement_scale)
            != (market.settlement_id, market.settlement_scale)
            or any(
                type(v) is not int
                for v in (
                    rate.scale,
                    rate.settlement_scale,
                    rate.numerator,
                    rate.denominator,
                    rate.observed_ms,
                    rate.expires_ms,
                )
            )
            or (num, den) != (rate.numerator, rate.denominator)
            or type(context.now_ms) is not int
            or not rate.observed_ms <= context.now_ms < rate.expires_ms
        ):
            raise ValueError("unverified conversion")
        expires = rate.expires_ms
    elif num != den:
        raise ValueError("non-unit settlement rate")
    return (units * num + den - 1) // den, expires


def costs(records, required, market, context):
    if not isinstance(records, list) or len(records) != len(required):
        raise ValueError("incomplete cost coverage")
    remaining = {rule.cost_id: rule for rule in required}
    separate = 0
    expiries = []
    for record in records:
        rule = remaining.pop(record["costId"])
        amount = atoms(record["settlementCostUnits"], allow_zero=True)
        if record["treatment"] != rule.treatment:
            raise ValueError("wrong treatment")
        if rule.treatment == "SEPARATE":
            converted, expiry = separate_cost(record, rule, market, context)
            if converted != amount:
                raise ValueError("conversion does not reconcile")
            separate += amount
            if expiry is not None:
                expiries.append(expiry)
    return separate, expiries


@dataclass(frozen=True)
class Checked:
    request_id: str | None
    code: str | None
    leg: Leg | None = None
    bound: bool = False


def validate(call, relationship, context):
    """Keep attribution in the caller's slot even if the response cannot be parsed."""
    if call is None:
        return Checked(None, "SOURCE_UNAVAILABLE")
    rid = None
    try:
        req = message(call.request)
        rid = req.get("requestId") if token(req.get("requestId")) else None
        if (
            req.keys() != REQUEST
            or not all(token(req[k]) for k in IDS)
            or req["mode"] != "SIMULATION"
            or req["side"] not in ("BUY", "SELL")
            or not valid_scale(req["baseScale"])
            or not valid_scale(req["settlementScale"])
        ):
            raise Rejection()
        quantity = atoms(req["baseUnits"])
        reply = message(call.response)
        if reply.get("status") == "ERROR":
            if (
                reply.keys() != {"status", "requestId", "sourceId", "code"}
                or reply["requestId"] != rid
                or reply["sourceId"] != req["sourceId"]
                or reply["code"] not in ERROR_ORDER
            ):
                raise Rejection()
            raise Rejection(reply["code"])
        if (
            reply.keys() != SUCCESS
            or reply["status"] != "OK"
            or not valid_scale(reply["baseScale"])
            or not valid_scale(reply["settlementScale"])
            or any(reply[k] != v for k, v in req.items())
            or not token(reply["connectionEpoch"])
        ):
            raise Rejection()
        check_cost_fields(reply["includedCosts"])
        observed = observation(reply["observedAt"])
        now = context.now_ms
        gross, final = atoms(reply["grossSettlementUnits"]), atoms(reply["settlementUnits"])
        if type(now) is int and observed > now:
            raise Rejection()
        binding = next(
            (
                b
                for b in (relationship.dex, relationship.kucoin)
                if b.market.source_id == req["sourceId"]
            ),
            None,
        )
        state = context.sources.get(req["sourceId"])
        if isinstance(state, SourceState):
            if state.epoch is not None and state.epoch != reply["connectionEpoch"]:
                raise Rejection()
            if type(state.earliest_ms) is int and observed < state.earliest_ms:
                raise Rejection()
        if binding is None:
            return Checked(rid, None)  # No active mapping to this source.
        market = binding.market
        if (req["baseAssetId"], req["baseScale"]) != (market.base_id, market.base_scale) or (
            req["settlementAssetId"],
            req["settlementScale"],
        ) != (market.settlement_id, market.settlement_scale):
            raise Rejection()
        bound = (req["relationshipId"], req["mappingRevision"], req["feePolicyId"]) == (
            relationship.relationship_id,
            relationship.mapping_revision,
            relationship.fee_policy_id,
        )
        fee_ok = True
        expiries = [observed + binding.max_age_ms]
        if bound:
            required = binding.buy_costs if req["side"] == "BUY" else binding.sell_costs
            try:
                charge, rates_expire = costs(reply["includedCosts"], required, market, context)
            except (ValueError, KeyError, TypeError):
                fee_ok = False
            else:
                if final != (gross + charge if req["side"] == "BUY" else gross - charge):
                    raise Rejection()
                expiries.extend(rates_expire)
        if (
            type(now) is not int
            or not isinstance(state, SourceState)
            or state.ready is not True
            or not token(state.epoch)
            or type(state.earliest_ms) is not int
        ):
            raise Rejection("SOURCE_UNAVAILABLE")
        if now >= expiries[0]:
            raise Rejection("STALE_QUOTE")
        if not fee_ok:
            raise Rejection("FEE_UNAVAILABLE")
        leg = Leg(
            req["mode"],
            market,
            rid,
            req["side"],
            Amount(quantity, market.base_scale),
            Amount(final, market.settlement_scale),
            state.epoch,
            observed,
            min(expiries),
        )
        return Checked(rid, None, leg, bound)
    except Rejection as exc:
        return Checked(rid, exc.code)
    except (AttributeError, ValueError, TypeError, RecursionError, OverflowError):
        return Checked(rid, "INVALID_QUOTE")
