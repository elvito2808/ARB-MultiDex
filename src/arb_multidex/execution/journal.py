"""SQLite journal and integer-atom inventory. Each transition is one transaction."""

import hashlib
import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass
from threading import Lock

SCHEMA = """
-- Only a positively identified legacy ID-only claim is eligible for a new fence.
CREATE TABLE IF NOT EXISTS sim_admission_diagnostics (id TEXT PRIMARY KEY, reason TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS sim_lost_handoffs (id TEXT PRIMARY KEY);
CREATE TABLE IF NOT EXISTS sim_workers (
    worker TEXT PRIMARY KEY, incomplete INTEGER NOT NULL DEFAULT 0,
    paused INTEGER NOT NULL DEFAULT 0, stopped INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS sim_runs (
    id TEXT PRIMARY KEY, worker TEXT NOT NULL REFERENCES sim_workers, network TEXT NOT NULL,
    request TEXT NOT NULL, gas_evidence TEXT, moves TEXT NOT NULL, script TEXT NOT NULL, state TEXT NOT NULL,
    reason TEXT, guard_resolved INTEGER NOT NULL DEFAULT 0
);
-- Created only by the verified first-submission transaction. No legacy backfill.
CREATE TABLE IF NOT EXISTS sim_run_admissions (
    id TEXT PRIMARY KEY REFERENCES sim_runs, digest TEXT NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS sim_network ON sim_runs(network) WHERE state IN ('DEX','KUCOIN');
CREATE UNIQUE INDEX IF NOT EXISTS sim_worker ON sim_runs(worker) WHERE state IN ('DEX','KUCOIN');
CREATE TABLE IF NOT EXISTS sim_attempts (
    id TEXT NOT NULL REFERENCES sim_runs, leg TEXT NOT NULL, number INTEGER NOT NULL CHECK(number BETWEEN 1 AND 3),
    status TEXT NOT NULL, receipt TEXT NOT NULL, was_unknown INTEGER NOT NULL DEFAULT 0, changes TEXT NOT NULL DEFAULT '[]',
    PRIMARY KEY(id,leg,number)
);
CREATE TABLE IF NOT EXISTS sim_balances (
    account TEXT NOT NULL, asset TEXT NOT NULL, scale INTEGER NOT NULL, units TEXT NOT NULL,
    PRIMARY KEY(account,asset)
);
CREATE TABLE IF NOT EXISTS sim_openings (
    account TEXT NOT NULL, asset TEXT NOT NULL, scale INTEGER NOT NULL, units TEXT NOT NULL,
    PRIMARY KEY(account,asset), FOREIGN KEY(account,asset) REFERENCES sim_balances
);
CREATE TRIGGER IF NOT EXISTS sim_openings_no_update BEFORE UPDATE ON sim_openings
BEGIN SELECT RAISE(ABORT, 'immutable simulated opening declaration'); END;
CREATE TRIGGER IF NOT EXISTS sim_openings_no_delete BEFORE DELETE ON sim_openings
BEGIN SELECT RAISE(ABORT, 'immutable simulated opening declaration'); END;
CREATE TABLE IF NOT EXISTS sim_holds (
    id TEXT NOT NULL REFERENCES sim_runs, leg TEXT NOT NULL, account TEXT NOT NULL,
    asset TEXT NOT NULL, units TEXT NOT NULL, PRIMARY KEY(id,leg,account,asset),
    FOREIGN KEY(account,asset) REFERENCES sim_balances
);
CREATE INDEX IF NOT EXISTS sim_hold_asset ON sim_holds(account,asset);
"""


@dataclass(frozen=True)
class Balance:
    recorded: int
    reserved: int
    opening_known: bool = True

    @property
    def available(self):
        return self.recorded - self.reserved if self.opening_known else 0


class Journal:
    def __init__(self, path):
        self.lock = Lock()
        self.db = sqlite3.connect(path, isolation_level=None, check_same_thread=False, timeout=30)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA foreign_keys=ON")
        self.db.executescript(SCHEMA)

    @contextmanager
    def transaction(self, *, write=True):
        with self.lock:
            self.db.execute("BEGIN IMMEDIATE" if write else "BEGIN")
            try:
                yield self.db
                self.db.commit()
            except BaseException:
                self.db.rollback()
                raise

    def close(self):
        with self.lock:
            self.db.close()


def balance(db, account, asset):
    key = account, asset
    row = db.execute(
        "SELECT b.units, o.account IS NOT NULL AS opening_known FROM sim_balances b "
        "LEFT JOIN sim_openings o USING(account,asset,scale) WHERE b.account=? AND b.asset=?",
        key,
    ).fetchone()
    if row is None:
        raise KeyError(key)
    holds = db.execute("SELECT units FROM sim_holds WHERE account=? AND asset=?", key)
    return Balance(
        int(row["units"]), sum(int(r["units"]) for r in holds), bool(row["opening_known"])
    )


def seed(db, workers):
    openings = {}
    for worker in workers:
        for (account, asset, scale), units in zip(worker.assets, worker.opening):
            key = (account, asset)
            if openings.setdefault(key, (scale, units)) != (scale, units):
                raise ValueError("conflicting opening declarations for a shared simulated asset")

    db.executemany(
        "INSERT OR IGNORE INTO sim_workers(worker) VALUES (?)",
        [(worker.relationship_id,) for worker in workers],
    )
    for (account, asset), (scale, units) in openings.items():
        key = account, asset
        opening = db.execute(
            "SELECT scale,units FROM sim_openings WHERE account=? AND asset=?", key
        ).fetchone()
        current = db.execute(
            "SELECT scale FROM sim_balances WHERE account=? AND asset=?", key
        ).fetchone()
        if opening is not None and (opening["scale"], opening["units"]) != (scale, str(units)):
            raise ValueError("conflicting opening declarations for a shared simulated asset")
        if current is None:
            if opening is not None:
                raise ValueError("missing current balance; reconciliation required")
            values = account, asset, scale, str(units)
            db.execute("INSERT INTO sim_balances VALUES (?,?,?,?)", values)
            db.execute("INSERT INTO sim_openings VALUES (?,?,?,?)", values)
        elif current["scale"] != scale:
            raise ValueError(
                "conflicting scale for a shared simulated asset; reconciliation required"
            )
        # Existing inventory without an independent opening record stays readable
        # for recovery. Neither today's config nor the mutable balance proves its origin.


def opening_problem(db, assets):
    for account, asset in assets:
        if (
            db.execute(
                "SELECT 1 FROM sim_openings JOIN sim_balances USING(account,asset,scale) "
                "WHERE account=? AND asset=?",
                (account, asset),
            ).fetchone()
            is None
        ):
            return f"opening declaration unproven for {account} {asset}; reconciliation required"
    return None


def admission_digest(request, moves):
    # Bind the verified request to the exact movements approved in the same commit.
    return hashlib.sha256((request + "\0" + moves).encode()).hexdigest()


def reserve(db, execution_id, moves):
    holds = [
        (execution_id, leg, account, asset, -delta)
        for leg, entries in moves.items()
        for account, asset, delta in entries
        if delta < 0
    ]
    for _, _, account, asset, units in holds:
        if balance(db, account, asset).available < units:
            raise ValueError(f"insufficient simulated balance for {account} {asset}")
    db.executemany(
        "INSERT INTO sim_holds VALUES (?,?,?,?,?)",
        [(i, leg, account, asset, str(n)) for i, leg, account, asset, n in holds],
    )


def settle(db, execution_id, leg, changes):
    for account, asset, delta in changes:
        key = account, asset
        if delta < 0:
            hold_key = execution_id, leg, *key
            row = db.execute(
                "SELECT units FROM sim_holds WHERE id=? AND leg=? AND account=? AND asset=?",
                hold_key,
            ).fetchone()
            if row is None or int(row["units"]) < -delta:
                raise RuntimeError("confirmed debit exceeds its own reservation")
            db.execute(
                "UPDATE sim_holds SET units=? WHERE id=? AND leg=? AND account=? AND asset=?",
                (str(int(row["units"]) + delta), *hold_key),
            )
        current = balance(db, *key)
        if current.available + delta < 0:
            raise RuntimeError("simulated settlement conflicts with reserved inventory")
        db.execute(
            "UPDATE sim_balances SET units=? WHERE account=? AND asset=?",
            (str(current.recorded + delta), *key),
        )
