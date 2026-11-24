import json
from io import StringIO

from arb_multidex.observability import EventLog


def test_log_discards_unapproved_fields_and_invalid_execution_id():
    stream = StringIO()
    EventLog(stream).emit(
        "worker_error", "worker", "ERROR", execution_id="private key text",
        error="RuntimeError", api_secret="secret-value", provider_payload={"key": "another-secret"},
        attempts=3,
    )
    event = json.loads(stream.getvalue())
    assert event["execution_id"] == "[REDACTED]"
    assert event["error_classification"] == "RuntimeError"
    assert event["simulated"]
    assert event["worker_id"] == event["relationship_id"] == "worker"
    assert "secret" not in stream.getvalue()
