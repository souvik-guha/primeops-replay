"""Text, JSON and CSV output."""

from __future__ import annotations

import csv
from datetime import datetime
from pathlib import Path
from typing import Any

from .models import Execution, ReconResult, format_e4

REPORT_COLUMNS = [
    "as_of",
    "trade_id",
    "symbol",
    "side",
    "status",
    "executed_quantity",
    "allocated_quantity",
    "difference",
    "fills",
    "average_price",
    "revision_used",
    "message_id_used",
    "allocations",
    "notes",
]


def render_trade(result: ReconResult, execution: Execution | None) -> str:
    header = f"Trade {result.trade_id}"
    if execution is not None:
        header += f"  {execution.symbol} {execution.side}"
    lines = [
        f"{header}  as of {result.as_of.isoformat()}",
        f"  Status      {result.status}",
        f"  Executed    {_qty(result.executed_quantity)}{_fill_summary(result)}",
    ]
    if result.revision_used is None:
        lines.append("  Allocated   -  (no allocation received)")
    else:
        lines.append(
            f"  Allocated   {_qty(result.allocated_quantity)}"
            f"  (revision {result.revision_used}, message {result.message_id_used})"
        )
        lines += [f"    {account:<10} {_qty(quantity)}" for account, quantity in result.allocations]
    difference = result.difference
    if difference is not None:
        label = "unallocated" if difference > 0 else "over-allocated" if difference < 0 else ""
        lines.append(f"  Difference  {abs(difference):,} {label}".rstrip())
    if result.notes:
        lines.append("  Notes")
        lines += [f"    - {note}" for note in result.notes]
    return "\n".join(lines)


def render_summary(results: list[ReconResult], as_of: datetime) -> str:
    lines = [
        f"As of {as_of.isoformat()}",
        f"  {'Trade':<8}{'Status':<21}{'Executed':>10}{'Fills':>6}{'Avg price':>11}{'Allocated':>11}{'Diff':>8}",
    ]
    for r in results:
        diff = "-" if r.difference is None else f"{r.difference:,}"
        fills = str(r.fill_count) if r.fill_count else "-"
        lines.append(
            f"  {r.trade_id:<8}{r.status:<21}{_qty(r.executed_quantity):>10}{fills:>6}{_price(r.average_price_e4):>11}"
            f"{_qty(r.allocated_quantity):>11}{diff:>8}"
        )
    return "\n".join(lines)


def as_dict(result: ReconResult) -> dict[str, Any]:
    return {
        "trade_id": result.trade_id,
        "as_of": result.as_of.isoformat(),
        "status": result.status.value,
        "executed_quantity": result.executed_quantity,
        "fills": result.fill_count,
        "average_price": None if result.average_price_e4 is None else format_e4(result.average_price_e4),
        "notional": None if result.notional_e4 is None else format_e4(result.notional_e4),
        "allocated_quantity": result.allocated_quantity,
        "difference": result.difference,
        "revision_used": result.revision_used,
        "message_id_used": result.message_id_used,
        "allocations": [{"account": a, "quantity": q} for a, q in result.allocations],
        "notes": list(result.notes),
    }


def write_report(path: Path, results: list[ReconResult], executions: dict[str, Execution]) -> None:
    with open(path, "w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(REPORT_COLUMNS)
        for r in results:
            execution = executions.get(r.trade_id)
            text_cells = [
                r.as_of.isoformat(),
                r.trade_id,
                execution.symbol if execution else "",
                execution.side if execution else "",
                r.status.value,
            ]
            numbers = [
                r.executed_quantity,
                r.allocated_quantity,
                r.difference,
                r.fill_count,
                _price(r.average_price_e4, blank=""),
                r.revision_used,
            ]
            tail = [
                r.message_id_used or "",
                "; ".join(f"{account} {quantity}" for account, quantity in r.allocations),
                " | ".join(r.notes),
            ]
            writer.writerow([_cell(c) for c in text_cells] + numbers + [_cell(c) for c in tail])


def _cell(text: str) -> str:
    """Stop Excel from treating a text cell as a formula (the loader already restricts IDs)."""
    return "'" + text if text[:1] in ("=", "+", "-", "@", "\t", "\r") else text


def _qty(value: int | None) -> str:
    return "-" if value is None else f"{value:,}"


def _price(value_e4: int | None, blank: str = "-") -> str:
    return blank if value_e4 is None else format_e4(value_e4)


def _fill_summary(result: ReconResult) -> str:
    """'(3 fills, average price 74.2417)'. Nothing for a single fill with no price."""
    if result.fill_count == 0 or (result.fill_count == 1 and result.average_price_e4 is None):
        return ""
    if result.average_price_e4 is None:
        return f"  ({result.fill_count} fills)"
    if result.fill_count == 1:
        return f"  (1 fill at {format_e4(result.average_price_e4)})"
    return f"  ({result.fill_count} fills, average price {format_e4(result.average_price_e4)})"
