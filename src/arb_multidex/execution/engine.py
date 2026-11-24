"""DEX-first simulation. Use a file-backed journal for restart recovery."""

import json
from dataclasses import asdict, dataclass
from time import time_ns

from arb_multidex.opportunity import ExecutionRequest

from . import journal
from .simulation import FailurePlan, prepare, simulate_dex, simulate_kucoin

ACTIVE = ("DEX", "KUCOIN")
SUCCESS_PLAN = FailurePlan()


@dataclass(frozen=True)
class Attempt:
    leg: str
    number: int
    status: str
    was_unknown: bool
    changes: tuple  # Confirmed (account, asset, signed atoms) entries.


@dataclass(frozen=True)
class Result:
    execution_id: str
    status: str
    reason: str | None
    attempts: tuple[Attempt, ...]


@dataclass(frozen=True)
class WorkerState:
    status: str
    consecutive_incomplete: int


def _run(db, execution_id):
    row = db.execute("SELECT * FROM sim_runs WHERE id=?", (execution_id,)).fetchone()
    if row is None:
        raise KeyError(execution_id)
    return row


def _finish(db, run, state, reason=None):
    db.execute("DELETE FROM sim_holds WHERE id=?", (run["id"],))
    db.execute("UPDATE sim_runs SET state=?, reason=? WHERE id=?", (state, reason, run["id"]))
    if state == "COMPLETED":
        db.execute("UPDATE sim_workers SET incomplete=0 WHERE worker=?", (run["worker"],))
    elif state == "INCOMPLETE_ARBITRAGE":
        db.execute(
            "UPDATE sim_workers SET incomplete=incomplete+1, paused=(incomplete+1>=5) WHERE worker=?",
            (run["worker"],),
        )


class SimulationEngine:
    """Pass explicit WorkerConfigs and optionally S2's ExecutionGuard.

    execute(request, dex_quote=...) runs to a final or unknown receipt. submit
    and advance expose the same durable transitions to a scheduler. At startup
    call recover() before opening admission, with the same paired S2 and S3
    journals. Pass the original S1 simulated DEX response retained for the
    accepted S2 leg as dex_quote. It contains the native gas cost omitted
    from ExecutionRequest. Seeding an existing journal never refills inventory.
    """

    def __init__(self, path, workers, *, guard=None, clock=None):
        workers = tuple(workers)
        self.workers = {w.relationship_id: w for w in workers}
        if not 1 <= len(workers) <= 3 or len(self.workers) != len(workers):
            raise ValueError("configure one to three distinct simulated workers")
        self.guard = guard
        self.clock = clock if clock is not None else lambda: time_ns() // 1_000_000
        self.journal = journal.Journal(path)
        try:
            with self.journal.transaction() as db:
                for config in workers:
                    journal.seed(db, config)
        except BaseException:
            self.close()
            raise

    def close(self):
        self.journal.close()

    def balance(self, account, asset):
        with self.journal.transaction(write=False) as db:
            return journal.balance(db, account, asset)

    def worker_state(self, worker):
        with self.journal.transaction(write=False) as db:
            row = db.execute("SELECT * FROM sim_workers WHERE worker=?", (worker,)).fetchone()
            if row is None:
                raise KeyError(worker)
            status = (
                "TRADING_PAUSED" if row["paused"] else "STOPPED" if row["stopped"] else "RUNNING"
            )
            return WorkerState(status, row["incomplete"])

    def _fresh(self, evaluated, expires):
        now = self.clock()
        return (
            type(now) is int
            and type(evaluated) is int
            and type(expires) is int
            and evaluated <= now < expires
        )

    def submit(self, request: ExecutionRequest, *, dex_quote=None, plan=SUCCESS_PLAN):
        if (
            not isinstance(request, ExecutionRequest)
            or not isinstance(request.execution_id, str)
            or not request.execution_id
        ):
            raise ValueError("expected an identified S2 ExecutionRequest")
        if not isinstance(plan, FailurePlan):
            raise TypeError("invalid simulation failure plan")
        config = self.workers[request.relationship_id]
        execution_id = request.execution_id
        encoded = json.dumps(asdict(request), sort_keys=True)
        with self.journal.transaction() as db:
            if db.execute("SELECT 1 FROM sim_lost_handoffs WHERE id=?", (execution_id,)).fetchone():
                return Result(execution_id, "REJECTED", "S2 handoff lost before execution", ())
            old = db.execute("SELECT request FROM sim_runs WHERE id=?", (execution_id,)).fetchone()
            if old is not None:
                if old["request"] != encoded:
                    raise ValueError("execution ID already belongs to different terms")
            else:
                worker = config.relationship_id
                db.execute(
                    "INSERT INTO sim_runs(id, worker, network, request, moves, script, state) "
                    "VALUES (?,?,?,?,?,?, 'REJECTED')",
                    (
                        execution_id,
                        worker,
                        config.dex.network,
                        encoded,
                        "{}",
                        json.dumps(asdict(plan)),
                    ),
                )
                try:
                    moves, gas_evidence = prepare(request, config, dex_quote)
                    state = db.execute(
                        "SELECT paused,stopped FROM sim_workers WHERE worker=?", (worker,)
                    ).fetchone()
                    if state["paused"] or state["stopped"]:
                        raise ValueError("TRADING_PAUSED" if state["paused"] else "STOPPED")
                    if not self._fresh(request.evaluated_ms, request.expires_ms):
                        raise ValueError("quote outside its validity window")
                    if db.execute(
                        "SELECT 1 FROM sim_runs WHERE (network=? OR worker=?) AND state IN ('DEX','KUCOIN')",
                        (config.dex.network, worker),
                    ).fetchone():
                        raise ValueError("active execution on worker or network")
                    journal.reserve(db, execution_id, moves)
                except ValueError as exc:
                    db.execute("UPDATE sim_runs SET reason=? WHERE id=?", (str(exc), execution_id))
                else:
                    db.execute(
                        "UPDATE sim_runs SET state='DEX', moves=?, gas_evidence=? WHERE id=?",
                        (json.dumps(moves), json.dumps(gas_evidence), execution_id),
                    )
        return self.result(execution_id)

    def result(self, execution_id):
        with self.journal.transaction(write=False) as db:
            if db.execute("SELECT 1 FROM sim_lost_handoffs WHERE id=?", (execution_id,)).fetchone():
                return Result(execution_id, "REJECTED", "S2 handoff lost before execution", ())
            run = _run(db, execution_id)
            rows = db.execute(
                "SELECT * FROM sim_attempts WHERE id=? ORDER BY leg,number", (execution_id,)
            )
            attempts = tuple(
                Attempt(
                    r["leg"],
                    r["number"],
                    r["status"],
                    bool(r["was_unknown"]),
                    tuple(map(tuple, json.loads(r["changes"]))),
                )
                for r in rows
            )
            status = run["state"]
            if attempts and attempts[-1].status == "UNKNOWN":
                status = "AWAITING_RECONCILIATION"
        if run["state"] not in ACTIVE and self.guard is not None and not run["guard_resolved"]:
            # Outside the S3 transaction: S2 may use the very same SQLite file.
            # Replaying this acknowledgement after a crash is idempotent.
            self.guard.resolve(execution_id)
            with self.journal.transaction() as db:
                db.execute("UPDATE sim_runs SET guard_resolved=1 WHERE id=?", (execution_id,))
        return Result(execution_id, status, run["reason"], attempts)

    def advance(self, execution_id):
        """Commit at most one submission or status check. Safe for concurrent drivers."""
        with self.journal.transaction() as db:
            run = _run(db, execution_id)
            if run["state"] in ACTIVE:
                self._advance(db, run)
        return self.result(execution_id)

    def _advance(self, db, run):
        leg = run["state"]
        key = run["id"], leg
        attempts = db.execute(
            "SELECT * FROM sim_attempts WHERE id=? AND leg=? ORDER BY number", key
        ).fetchall()
        previous = attempts[-1] if attempts else None
        if previous and previous["status"] in ("SUBMITTED", "UNKNOWN"):
            outcome = previous["receipt"]
            if outcome == "UNKNOWN":
                db.execute(
                    "UPDATE sim_attempts SET status='UNKNOWN',was_unknown=1 WHERE id=? AND leg=? AND number=?",
                    (*key, previous["number"]),
                )
                return
            entries = json.loads(run["moves"])[leg]
            if leg == "DEX":
                changes = simulate_dex(entries, outcome)
            else:
                filled = sum(abs(json.loads(a["changes"])[0][2]) for a in attempts[:-1])
                changes = simulate_kucoin(entries, outcome, filled)
            journal.settle(db, *key, changes)
            db.execute(
                "UPDATE sim_attempts SET status=?, changes=? WHERE id=? AND leg=? AND number=?",
                (outcome, json.dumps(changes), *key, previous["number"]),
            )
            if outcome == "SUCCESS":
                db.execute("DELETE FROM sim_holds WHERE id=? AND leg=?", key)
                if leg == "DEX":
                    db.execute("UPDATE sim_runs SET state='KUCOIN' WHERE id=?", (run["id"],))
                else:
                    _finish(db, run, "COMPLETED")
            elif len(attempts) == 3:
                _finish(
                    db,
                    run,
                    "DEX_FAILED" if leg == "DEX" else "INCOMPLETE_ARBITRAGE",
                    "attempts exhausted",
                )
            return

        # A new attempt is legal only after verifying a final failed/partial receipt.
        if previous and previous["status"] not in ("FAILED", "PARTIAL"):
            raise RuntimeError("unexpected previous simulated attempt state")
        request = json.loads(run["request"])
        if not self._fresh(request["evaluated_ms"], request["expires_ms"]):
            _finish(
                db,
                run,
                "DEX_FAILED" if leg == "DEX" else "INCOMPLETE_ARBITRAGE",
                "quote expired before submission",
            )
            return
        script = json.loads(run["script"])[leg.lower()]
        number = len(attempts) + 1
        receipt = script[number - 1] if number <= len(script) else "SUCCESS"
        db.execute(
            "INSERT INTO sim_attempts(id,leg,number,status,receipt) VALUES (?,?,?,'SUBMITTED',?)",
            (*key, number, receipt),
        )

    def reconcile(self, execution_id, leg, number, outcome):
        """Supply final simulated evidence for the exact unknown attempt and resume."""
        allowed = ("SUCCESS", "FAILED", "PARTIAL") if leg == "KUCOIN" else ("SUCCESS", "FAILED")
        if leg not in ACTIVE or outcome not in allowed:
            raise ValueError("invalid final receipt")
        with self.journal.transaction() as db:
            changed = db.execute(
                "UPDATE sim_attempts SET receipt=?,status='SUBMITTED' WHERE id=? AND leg=? "
                "AND number=? AND status='UNKNOWN' AND receipt='UNKNOWN'",
                (outcome, execution_id, leg, number),
            ).rowcount
            if changed != 1:
                raise ValueError("no matching unknown attempt")
        return self._drain(execution_id)

    def _drain(self, execution_id):
        result = self.result(execution_id)
        while result.status in ACTIVE:
            result = self.advance(execution_id)
        return result

    def execute(self, request, **options):
        self.submit(request, **options)
        return self._drain(request.execution_id)

    def recover(self):
        """Recover the paired journals before admitting new S2 requests.

        A crash after S2 admission but before S3 submission leaves only the
        claim ID. Fence it in S3 before releasing the claim. The opportunity
        stays spent, with no simulated fill or reservation to reconstruct.
        """
        lost = []
        if self.guard is not None:
            for execution_id in self.guard.pending():
                with self.journal.transaction() as db:
                    if db.execute("SELECT 1 FROM sim_runs WHERE id=?", (execution_id,)).fetchone():
                        continue
                    db.execute(
                        "INSERT OR IGNORE INTO sim_lost_handoffs VALUES (?)", (execution_id,)
                    )
                self.guard.resolve(execution_id)
                lost.append(
                    Result(execution_id, "REJECTED", "S2 handoff lost before execution", ())
                )
        with self.journal.transaction(write=False) as db:
            ids = [
                r[0]
                for r in db.execute(
                    "SELECT id FROM sim_runs WHERE state IN ('DEX','KUCOIN') "
                    "OR (? AND guard_resolved=0) ORDER BY rowid",
                    (self.guard is not None,),
                )
            ]
        return tuple(lost) + tuple(self._drain(i) for i in ids)

    def stop_worker(self, worker):
        """Close admission first, then drain accepted work or leave an unknown receipt durable."""
        with self.journal.transaction() as db:
            if not db.execute(
                "UPDATE sim_workers SET stopped=1 WHERE worker=?", (worker,)
            ).rowcount:
                raise KeyError(worker)
            ids = [
                r[0]
                for r in db.execute(
                    "SELECT id FROM sim_runs WHERE worker=? AND state IN ('DEX','KUCOIN')",
                    (worker,),
                )
            ]
        return tuple(self._drain(i) for i in ids)

    def manual_restart(self, worker):
        """Explicit operator action after review. Unresolved trades keep admission closed."""
        with self.journal.transaction() as db:
            if db.execute(
                "SELECT 1 FROM sim_runs WHERE worker=? AND state IN ('DEX','KUCOIN')", (worker,)
            ).fetchone():
                raise ValueError("worker has an unresolved execution")
            if not db.execute(
                "UPDATE sim_workers SET incomplete=CASE WHEN paused THEN 0 ELSE incomplete END, "
                "paused=0, stopped=0 WHERE worker=?",
                (worker,),
            ).rowcount:
                raise KeyError(worker)
