"""Rebuild what an analyst could see for a trade at a given time.

Rules:
  1. Only records received at or before as_of count.
  2. Records are read in arrival order (received time, then file order).
  3. A repeated message ID counts once.
  4. A higher revision replaces the current split. A lower revision arriving
     later is stale. Two different messages with the same revision conflict,
     and the first one stands.
  5. A trade's fills add up: executed quantity is the sum of the fills that
     had arrived, and the average price is weighted by quantity.
  6. A repeated fill ID counts once.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime

from .models import AllocationEvent, Execution, Fill, ReconResult, status_for


def replay(
    trade_id: str,
    as_of: datetime,
    executions: dict[str, Execution],
    events: list[AllocationEvent],
) -> ReconResult:
    trade_events = [e for e in events if e.trade_id == trade_id]
    return _replay_trade(trade_id, as_of, executions.get(trade_id), trade_events)


def replay_all(
    as_of: datetime,
    executions: dict[str, Execution],
    events: list[AllocationEvent],
) -> list[ReconResult]:
    by_trade: dict[str, list[AllocationEvent]] = defaultdict(list)
    for event in events:
        by_trade[event.trade_id].append(event)
    trade_ids = sorted(set(executions) | set(by_trade))
    return [_replay_trade(t, as_of, executions.get(t), by_trade.get(t, [])) for t in trade_ids]


def _counted_fills(execution: Execution | None, as_of: datetime) -> tuple[list[Fill], list[str]]:
    """The fills that count at as_of, and a note for each resend that was ignored."""
    if execution is None:
        return [], []
    arrived = sorted(
        (f for f in execution.fills if f.received_at <= as_of),
        key=lambda f: (f.received_at, f.sequence),
    )
    first_copy: dict[str, Fill] = {}
    notes: list[str] = []
    for fill in arrived:
        original = first_copy.get(fill.fill_id)
        if original is None:
            first_copy[fill.fill_id] = fill
        elif (fill.quantity, fill.price_e4) == (original.quantity, original.price_e4):
            notes.append(f"{fill.fill_id}: duplicate fill, ignored")
        else:
            notes.append(f"{fill.fill_id}: repeated fill ID with different content, ignored")
    return list(first_copy.values()), notes


def _replay_trade(
    trade_id: str,
    as_of: datetime,
    execution: Execution | None,
    trade_events: list[AllocationEvent],
) -> ReconResult:
    fills, notes = _counted_fills(execution, as_of)

    visible = sorted(
        (e for e in trade_events if e.received_at <= as_of),
        key=lambda e: (e.received_at, e.sequence),
    )

    current: AllocationEvent | None = None
    first_copy: dict[str, AllocationEvent] = {}
    for event in visible:
        if event.message_id in first_copy:
            original = first_copy[event.message_id]
            if (event.revision, event.allocations) == (original.revision, original.allocations):
                notes.append(f"{event.message_id}: duplicate message, ignored")
            else:
                notes.append(f"{event.message_id}: repeated message ID with different content, ignored")
            continue
        first_copy[event.message_id] = event

        if current is None or event.revision > current.revision:
            current = event
        elif event.revision == current.revision:
            notes.append(f"{event.message_id}: revision {event.revision} conflicts with {current.message_id}, ignored")
        else:
            notes.append(
                f"{event.message_id}: stale revision {event.revision} arrived after "
                f"revision {current.revision}, ignored"
            )

    executed = sum(f.quantity for f in fills) if fills else None
    notionals = [f.notional_e4 for f in fills]
    notional = sum(n for n in notionals if n is not None) if fills and None not in notionals else None
    allocated = current.allocated_quantity if current else None
    trade_known = execution is not None or bool(trade_events)
    return ReconResult(
        trade_id=trade_id,
        as_of=as_of,
        status=status_for(executed, allocated, trade_known=trade_known),
        executed_quantity=executed,
        allocated_quantity=allocated,
        allocations=current.allocations if current else (),
        revision_used=current.revision if current else None,
        message_id_used=current.message_id if current else None,
        used_received_at=current.received_at if current else None,
        notes=tuple(notes),
        fill_count=len(fills),
        notional_e4=notional,
    )
