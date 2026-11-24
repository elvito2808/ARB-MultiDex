"""DEX-first simulation. Use a file-backed journal for restart recovery."""

import json
from dataclasses import asdict, dataclass
from time import time_ns

from arb_multidex.opportunity import Amount, Direction, ExecutionRequest, Leg, Market, Rate
from arb_multidex.opportunity.guard import AdmissionEvidenceError, ExecutionGuard, LegacyAdmission
from arb_multidex.opportunity.models import AcceptedCost, SeparateCost

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


def _typed_replay(request):
    # JSON alone erases enum/string, dataclass/dict and tuple/list distinctions.
    if (
        type(request.direction) is not Direction
        or type(request.net_profit) is not Amount
        or type(request.threshold) is not Amount
    ):
        return False
    for leg in (request.buy, request.sell):
        if type(leg) is not Leg or type(leg.market) is not Market or type(leg.costs) is not tuple:
            return False
        if any(
            type(amount) is not Amount
            for amount in (leg.base, leg.gross_settlement, leg.settlement)
        ):
            return False
        for cost in leg.costs:
            if type(cost) is SeparateCost:
                if type(cost.native) is not Amount or (
                    cost.rate is not None and type(cost.rate) is not Rate
                ):
                    return False
            elif type(cost) is not AcceptedCost:
                return False
    return True


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
    """Use paired file-backed S2 and S3 journals and a shared UTC millisecond clock.

    Call recover() before opening admission. submit() reserves from the verified
    S2 record; advance() drives one durable simulation transition at a time.
    """

    def __init__(self, path, workers, *, guard: ExecutionGuard, clock=None):
        if guard is None:
            raise ValueError("a shared S2 admission guard is required")
        workers = tuple(workers)
        self.workers = {w.relationship_id: w for w in workers}
        if not 1 <= len(workers) <= 3 or len(self.workers) != len(workers):
            raise ValueError("configure one to three distinct simulated workers")
        self.guard = guard
        self.clock = clock if clock is not None else lambda: time_ns() // 1_000_000
        self.journal = journal.Journal(path)
        try:
            with self.journal.transaction() as db:
                journal.seed(db, workers)
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

    @staticmethod
    def _journaled(db, request):
        execution_id = request.execution_id
        old = db.execute("SELECT request FROM sim_runs WHERE id=?", (execution_id,)).fetchone()
        if old is not None:
            if not _typed_replay(request) or old["request"] != json.dumps(
                asdict(request), sort_keys=True, allow_nan=False
            ):
                raise ValueError("execution ID already belongs to different terms")
            return True
        return (
            db.execute("SELECT 1 FROM sim_lost_handoffs WHERE id=?", (execution_id,)).fetchone()
            is not None
        )

    def _defer(self, error):
        reason = str(error)
        with self.journal.transaction() as db:
            db.execute(
                "INSERT INTO sim_admission_diagnostics VALUES (?,?) "
                "ON CONFLICT(id) DO UPDATE SET reason=excluded.reason",
                (error.execution_id, reason),
            )
        return Result(error.execution_id, "AWAITING_RECONCILIATION", reason, ())

    def submit(self, request: ExecutionRequest, *, plan=SUCCESS_PLAN):
        if (
            type(request) is not ExecutionRequest
            or type(request.execution_id) is not str
            or not request.execution_id
        ):
            raise ValueError("expected an identified S2 ExecutionRequest")
        if not isinstance(plan, FailurePlan):
            raise TypeError("invalid simulation failure plan")
        execution_id = request.execution_id
        with self.journal.transaction(write=False) as db:
            replay = self._journaled(db, request)
        if replay:
            return self.result(execution_id)

        # S2 may use the same SQLite file. Do not acquire its Python lock while
        # holding a writer transaction. Only S3 acknowledges journaled outcomes.
        try:
            accepted = self.guard.verify_pending(request)
        except AdmissionEvidenceError as exc:
            return self._defer(exc)
        with self.journal.transaction() as db:
            # Another driver may have reserved, finished and acknowledged while
            # verification was in flight. This check also covers a now-spent claim.
            if not self._journaled(db, request):
                if accepted is None:
                    reason = (
                        "unknown relationship"
                        if type(request.relationship_id) is str
                        and request.relationship_id not in self.workers
                        else "no matching pending S2 admission"
                    )
                    return Result(execution_id, "REJECTED", reason, ())
                config = self.workers.get(accepted.relationship_id)
                if config is None:
                    return Result(execution_id, "REJECTED", "unknown relationship", ())
                self._reserve_accepted(db, accepted, config, plan)
        return self.result(execution_id)

    def _reserve_accepted(self, db, request, config, plan):
        execution_id = request.execution_id
        worker = config.relationship_id
        db.execute(
            "INSERT INTO sim_runs(id, worker, network, request, moves, script, state) "
            "VALUES (?,?,?,?,?,?, 'REJECTED')",
            (
                execution_id,
                worker,
                config.dex.network,
                json.dumps(asdict(request), sort_keys=True),
                "{}",
                json.dumps(asdict(plan)),
            ),
        )
        try:
            moves, gas_evidence = prepare(request, config)
            state = db.execute(
                "SELECT paused,stopped FROM sim_workers WHERE worker=?", (worker,)
            ).fetchone()
            if state["paused"] or state["stopped"]:
                raise ValueError("TRADING_PAUSED" if state["paused"] else "STOPPED")
            if db.execute(
                "SELECT 1 FROM sim_runs WHERE (network=? OR worker=?) AND state IN ('DEX','KUCOIN')",
                (config.dex.network, worker),
            ).fetchone():
                raise ValueError("active execution on worker or network")
            # The guard validated that expires_ms is the minimum of both quote
            # deadlines and every trusted rate expiry. Sample after taking the write lock.
            if not self._fresh(request.evaluated_ms, request.expires_ms):
                raise ValueError("quote outside its validity window")
            journal.reserve(db, execution_id, moves)
        except ValueError as exc:
            db.execute("UPDATE sim_runs SET reason=? WHERE id=?", (str(exc), execution_id))
        else:
            db.execute(
                "UPDATE sim_runs SET state='DEX', moves=?, gas_evidence=? WHERE id=?",
                (json.dumps(moves), json.dumps(gas_evidence), execution_id),
            )

    def result(self, execution_id):
        with self.journal.transaction(write=False) as db:
            if db.execute("SELECT 1 FROM sim_lost_handoffs WHERE id=?", (execution_id,)).fetchone():
                return Result(execution_id, "REJECTED", "S2 handoff lost before execution", ())
            run = db.execute("SELECT * FROM sim_runs WHERE id=?", (execution_id,)).fetchone()
            if run is None:
                error = db.execute(
                    "SELECT reason FROM sim_admission_diagnostics WHERE id=?", (execution_id,)
                ).fetchone()
                if error is None:
                    raise KeyError(execution_id)
                return Result(execution_id, "AWAITING_RECONCILIATION", error["reason"], ())
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
        if run["state"] not in ACTIVE and not run["guard_resolved"]:
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
        return self._drain(self.result(execution_id))

    def _drain(self, result):
        while result.status in ACTIVE:
            result = self.advance(result.execution_id)
        return result

    def execute(self, request, **options):
        return self._drain(self.submit(request, **options))

    def recover(self):
        """Resume durable S3 outcomes first; classify unjournaled S2 claims independently."""
        with self.journal.transaction(write=False) as db:
            ids = [
                r[0]
                for r in db.execute(
                    "SELECT id FROM sim_runs WHERE state IN ('DEX','KUCOIN') OR guard_resolved=0 ORDER BY rowid"
                )
            ]
        results = {i: self._drain(self.result(i)) for i in ids}
        for execution_id in self.guard.pending():
            if execution_id in results:
                continue
            with self.journal.transaction(write=False) as db:
                existing = db.execute(
                    "SELECT 1 FROM sim_runs WHERE id=?", (execution_id,)
                ).fetchone()
            if existing:
                results[execution_id] = self._drain(self.result(execution_id))
                continue
            try:
                admitted = self.guard.read_pending(execution_id)
            except AdmissionEvidenceError as exc:
                results[execution_id] = self._defer(exc)
                continue
            if isinstance(admitted, ExecutionRequest):
                results[execution_id] = self.execute(admitted)
            elif isinstance(admitted, LegacyAdmission):
                with self.journal.transaction() as db:
                    db.execute(
                        "INSERT OR IGNORE INTO sim_lost_handoffs VALUES (?)", (execution_id,)
                    )
                self.guard.resolve(execution_id)
                results[execution_id] = self.result(execution_id)
            # None means another driver resolved the claim after enumeration.
        return tuple(results.values())

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
        return tuple(self._drain(self.result(i)) for i in ids)

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
