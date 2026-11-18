"""Command line.

    primeops-replay show --trade-id T1001 --as-of 2026-11-18T09:50:00+08:00
    primeops-replay summary --as-of 2026-11-18T11:00:00+08:00
    primeops-replay report --as-of 2026-11-18T11:00:00+08:00 --out breaks.csv

A time without an offset is read as Hong Kong time. --engine sql runs the
same rules as a SQL query; the explanatory notes always come from engine.py.
"""

from __future__ import annotations

import argparse
import dataclasses
import json
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

from . import output
from .engine import replay, replay_all
from .loader import HONG_KONG, InputError, Rejection, load_allocation_events, load_executions, parse_timestamp
from .models import BREAKS, AllocationEvent, Execution, ReconResult, Status
from .store import build_database, replay_sql

DEFAULT_DATA_DIR = Path("data/sample")
SQLITE_HEADER = b"SQLite format 3\x00"


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        return _run(args)
    except (InputError, OSError, UnicodeDecodeError, sqlite3.Error) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="primeops-replay",
        description="Reconcile broker executions with client allocations as of any point in time.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    show = sub.add_parser("show", help="one trade as of a time")
    show.add_argument("--trade-id", required=True)
    _shared_args(show, json_flag=True)

    summary = sub.add_parser("summary", help="every trade as of a time")
    _shared_args(summary, json_flag=True)

    report = sub.add_parser("report", help="write trades that need attention to a CSV file")
    _shared_args(report, json_flag=False)
    report.add_argument("--out", type=Path, required=True)
    report.add_argument("--all", action="store_true", help="include every trade")
    report.add_argument("--save-db", type=Path, help="keep the SQLite database (implies --engine sql)")
    return parser


def _shared_args(parser: argparse.ArgumentParser, *, json_flag: bool) -> None:
    parser.add_argument("--as-of", required=True, help="e.g. 2026-11-18T09:50:00+08:00")
    parser.add_argument("--data-dir", type=Path, default=DEFAULT_DATA_DIR)
    parser.add_argument("--engine", choices=["python", "sql"], default="python")
    if json_flag:
        parser.add_argument("--json", action="store_true")


def _run(args: argparse.Namespace) -> int:
    as_of = parse_timestamp(args.as_of, default_tz=HONG_KONG)
    executions = load_executions(args.data_dir / "executions.csv")
    events, rejected = load_allocation_events(args.data_dir / "allocation_events.jsonl")
    seen = [r for r in rejected if r.received_at is None or r.received_at <= as_of]

    save_db = getattr(args, "save_db", None)
    conn = None
    if args.engine == "sql" or save_db is not None:
        conn = build_database(executions, events, _db_path(save_db))

    only = args.trade_id if args.command == "show" else None
    results = reconcile(as_of, executions, events, seen, conn, only)

    if args.command == "show":
        result = results[0] if results else ReconResult(args.trade_id, as_of, Status.UNKNOWN_TRADE)
        execution = executions.get(result.trade_id)
        print(json.dumps(output.as_dict(result), indent=2) if args.json else output.render_trade(result, execution))
        if result.status is Status.UNKNOWN_TRADE:
            print(f"error: trade {args.trade_id} is not in the input files", file=sys.stderr)
            return 2
        return 0

    if args.command == "report":
        rows = results if args.all else [r for r in results if needs_attention(r, seen)]
        output.write_report(args.out, rows, executions)
        print(f"wrote {len(rows)} row(s) to {args.out}" + (f"; database saved to {save_db}" if save_db else ""))
        return 0

    if args.json:
        print(json.dumps([output.as_dict(r) for r in results], indent=2))
    else:
        print(output.render_summary(results, as_of))
        for rejection in sorted(seen, key=lambda r: r.trade_id is None):
            print(f"  {rejection.note}")
    return 0


def reconcile(
    as_of: datetime,
    executions: dict[str, Execution],
    events: list[AllocationEvent],
    seen: list[Rejection],
    conn: sqlite3.Connection | None = None,
    trade_id: str | None = None,
) -> list[ReconResult]:
    """State from the chosen engine; notes from engine.py plus any rejected messages."""
    if trade_id is not None:
        python_results = [replay(trade_id, as_of, executions, events)]
    else:
        python_results = replay_all(as_of, executions, events)
    states = python_results if conn is None else replay_sql(conn, as_of, trade_id)
    notes = {r.trade_id: r.notes for r in python_results}
    return [
        dataclasses.replace(
            state,
            notes=notes.get(state.trade_id, ()) + tuple(x.note for x in seen if x.trade_id == state.trade_id),
        )
        for state in states
    ]


def needs_attention(result: ReconResult, seen: list[Rejection]) -> bool:
    """A break, or a rejected message newer than the instruction in force."""
    if result.status in BREAKS:
        return True
    for rejection in seen:
        if rejection.trade_id != result.trade_id:
            continue
        if result.used_received_at is None or rejection.received_at is None:
            return True
        if rejection.received_at > result.used_received_at:
            return True
    return False


def _db_path(path: Path | None) -> str:
    """Only ever replace a file that is already an SQLite database."""
    if path is None:
        return ":memory:"
    if path.exists():
        if not path.is_file():
            raise InputError(f"{path} is not a file")
        with open(path, "rb") as handle:
            if handle.read(len(SQLITE_HEADER)) != SQLITE_HEADER:
                raise InputError(f"{path} exists and is not an SQLite database; refusing to overwrite it")
        path.unlink()
    return str(path)


if __name__ == "__main__":
    sys.exit(main())
