"""Atomic S2 admission. S3 explicitly resolves a request after reconciliation."""

import hashlib
import json
import sqlite3
from dataclasses import asdict
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


def _read_cost(record):
    if record["treatment"] == "EMBEDDED":
        return AcceptedCost(**record)
    if record["treatment"] != "SEPARATE":
        raise ValueError("unknown admitted cost treatment")
    record["native"] = Amount(**record["native"])
    if record["rate"] is not None:
        record["rate"] = Rate(**record["rate"])
    return SeparateCost(**record)


def _read_leg(record):
    record["market"] = Market(**record["market"])
    for field in ("base", "settlement", "gross_settlement"):
        record[field] = Amount(**record[field])
    record["costs"] = tuple(_read_cost(c) for c in record["costs"])
    return Leg(**record)


def _read_request(payload, execution_id):
    admission = json.loads(payload)
    if type(admission["version"]) is not int or admission["version"] != 1:
        raise ValueError("unsupported S2 admission version")
    record = admission["request"]
    if record["execution_id"] != execution_id:
        raise ValueError("S2 admission ID differs from its journal key")
    record["direction"] = Direction(record["direction"])
    for field in ("buy", "sell"):
        record[field] = _read_leg(record[field])
    for field in ("net_profit", "threshold"):
        record[field] = Amount(**record[field])
    return ExecutionRequest(**record)


class ExecutionGuard:
    """Share one journal across workers. Use a file path for restart protection."""

    def __init__(self, path):
        self._lock = Lock()
        self._db = sqlite3.connect(path, check_same_thread=False)
        with self._db:
            self._db.execute("""
                CREATE TABLE IF NOT EXISTS opportunity_requests (
                    execution_id TEXT PRIMARY KEY, relationship_id TEXT NOT NULL,
                    network TEXT NOT NULL, pending INTEGER NOT NULL,
                    buy_request_id TEXT NOT NULL, sell_request_id TEXT NOT NULL,
                    UNIQUE(relationship_id, buy_request_id, sell_request_id)
                )
            """)
            for column in ("network", "relationship_id"):
                self._db.execute(f"""
                    CREATE UNIQUE INDEX IF NOT EXISTS pending_{column}
                    ON opportunity_requests({column}) WHERE pending = 1
                """)

            # A separate insert-only table also works with existing ID-only journals.
            self._db.execute("""
                CREATE TABLE IF NOT EXISTS opportunity_admissions (
                    execution_id TEXT PRIMARY KEY REFERENCES opportunity_requests(execution_id),
                    payload TEXT NOT NULL
                )
            """)

    def _admit(self, request, network):
        """S2 detector write path. Commit validated evidence and its claim together."""
        with self._lock, self._db:
            cursor = self._db.execute(
                "INSERT INTO opportunity_requests VALUES (?, ?, ?, 1, ?, ?) ON CONFLICT DO NOTHING",
                (
                    request.execution_id,
                    request.relationship_id,
                    network,
                    request.buy.request_id,
                    request.sell.request_id,
                ),
            )
            if cursor.rowcount == 0:
                return False
            self._db.execute(
                "INSERT INTO opportunity_admissions (execution_id, payload) VALUES (?, ?)",
                (request.execution_id, _payload(request)),
            )
            return True

    def _pending_payload(self, execution_id):
        with self._lock:
            row = self._db.execute(
                "SELECT payload FROM opportunity_requests JOIN opportunity_admissions "
                "USING (execution_id) WHERE execution_id = ? AND pending = 1",
                (execution_id,),
            ).fetchone()
        return None if row is None else row[0]

    def read_pending(self, execution_id: str) -> ExecutionRequest | None:
        """Read immutable evidence for recovery. Legacy ID-only claims have no evidence."""
        payload = self._pending_payload(execution_id)
        return None if payload is None else _read_request(payload, execution_id)

    def verify_pending(self, request: ExecutionRequest) -> ExecutionRequest | None:
        """Return the journal's typed copy only on an exact pending match. Read-only."""
        if type(request) is not ExecutionRequest or type(request.execution_id) is not str:
            return None
        try:
            supplied = _payload(request)
        except (TypeError, ValueError, RecursionError):
            return None
        stored = self._pending_payload(request.execution_id)
        return _read_request(stored, request.execution_id) if stored == supplied else None

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
