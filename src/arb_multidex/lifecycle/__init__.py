"""Independent worker control over the durable simulation engine."""

import sqlite3
import time
from threading import RLock

from arb_multidex.connections.feed import MarketError

RECOVERABLE = (OSError, sqlite3.Error, MarketError, RuntimeError, ValueError)


class Lifecycle:
    def __init__(self, engine, log, health, sleep=time.sleep):
        self.engine = engine
        self.log = log
        self.health = health
        self.sleep = sleep
        self.locks = {worker: RLock() for worker in engine.workers}
        with engine.journal.transaction() as db:
            db.execute("""
                CREATE TABLE IF NOT EXISTS runtime_restarts (
                    worker TEXT PRIMARY KEY REFERENCES sim_workers(worker),
                    attempts INTEGER NOT NULL CHECK (attempts BETWEEN 0 AND 3),
                    error TEXT
                )
            """)

    def _restart_record(self, worker, attempts, error=None):
        with self.engine.journal.transaction() as db:
            db.execute(
                "INSERT INTO runtime_restarts VALUES (?, ?, ?) "
                "ON CONFLICT(worker) DO UPDATE SET attempts=excluded.attempts, error=excluded.error",
                (worker, attempts, error),
            )

    def status(self, worker):
        config = self.engine.workers[worker]
        status = self.engine.worker_state(worker)
        with self.engine.journal.transaction(write=False) as db:
            restart = db.execute(
                "SELECT attempts, error FROM runtime_restarts WHERE worker=?", (worker,)
            ).fetchone()
            counts = dict(db.execute(
                "SELECT state, COUNT(*) FROM sim_runs JOIN sim_run_admissions USING(id) "
                "WHERE worker=? AND state IN ('COMPLETED','INCOMPLETE_ARBITRAGE') GROUP BY state",
                (worker,),
            ))
            active = db.execute(
                "SELECT id FROM sim_runs WHERE network=? AND state IN ('DEX','KUCOIN')",
                (config.dex.network,),
            ).fetchone()
        attempts, error = restart if restart else (0, None)
        state = status.status
        if state not in ("TRADING_PAUSED", "AWAITING_RECONCILIATION"):
            if active and self.engine.result(active["id"]).status == "AWAITING_RECONCILIATION":
                state = "AWAITING_RECONCILIATION"
            elif active:
                state = "RECOVERY_PENDING"
            elif attempts == 3 and error:
                state = "ERROR"
            elif error:
                state = "RECOVERING"
        return {
            "state": state,
            "automatic_restart_attempts": attempts,
            "last_error": error,
            "consecutive_incomplete": status.consecutive_incomplete,
            "completed_count": counts.get("COMPLETED", 0),
            "incomplete_count": counts.get("INCOMPLETE_ARBITRAGE", 0),
        }

    def start(self, worker, *, manual_restart=False):
        with self.locks[worker]:
            state = self.status(worker)["state"]
            blocked = {"AWAITING_RECONCILIATION", "RECOVERY_PENDING"}
            if not manual_restart:
                blocked.update(("ERROR", "TRADING_PAUSED"))
            if state in blocked:
                self.log.emit("worker_start_blocked", worker, state, error=state)
                return False
            error = self.health(worker)
            if error:
                self.log.emit("health_failed", worker, state, error=error)
                return False
            if manual_restart or state == "STOPPED":
                self.engine.manual_restart(worker)
                self._restart_record(worker, 0)
            self.log.emit("worker_started", worker, "RUNNING")
            return True

    def stop(self, worker):
        with self.locks[worker]:
            self.log.emit("worker_stopping", worker, "STOPPING")
            for result in self.engine.stop_worker(worker):
                self.log.emit(
                    "stop_recovery", worker, "STOPPING",
                    execution_id=result.execution_id, execution_status=result.status,
                )
            status = self.status(worker)
            self.log.emit("worker_stopped", worker, status["state"])

    def restart(self, worker):
        with self.locks[worker]:
            self.stop(worker)
            return self.start(worker, manual_restart=True)

    def run(self, worker, cycle):
        with self.locks[worker]:
            status = self.status(worker)
            if status["state"] == "RECOVERING":
                if not self._recover(worker, status["last_error"], status["automatic_restart_attempts"]):
                    return None
            elif status["state"] != "RUNNING":
                self.log.emit("cycle_blocked", worker, status["state"], error=status["state"])
                return None
            try:
                result = cycle()
            except RECOVERABLE as exc:
                error = type(exc).__name__
                self.log.emit("worker_error", worker, "RECOVERING", error=error)
                self._recover(worker, error, status["automatic_restart_attempts"])
                return None
            if self.status(worker)["automatic_restart_attempts"]:
                self._restart_record(worker, 0)
            return result

    def _recover(self, worker, error, attempts):
        # Fence the worker before restarting. S3 drains confirmed work or leaves holds durable.
        self._restart_record(worker, attempts, error)
        for attempt in range(attempts + 1, 4):
            self.log.emit("automatic_restart", worker, "RECOVERING", error=error, attempt=attempt)
            self.sleep(0.1 * 2 ** (attempt - 1))
            try:
                self.engine.stop_worker(worker)
                state = self.status(worker)["state"]
                if state in ("TRADING_PAUSED", "AWAITING_RECONCILIATION"):
                    self._restart_record(worker, 0)
                    self.log.emit("automatic_restart_blocked", worker, state, error=state)
                    return False
                error = self.health(worker)
                if error is None:
                    self.engine.manual_restart(worker)
                    self._restart_record(worker, attempt)
                    self.log.emit("automatic_restart_succeeded", worker, "RUNNING", attempt=attempt)
                    return True
            except RECOVERABLE as exc:
                error = type(exc).__name__
            self._restart_record(worker, attempt, error)
        self.log.emit("restart_limit_reached", worker, "ERROR", error=error)
        return False
