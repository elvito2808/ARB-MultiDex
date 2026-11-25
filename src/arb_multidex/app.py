"""Run quote, admission, execution, and recovery cycles."""

from contextlib import ExitStack, contextmanager
from dataclasses import asdict
from decimal import Decimal
from fcntl import LOCK_EX, flock
from pathlib import Path

from arb_multidex.execution import FailurePlan, SimulationEngine
from arb_multidex.lifecycle import Lifecycle
from arb_multidex.lifecycle.market import MockMarkets, load_owner
from arb_multidex.observability import EventLog
from arb_multidex.opportunity import ExecutionGuard, OpportunityDetector

ASSET_NAMES = ("WHBAR", "USDC", "HBAR", "HBAR", "USDT")


def decimal_amount(units, scale):
    # Construct from text to preserve every atom independent of the Decimal context.
    return format(Decimal(f"{units}e-{scale}"), "f")


def balances(engine, worker):
    holdings = []
    for name, (account, asset, scale) in zip(ASSET_NAMES, worker.assets):
        balance = engine.balance(account, asset)
        holdings.append({
            "account": account, "asset": name, "scale": scale,
            "recorded": decimal_amount(balance.recorded, scale),
            "reserved": decimal_amount(balance.reserved, scale),
            "available": decimal_amount(balance.available, scale),
            "opening_known": balance.opening_known,
        })
    return holdings


def quote_error(calls):
    return next(
        (call.response["code"] for call in calls.values() if call.response["status"] != "OK"),
        None,
    )


class Application:
    def __init__(self, engine, markets, log, scenario):
        self.engine = engine
        self.markets = markets
        self.log = log
        self.scenario = scenario
        self.detector = OpportunityDetector(engine.guard, markets.context)
        self.bindings = {
            worker: markets.bind(config) for worker, config in engine.workers.items()
        }
        for relation, _ in self.bindings.values():
            self.detector.register(relation)
        self.lifecycle = Lifecycle(engine, log, self.health)
        self.recovered = False

    def health(self, worker):
        config = self.engine.workers[worker]
        if any(not self.engine.balance(a, x).opening_known for a, x, _ in config.assets):
            return "AWAITING_RECONCILIATION"
        return quote_error(self.markets.quotes(*self.bindings[worker]))

    def _results(self):
        with self.engine.journal.transaction(write=False) as db:
            return dict(db.execute("SELECT id, worker FROM sim_runs"))

    def recover(self):
        if self.recovered:
            return
        for worker in self.bindings:
            self.log.emit("recovery_started", worker, "RECOVERING")
        recovered = self.engine.recover()
        owners = self._results() if recovered else {}
        for result in recovered:
            worker = owners.get(result.execution_id)
            if worker is not None:
                self.execution_event(worker, result, "recovered_execution")
            else:
                # Legacy or malformed pending evidence may lack a trusted worker binding.
                self.log.emit(
                    "recovered_execution", None, "RECOVERING", execution_id=result.execution_id,
                    error=result.status, execution_status=result.status,
                )
        for worker in self.bindings:
            status = self.lifecycle.status(worker)
            self.log.emit("recovery_finished", worker, status["state"], **status)
        self.recovered = True

    def execution_event(self, worker, result, event):
        status = self.lifecycle.status(worker)
        error = result.status if result.status != "COMPLETED" else None
        for attempt in result.attempts:
            self.log.emit(
                "execution_retry" if attempt.number > 1 else "execution_attempt",
                worker, status["state"], execution_id=result.execution_id,
                leg=attempt.leg, attempt=attempt.number, attempt_status=attempt.status,
                error=attempt.status if attempt.status != "SUCCESS" else None,
            )
        self.log.emit(
            event, worker, status["state"], execution_id=result.execution_id, error=error,
            execution_status=result.status, **status,
        )
        if status["state"] == "TRADING_PAUSED":
            self.log.emit("worker_paused", worker, status["state"], execution_id=result.execution_id)

    def cycle(self, worker, report):
        relation, producers = self.bindings[worker]
        calls = self.markets.quotes(relation, producers)
        report["quotes"] = []
        for (direction, side), call in calls.items():
            summary = {
                "direction": direction.value, "side": side, "source": call.request["sourceId"],
                "status": call.response["status"],
            }
            if call.response["status"] == "OK":
                summary["observed_at"] = call.response["observedAt"]
            else:
                summary["code"] = call.response["code"]
            report["quotes"].append(summary)
            self.log.emit("quote", worker, "CHECKING_HEALTH", error=summary.get("code"), **summary)
        error = quote_error(calls)
        if error is not None:
            self.log.emit("health_failed", worker, "DEGRADED", error=error)
            # Fail closed if an otherwise healthy direction could qualify while this worker
            # failed startup health. Keep genuine source failures on their original calls.
            eligible_calls = {
                slot: call for slot, call in calls.items()
                if any(calls[slot[0], side].response["status"] != "OK" for side in ("BUY", "SELL"))
            }
        else:
            eligible_calls = calls
            self.log.emit("health_passed", worker, "RUNNING")
        evaluation = self.detector.evaluate(worker, eligible_calls)
        report["decisions"] = []
        request = evaluation.execution_request
        for decision in evaluation.decisions:
            profit = decision.net_profit
            accepted = request is not None and request.direction == decision.direction
            reason = decision.code or error
            if not reason and not accepted:
                reason = "NOT_ADMITTED" if decision.qualifies else "BELOW_MINIMUM_NET_PROFIT"
            summary = {
                "direction": decision.direction.value, "accepted": accepted, "reason": reason,
                "net_profit_usdt": decimal_amount(profit.units, profit.scale) if profit else None,
            }
            report["decisions"].append(summary)
            self.log.emit("evaluation", worker, "DEGRADED" if error else "RUNNING", **summary)
        if request is None:
            reasons = {decision["reason"] for decision in report["decisions"]}
            report.update(status="NO_OP", reasons=sorted(reasons))
            return report

        report["admission"] = {
            "execution_id": request.execution_id,
            "direction": request.direction.value,
            "net_profit_usdt": decimal_amount(request.net_profit.units, request.net_profit.scale),
        }
        self.log.emit("opportunity_admitted", worker, "RUNNING", **report["admission"])
        plan = FailurePlan(kucoin=("FAILED",) * 3) if self.scenario == "incomplete" else FailurePlan()
        result = self.engine.execute(request, plan=plan)
        self.execution_event(worker, result, "execution_result")
        report.update(status=result.status, attempts=[asdict(attempt) for attempt in result.attempts])
        return report

    def simulate(self, worker):
        if not self.recovered:
            raise RuntimeError("recovery must run before simulation")
        config = self.engine.workers[worker]
        report = {
            "worker_id": worker, "relationship_id": worker, "mode": "SIMULATION",
            "market_source_mode": self.markets.provenance, "scenario": self.scenario,
            "real_orders": 0, "blockchain_transactions": 0,
            "base_volume_hbar": decimal_amount(config.volume.units, config.volume.scale),
            "minimum_net_profit_usdt": decimal_amount(
                config.minimum_profit.units, config.minimum_profit.scale
            ),
            "before": balances(self.engine, config),
        }
        result = self.lifecycle.run(worker, lambda: self.cycle(worker, report))
        status = self.lifecycle.status(worker)
        if result is None:
            reason = status["last_error"] or status["state"]
            if reason == "RUNNING":
                reason = "CYCLE_INTERRUPTED"
            report.update(status="NO_OP", reasons=[reason])
        report.update(worker_status=status, after=balances(self.engine, config))
        self.log.emit("cycle_finished", worker, status["state"], cycle_status=report["status"])
        return report


@contextmanager
def open_application(configs, state_dir, scenario, stream):
    workers = tuple(load_owner(path) for path in configs)
    if not 1 <= len(workers) <= 3 or len({w.relationship_id for w in workers}) != len(workers):
        raise ValueError("configure one to three distinct workers")
    with open_runtime(workers, state_dir, MockMarkets(scenario), scenario, stream) as app:
        yield app


@contextmanager
def open_runtime(workers, state_dir, markets, scenario, stream, *, journal_prefix=""):
    state_dir = Path(state_dir)
    state_dir.mkdir(parents=True, exist_ok=True)
    s2, s3 = (
        state_dir / (journal_prefix + name) for name in ("opportunities.sqlite3", "execution.sqlite3")
    )
    with ExitStack() as stack:
        # Serialize commands and first-time journal creation for this directory.
        lock = stack.enter_context((state_dir / ".runtime.lock").open("a"))
        flock(lock, LOCK_EX)
        if s2.exists() != s3.exists() or (s2.exists() and s2.samefile(s3)):
            raise ValueError("expected two paired SQLite journals")
        guard = ExecutionGuard(s2)
        stack.callback(guard.close)
        engine = SimulationEngine(s3, workers, guard=guard, clock=markets.clock)
        stack.callback(engine.close)
        log = EventLog(stream, market_source_mode=markets.provenance)
        yield Application(engine, markets, log, scenario)
