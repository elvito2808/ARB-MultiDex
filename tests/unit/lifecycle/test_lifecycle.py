import json
from io import StringIO
from pathlib import Path

from arb_multidex.app import open_application

OWNER = Path("config/simulation.owner.example.json")
WORKER = json.loads(OWNER.read_text())["relationship"]["id"]


def test_worker_error_restart_limit_is_isolated_and_durable(tmp_path):
    second = tmp_path / "second.json"
    config = json.loads(OWNER.read_text())
    config["relationship"]["id"] = "second"
    second.write_text(json.dumps(config))
    state = tmp_path / "state"
    events = StringIO()
    with open_application([OWNER, second], state, "profitable", events) as app:
        lifecycle = app.lifecycle
        lifecycle.health = lambda worker: "SOURCE_UNAVAILABLE"
        delays = []
        lifecycle.sleep = delays.append
        epoch = app.markets.spot.epoch

        def fail():
            raise RuntimeError("confidential fixture")

        assert lifecycle.run(WORKER, fail) is None
        assert delays == [0.1, 0.2, 0.4]
        assert lifecycle.status(WORKER)["state"] == "ERROR"
        assert lifecycle.status("second")["state"] == "RUNNING"
        assert app.markets.spot.epoch == epoch
    with open_application([OWNER, second], state, "profitable", events) as app:
        lifecycle = app.lifecycle
        assert not lifecycle.start(WORKER)
        assert lifecycle.status(WORKER)["automatic_restart_attempts"] == 3
        assert lifecycle.restart(WORKER)
        assert lifecycle.status(WORKER)["state"] == "RUNNING"
        assert lifecycle.status("second")["state"] == "RUNNING"
    assert "confidential fixture" not in events.getvalue()


def test_pause_requires_explicit_restart(tmp_path):
    with open_application([OWNER], tmp_path, "profitable", StringIO()) as app:
        # Seed the existing S3 pause state as if five incomplete executions had accrued.
        with app.engine.journal.transaction() as db:
            db.execute("UPDATE sim_workers SET incomplete=5, paused=1 WHERE worker=?", (WORKER,))
        app.recover()
        assert app.simulate(WORKER)["reasons"] == ["TRADING_PAUSED"]
        assert not app.lifecycle.start(WORKER)
        assert app.lifecycle.restart(WORKER)
        assert app.lifecycle.status(WORKER)["consecutive_incomplete"] == 0
