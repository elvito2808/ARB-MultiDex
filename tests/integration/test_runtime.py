import json
import sqlite3
import subprocess
import sys
from io import StringIO
from pathlib import Path

import pytest

from arb_multidex.app import open_application
from arb_multidex.connections.mock import SAMPLE_MS
from arb_multidex.execution import FailurePlan
from arb_multidex.kucoin.market import top

OWNER = Path("config/simulation.owner.example.json")
WORKER = json.loads(OWNER.read_text())["relationship"]["id"]


def cli(state, command, *options):
    process = subprocess.run(
        [
            sys.executable, "-m", "arb_multidex", command, "--config", str(OWNER),
            "--state-dir", str(state), *options,
        ],
        capture_output=True, text=True, check=True,
    )
    return json.loads(process.stdout), [json.loads(line) for line in process.stderr.splitlines()]


def admit(app):
    relation, producers = app.bindings[WORKER]
    calls = app.markets.quotes(relation, producers)
    return app.detector.evaluate(WORKER, calls).execution_request


def test_cli_success_replay_stop_and_restart(tmp_path):
    result, logs = cli(tmp_path, "simulate")
    assert result["status"] == "COMPLETED"
    assert result["base_volume_hbar"] == "250.00000000"
    assert result["minimum_net_profit_usdt"] == "0.150000"
    assert [d["net_profit_usdt"] for d in result["decisions"]] == ["-0.691948", "0.334174"]
    assert [d["accepted"] for d in result["decisions"]] == [False, True]
    assert [(a["leg"], a["status"]) for a in result["attempts"]] == [
        ("DEX", "SUCCESS"), ("KUCOIN", "SUCCESS"),
    ]
    assert [a["recorded"] for a in result["after"]] == [
        "1250.00000000", "486.847677", "9.99999995", "750.00000000", "513.486500",
    ]
    assert result["real_orders"] == result["blockchain_transactions"] == 0
    assert all(e["simulated"] and e["market_source_mode"] == "MOCK" for e in logs)
    events = [e["event"] for e in logs]
    assert events.index("recovery_finished") < events.index("opportunity_admitted")
    assert cli(tmp_path, "stop")[0]["state"] == "STOPPED"
    assert cli(tmp_path, "simulate")[0]["reasons"] == ["STOPPED"]
    assert cli(tmp_path, "restart", "--worker", WORKER)[0]["state"] == "RUNNING"
    replay, _ = cli(tmp_path, "simulate")
    assert replay["status"] == "NO_OP" and "NOT_ADMITTED" in replay["reasons"]
    assert replay["before"] == replay["after"] == result["after"]
    assert replay["worker_status"]["completed_count"] == 1


@pytest.mark.parametrize(
    ("scenario", "reason"),
    [
        ("no-profit", "BELOW_MINIMUM_NET_PROFIT"),
        ("stale", "STALE_QUOTE"),
        ("unavailable", "SOURCE_UNAVAILABLE"),
    ],
)
def test_rejected_fixtures(tmp_path, scenario, reason):
    output, _ = cli(tmp_path, "simulate", "--scenario", scenario)
    assert output["status"] == "NO_OP"
    assert output["reasons"] == [reason]
    assert len(output["decisions"]) == 2
    assert output["before"] == output["after"]
    assert "admission" not in output


def test_incomplete_fixture(tmp_path):
    output, logs = cli(tmp_path, "simulate", "--scenario", "incomplete")
    assert output["status"] == "INCOMPLETE_ARBITRAGE"
    assert [(a["leg"], a["number"], a["status"]) for a in output["attempts"]] == [
        ("DEX", 1, "SUCCESS"), ("KUCOIN", 1, "FAILED"),
        ("KUCOIN", 2, "FAILED"), ("KUCOIN", 3, "FAILED"),
    ]
    assert output["before"][3:] == output["after"][3:]
    assert output["after"][0]["recorded"] == "1250.00000000"
    assert output["worker_status"]["incomplete_count"] == 1
    assert sum(e["event"] == "execution_retry" for e in logs) == 2


@pytest.mark.parametrize("graceful", [False, True])
def test_recover_or_gracefully_drain_confirmed_dex(tmp_path, graceful):
    with open_application([OWNER], tmp_path, "profitable", StringIO()) as app:
        app.recover()
        request = admit(app)
        app.engine.submit(request)
        app.engine.advance(request.execution_id)
        assert app.engine.advance(request.execution_id).status == "KUCOIN"
        if graceful:
            app.lifecycle.stop(WORKER)
    output, logs = cli(tmp_path, "simulate")
    assert output["status"] == "NO_OP"
    assert output["worker_status"]["completed_count"] == 1
    assert output["before"] == output["after"]
    assert output["after"][0]["recorded"] == "1250.00000000"
    if not graceful:
        assert any(e["event"] == "recovered_execution" for e in logs)
    with sqlite3.connect(tmp_path / "execution.sqlite3") as db:
        assert db.execute("SELECT COUNT(*) FROM sim_attempts").fetchone()[0] == 2


@pytest.mark.parametrize("legacy", [False, True])
def test_unresolved_run_preserves_confirmed_fills_and_holds(tmp_path, legacy):
    with open_application([OWNER], tmp_path, "profitable", StringIO()) as app:
        app.recover()
        request = admit(app)
        result = app.engine.execute(request, plan=FailurePlan(kucoin=("UNKNOWN",)))
        assert result.status == "AWAITING_RECONCILIATION"
    if legacy:
        with sqlite3.connect(tmp_path / "execution.sqlite3") as db:
            db.execute("DELETE FROM sim_run_admissions")
    output, _ = cli(tmp_path, "simulate")
    assert output["reasons"] == ["AWAITING_RECONCILIATION"]
    assert output["before"] == output["after"]
    assert output["after"][0]["recorded"] == "1250.00000000"
    assert output["after"][3]["reserved"] == "250.00000000"
    with open_application([OWNER], tmp_path, "profitable", StringIO()) as app:
        assert not app.lifecycle.restart(WORKER)
        assert app.engine.result(request.execution_id).attempts == result.attempts


def test_pending_s2_handoff_is_recovered_before_new_admission(tmp_path):
    with open_application([OWNER], tmp_path, "profitable", StringIO()) as app:
        app.recover()
        request = admit(app)
        assert app.engine.guard.pending() == (request.execution_id,)
    output, logs = cli(tmp_path, "simulate")
    assert output["status"] == "NO_OP"
    recovered = next(e for e in logs if e["event"] == "recovered_execution")
    assert recovered["execution_id"] == request.execution_id
    assert recovered["execution_status"] == "COMPLETED"


def test_real_detector_and_engine_accept_exact_owner_threshold(tmp_path):
    with open_application([OWNER], tmp_path, "profitable", StringIO()) as app:
        app.markets.spot.snapshot = top(SAMPLE_MS, "0.053262568", "300", "0.055", "300")
        app.recover()
        result = app.simulate(WORKER)
    assert result["status"] == "COMPLETED"
    assert result["admission"]["net_profit_usdt"] == "0.150000"
