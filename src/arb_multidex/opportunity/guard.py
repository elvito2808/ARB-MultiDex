"""Atomic S2 admission. S3 explicitly resolves a request after reconciliation."""

import hashlib
import json
import sqlite3
from threading import Lock


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

    def admit(self, request, network):
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
            return cursor.rowcount == 1

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
