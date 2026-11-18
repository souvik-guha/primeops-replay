"""Command line.

    primeops-replay show --trade-id T1001 --as-of 2026-11-18T09:50:00+08:00
    primeops-replay summary --as-of 2026-11-18T11:00:00+08:00

A time without an offset is read as Hong Kong time.
"""

from __future__ import annotations

import argparse
import dataclasses
import json
import sys
from pathlib import Path

from . import output
from .engine import replay, replay_all
from .loader import HONG_KONG, InputError, load_allocation_events, load_executions, parse_timestamp
from .models import Status

DEFAULT_DATA_DIR = Path("data/sample")


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        return _run(args)
    except (InputError, OSError, UnicodeDecodeError) as exc:
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
    _shared_args(show)

    summary = sub.add_parser("summary", help="every trade as of a time")
    _shared_args(summary)
    return parser


def _shared_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--as-of", required=True, help="e.g. 2026-11-18T09:50:00+08:00")
    parser.add_argument("--data-dir", type=Path, default=DEFAULT_DATA_DIR)
    parser.add_argument("--json", action="store_true")


def _run(args: argparse.Namespace) -> int:
    as_of = parse_timestamp(args.as_of, default_tz=HONG_KONG)
    executions = load_executions(args.data_dir / "executions.csv")
    events, rejected = load_allocation_events(args.data_dir / "allocation_events.jsonl")
    seen = [r for r in rejected if r.received_at is None or r.received_at <= as_of]

    if args.command == "show":
        result = replay(args.trade_id, as_of, executions, events)
        result = dataclasses.replace(
            result, notes=result.notes + tuple(x.note for x in seen if x.trade_id == result.trade_id)
        )
        execution = executions.get(result.trade_id)
        print(json.dumps(output.as_dict(result), indent=2) if args.json else output.render_trade(result, execution))
        if result.status is Status.UNKNOWN_TRADE:
            print(f"error: trade {args.trade_id} is not in the input files", file=sys.stderr)
            return 2
        return 0

    results = replay_all(as_of, executions, events)
    if args.json:
        print(json.dumps([output.as_dict(r) for r in results], indent=2))
    else:
        print(output.render_summary(results, as_of))
        for rejection in sorted(seen, key=lambda r: r.trade_id is None):
            print(f"  {rejection.note}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
