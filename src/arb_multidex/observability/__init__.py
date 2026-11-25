"""Structured events for the offline runtime."""

import json
from datetime import datetime, timezone

from arb_multidex.opportunity.models import token

# Event dimensions are produced by runtime code. Never forward configuration,
# exception text, raw provider messages, or arbitrary structured extras.
FIELDS = frozenset({
    "attempt", "attempt_status", "automatic_restart_attempts", "code", "completed_count",
    "consecutive_incomplete", "cycle_status", "direction", "accepted", "execution_status",
    "incomplete_count", "last_error", "leg", "net_profit_usdt", "observed_at", "reason",
    "side", "source", "status",
})


class EventLog:
    """Accept only named, runtime-produced fields. Never serialize raw inputs or exceptions."""

    def __init__(self, stream, *, market_source_mode="MOCK"):
        self.stream = stream
        self.market_source_mode = market_source_mode

    def emit(self, event, worker, lifecycle_state, *, execution_id=None, error=None, **fields):
        record = {
            "timestamp": datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
            "event": event,
            "worker_id": worker,
            "relationship_id": worker,
            "market_source_mode": self.market_source_mode,
            "mode": "SIMULATION",
            "simulated": True,
            "lifecycle_state": lifecycle_state,
            **{key: value for key, value in fields.items() if key in FIELDS},
        }
        if execution_id is not None:
            record["execution_id"] = execution_id if token(execution_id) else "[REDACTED]"
        if error is not None:
            record["error_classification"] = error
        print(json.dumps(record, sort_keys=True), file=self.stream, flush=True)
