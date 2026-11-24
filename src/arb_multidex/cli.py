"""Command-line entry point for the simulation runtime."""

import argparse
import json
import sqlite3
import sys

from arb_multidex.app import open_application
from arb_multidex.lifecycle.market import SCENARIOS


def parser():
    root = argparse.ArgumentParser(description="Run the offline ARB-MultiDex simulation.")
    commands = root.add_subparsers(dest="command", required=True)
    for command, help_text in (
        ("simulate", "recover and evaluate one offline cycle per selected worker"),
        ("start", "health-check and start the selected workers"),
        ("stop", "gracefully stop the selected workers"),
        ("restart", "explicitly restart one worker after draining its outstanding work"),
        ("status", "show worker lifecycle state and health"),
    ):
        sub = commands.add_parser(command, help=help_text)
        sub.add_argument("--config", action="append", required=True, help="owner JSON, repeatable")
        sub.add_argument("--state-dir", default=".arb-multidex", help="paired SQLite journal folder")
        sub.add_argument("--scenario", choices=SCENARIOS, default="profitable")
        sub.add_argument("--worker", required=command == "restart", help="one relationship ID")
    return root


def main(argv=None):
    args = parser().parse_args(argv)
    try:
        with open_application(args.config, args.state_dir, args.scenario, sys.stderr) as app:
            workers = (args.worker,) if args.worker else tuple(app.bindings)
            if any(worker not in app.bindings for worker in workers):
                print("Unknown configured worker.", file=sys.stderr)
                return 2
            if args.command in ("start", "simulate"):
                app.recover()
            exit_code = 0
            for worker in workers:
                if args.command == "simulate":
                    result = app.simulate(worker)
                else:
                    if args.command in ("start", "restart"):
                        if not getattr(app.lifecycle, args.command)(worker):
                            exit_code = 1
                    elif args.command == "stop":
                        app.lifecycle.stop(worker)
                    result = {"worker_id": worker, **app.lifecycle.status(worker)}
                    if args.command != "stop":
                        result["health_error"] = app.health(worker)
                print(json.dumps(result, sort_keys=True), flush=True)
            return exit_code
    except (OSError, sqlite3.Error, ValueError, TypeError, KeyError, AttributeError) as exc:
        print(json.dumps({"event": "runtime_error", "error_classification": type(exc).__name__}),
              file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        # SQLite rolls back the interrupted transition. Previous receipts remain recoverable.
        return 130
