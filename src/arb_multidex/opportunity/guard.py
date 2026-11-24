"""Atomic S2 admission. S3 explicitly resolves a request after reconciliation."""

import hashlib
import json
import sqlite3
from dataclasses import asdict, dataclass, fields
from threading import Lock

from .models import (
    AcceptedCost,
    Amount,
    Direction,
    ExecutionRequest,
    Leg,
    Market,
    Rate,
    SeparateCost,
    token,
    valid_scale,
)


def execution_id(relationship, direction, buy, sell):
    # Producers can issue fresh request IDs for the same two market snapshots.
    identity = [
        "SIMULATION",
        relationship.relationship_id,
        relationship.mapping_revision,
        relationship.fee_policy_id,
        direction.value,
        [
            (leg.market.source_id, leg.epoch, leg.observed_ms, leg.base.units, leg.base.scale)
            for leg in (buy, sell)
        ],
    ]
    return hashlib.sha256(json.dumps(identity, separators=(",", ":")).encode()).hexdigest()


def _payload(request):
    # Canonical JSON keeps arbitrary-precision atoms and distinguishes bool from int.
    return json.dumps(
        {"version": 1, "request": asdict(request)},
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


class AdmissionEvidenceError(ValueError):
    """A pending claim has unavailable or invalid evidence. Hold for reconciliation."""

    def __init__(self, execution_id, reason):
        self.execution_id = execution_id
        super().__init__(f"Invalid admission evidence for {execution_id}: {reason}")


@dataclass(frozen=True)
class LegacyAdmission:
    """Positive identification of an unresolved claim from an ID-only journal."""

    execution_id: str


def _require(condition, reason):
    if not condition:
        raise ValueError(reason)


def _fields(record, model):
    # Dataclass defaults (e.g. SIMULATION) must never fill gaps in persisted data.
    _require(
        type(record) is dict and record.keys() == {f.name for f in fields(model)},
        f"invalid {model.__name__} fields",
    )
    return record


def _atoms(value, minimum=0):
    return type(value) is int and minimum <= value < 10**78


def _amount(record):
    return Amount(**_fields(record, Amount))


def _read_cost(record, market, now):
    _require(type(record) is dict, "invalid cost")
    if record.get("treatment") == "EMBEDDED":
        cost = AcceptedCost(**_fields(record, AcceptedCost))
    else:
        _fields(record, SeparateCost)
        _require(record["treatment"] == "SEPARATE", "invalid cost treatment")
        record["native"] = _amount(record["native"])
        raw_rate = record["rate"]
        if raw_rate is not None:
            record["rate"] = Rate(**_fields(raw_rate, Rate))
        cost = SeparateCost(**record)
        _require(
            token(cost.asset_id)
            and cost.asset_id != market.base_id
            and _atoms(cost.native.units)
            and _atoms(cost.numerator, 1)
            and _atoms(cost.denominator, 1),
            "invalid separate cost",
        )
        if cost.asset_id == market.settlement_id:
            _require(
                cost.native.scale == market.settlement_scale
                and cost.numerator == cost.denominator
                and cost.rate_ref is None
                and cost.rate is None,
                "invalid settlement conversion",
            )
        else:
            rate = cost.rate
            _require(
                token(cost.rate_ref) and rate is not None and token(rate.source),
                "missing foreign rate identity",
            )
            _require(
                valid_scale(rate.scale)
                and valid_scale(rate.settlement_scale)
                and (rate.asset_id, rate.scale) == (cost.asset_id, cost.native.scale)
                and (rate.settlement_id, rate.settlement_scale)
                == (market.settlement_id, market.settlement_scale)
                and _atoms(rate.numerator, 1)
                and _atoms(rate.denominator, 1)
                and (rate.numerator, rate.denominator) == (cost.numerator, cost.denominator),
                "invalid foreign rate terms",
            )
            _require(
                type(rate.observed_ms) is int
                and type(rate.expires_ms) is int
                and rate.observed_ms <= now < rate.expires_ms,
                "invalid rate observation window",
            )
        converted = (cost.native.units * cost.numerator + cost.denominator - 1) // cost.denominator
        _require(converted == cost.settlement_units, "separate cost does not reconcile")
    _require(
        token(cost.cost_id) and _atoms(cost.settlement_units), "invalid cost identity or amount"
    )
    return cost


def _read_leg(record, side, now):
    _fields(record, Leg)
    market = Market(**_fields(record["market"], Market))
    _require(
        all(v is None or token(v) for v in (market.network, market.pool, market.symbol)),
        "invalid market identity",
    )
    record["market"] = market
    for field in ("base", "settlement", "gross_settlement"):
        amount = _amount(record[field])
        scale = market.base_scale if field == "base" else market.settlement_scale
        _require(_atoms(amount.units, 1) and amount.scale == scale, f"invalid leg {field}")
        record[field] = amount
    _require(type(record["costs"]) is list and len(record["costs"]) <= 32, "invalid cost array")
    record["costs"] = tuple(_read_cost(c, market, now) for c in record["costs"])
    leg = Leg(**record)
    _require(
        leg.mode == "SIMULATION"
        and leg.side == side
        and token(leg.request_id)
        and token(leg.epoch),
        "invalid quote identity",
    )
    _require(len({c.cost_id for c in leg.costs}) == len(leg.costs), "duplicate cost ID")
    separate = [c for c in leg.costs if isinstance(c, SeparateCost)]
    charge = sum(c.settlement_units for c in separate)
    expected = leg.gross_settlement.units + (charge if side == "BUY" else -charge)
    _require(leg.settlement.units == expected, "gross and final settlement do not reconcile")
    _require(
        all(type(t) is int for t in (leg.observed_ms, leg.expires_ms, leg.quote_expires_ms)),
        "invalid quote timestamps",
    )
    # SIM-01 observations cover Gregorian years 0001 through 9999 in UTC.
    _require(
        -62135596800000 <= leg.observed_ms <= 253402300799999, "quote observation outside calendar"
    )
    expiries = [leg.quote_expires_ms, *(c.rate.expires_ms for c in separate if c.rate is not None)]
    _require(
        leg.observed_ms <= now < leg.expires_ms == min(expiries), "invalid quote validity window"
    )
    return leg


def _read_request(payload, claim_id):
    admission = json.loads(payload)
    _require(
        type(admission) is dict
        and admission.keys() == {"version", "request"}
        and type(admission["version"]) is int
        and admission["version"] == 1,
        "invalid S2 admission schema",
    )
    record = _fields(admission["request"], ExecutionRequest)
    now = record["evaluated_ms"]
    _require(type(now) is int and type(record["expires_ms"]) is int, "invalid decision timestamps")
    record["direction"] = Direction(record["direction"])
    buy = record["buy"] = _read_leg(record["buy"], "BUY", now)
    sell = record["sell"] = _read_leg(record["sell"], "SELL", now)
    for field in ("net_profit", "threshold"):
        record[field] = _amount(record[field])
    req = ExecutionRequest(**record)
    _require(
        req.mode == "SIMULATION"
        and req.status == "ACCEPTED"
        and all(token(v) for v in (req.relationship_id, req.mapping_revision, req.fee_policy_id)),
        "invalid admission identity",
    )
    dex, spot = (
        (buy.market, sell.market)
        if req.direction == Direction.BUY_DEX
        else (sell.market, buy.market)
    )
    _require(
        token(dex.network)
        and token(dex.pool)
        and token(spot.symbol)
        and dex.source_id != spot.source_id
        and buy.request_id != sell.request_id,
        "invalid market or leg pairing",
    )
    observations = {}
    for cost in (*buy.costs, *sell.costs):
        if isinstance(cost, SeparateCost) and cost.rate is not None:
            previous = observations.setdefault(cost.rate_ref, cost.rate)
            _require(previous == cost.rate, "conflicting observations for one rate reference")
    scale = max(buy.base.scale, sell.base.scale)
    _require(buy.base.at(scale) == sell.base.at(scale), "unequal base quantities")
    scale = max(buy.settlement.scale, sell.settlement.scale, req.threshold.scale)
    profit = sell.settlement.at(scale) - buy.settlement.at(scale)
    _require(
        req.net_profit == Amount(profit, scale) and 0 <= req.threshold.at(scale) <= profit,
        "invalid admitted profit or threshold",
    )
    _require(req.expires_ms == min(buy.expires_ms, sell.expires_ms), "invalid decision expiry")
    _require(
        req.execution_id == claim_id == execution_id(req, req.direction, buy, sell),
        "execution identity differs from admitted legs",
    )
    # The writer produces a single canonical encoding. This also detects duplicate JSON keys.
    _require(_payload(req) == payload, "noncanonical admission payload")
    return req


def _digest(payload):
    return hashlib.sha256(payload).hexdigest()


class ExecutionGuard:
    """Share one journal across workers. Use a file path for restart protection."""

    def __init__(self, path):
        self._lock = Lock()
        self._db = sqlite3.connect(path, check_same_thread=False)
        with self._db:
            # Serialize schema inspection, provenance classification and writer startup.
            self._db.execute("BEGIN IMMEDIATE")
            self._encoding = self._db.execute("PRAGMA encoding").fetchone()[0]
            self._db.execute("""
                CREATE TABLE IF NOT EXISTS opportunity_requests (
                    execution_id TEXT PRIMARY KEY, relationship_id TEXT NOT NULL,
                    network TEXT NOT NULL, pending INTEGER NOT NULL,
                    buy_request_id TEXT NOT NULL, sell_request_id TEXT NOT NULL,
                    evidence_digest TEXT,
                    UNIQUE(relationship_id, buy_request_id, sell_request_id)
                )
            """)
            for column in ("network", "relationship_id"):
                self._db.execute(f"""
                    CREATE UNIQUE INDEX IF NOT EXISTS pending_{column}
                    ON opportunity_requests({column}) WHERE pending = 1
                """)

            columns = self._db.execute("PRAGMA table_info(opportunity_requests)")
            if "evidence_digest" not in {row[1] for row in columns}:
                self._db.execute("ALTER TABLE opportunity_requests ADD COLUMN evidence_digest TEXT")
                has_evidence_table = self._db.execute(
                    "SELECT 1 FROM sqlite_master WHERE type='table' AND name='opportunity_admissions'"
                ).fetchone()
                if has_evidence_table:
                    # Revision 1 stored no provenance on the claim. Missing rows stay unknown.
                    self._db.create_function("admission_sha256", 1, _digest)
                    self._db.execute("""
                        UPDATE opportunity_requests SET evidence_digest = (
                            SELECT admission_sha256(CAST(payload AS BLOB))
                            FROM opportunity_admissions AS a
                            WHERE a.execution_id = opportunity_requests.execution_id
                              AND typeof(a.payload) = 'text'
                        )
                    """)
                else:
                    self._db.execute("UPDATE opportunity_requests SET evidence_digest = 'legacy'")

            # A separate insert-only table also works with existing ID-only journals.
            self._db.execute("""
                CREATE TABLE IF NOT EXISTS opportunity_admissions (
                    execution_id TEXT PRIMARY KEY REFERENCES opportunity_requests(execution_id),
                    payload TEXT NOT NULL
                )
            """)

    def _admit(self, request, network):
        """S2 detector write path. Commit validated evidence and its claim together."""
        payload = _payload(request)
        with self._lock, self._db:
            cursor = self._db.execute(
                "INSERT INTO opportunity_requests (execution_id, relationship_id, network, pending, "
                "buy_request_id, sell_request_id, evidence_digest) VALUES (?, ?, ?, 1, ?, ?, ?) "
                "ON CONFLICT DO NOTHING",
                (
                    request.execution_id,
                    request.relationship_id,
                    network,
                    request.buy.request_id,
                    request.sell.request_id,
                    _digest(payload.encode(self._encoding)),
                ),
            )
            if cursor.rowcount == 0:
                return False
            self._db.execute(
                "INSERT INTO opportunity_admissions (execution_id, payload) VALUES (?, ?)",
                (request.execution_id, payload),
            )
            return True

    def read_pending(self, execution_id: str) -> ExecutionRequest | LegacyAdmission | None:
        """Recover a verified request or proven legacy claim. Invalid evidence raises AdmissionEvidenceError."""
        try:
            with self._lock:
                row = self._db.execute(
                    "SELECT r.evidence_digest, a.execution_id, typeof(a.payload), CAST(a.payload AS BLOB), "
                    "r.relationship_id, r.network, r.buy_request_id, r.sell_request_id "
                    "FROM opportunity_requests r LEFT JOIN opportunity_admissions a USING (execution_id) "
                    "WHERE r.execution_id = ? AND r.pending = 1",
                    (execution_id,),
                ).fetchone()
        except sqlite3.Error as exc:
            raise AdmissionEvidenceError(execution_id, "journal read unavailable") from exc
        if row is None:
            return None
        digest, evidence_id, storage, payload, relationship, network, buy_id, sell_id = row
        if digest == "legacy" and evidence_id is None:
            return LegacyAdmission(execution_id)
        try:
            _require(storage == "text" and payload is not None, "missing or non-text evidence")
            _require(digest == _digest(payload), "claim and evidence digest differ")
            request = _read_request(payload.decode(self._encoding), execution_id)
            dex = request.buy if request.direction == Direction.BUY_DEX else request.sell
            _require(
                (relationship, network, buy_id, sell_id)
                == (
                    request.relationship_id,
                    dex.market.network,
                    request.buy.request_id,
                    request.sell.request_id,
                ),
                "claim columns differ from request",
            )
        except (ValueError, TypeError, KeyError, RecursionError, OverflowError) as exc:
            raise AdmissionEvidenceError(execution_id, str(exc)) from exc
        return request

    def verify_pending(self, request: ExecutionRequest) -> ExecutionRequest | None:
        """Validate stored evidence first, then compare the supplied request without consuming the claim."""
        if type(request) is not ExecutionRequest or type(request.execution_id) is not str:
            return None
        stored = self.read_pending(request.execution_id)
        if not isinstance(stored, ExecutionRequest):
            return None
        try:
            supplied = _payload(request)
        except (TypeError, ValueError, RecursionError):
            return None
        if (
            type(request.direction) is Direction
            and request == stored
            and supplied == _payload(stored)
        ):
            return stored
        return None

    def pending(self):
        """Unresolved IDs (including after restart) for S3 reconciliation."""
        with self._lock:
            return tuple(
                row[0]
                for row in self._db.execute(
                    "SELECT execution_id FROM opportunity_requests WHERE pending = 1 ORDER BY execution_id"
                )
            )

    def resolve(self, execution_id):
        """Release only this ID's claim. Its snapshot identity remains spent."""
        with self._lock, self._db:
            return (
                self._db.execute(
                    "UPDATE opportunity_requests SET pending = 0 WHERE execution_id = ? AND pending = 1",
                    (execution_id,),
                ).rowcount
                == 1
            )

    def close(self):
        with self._lock:
            self._db.close()
