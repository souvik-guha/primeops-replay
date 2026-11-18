"""The same as-of rules as engine.py, written as SQL on SQLite.

The schema and the as-of query live in sql/. Duplicate messages are stored
as they arrived and filtered by the query.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from importlib.resources import files

from .models import AllocationEvent, Execution, ReconResult, status_for


def _sql(name: str) -> str:
    return files("primeops_replay").joinpath("sql").joinpath(name).read_text(encoding="utf-8")


SCHEMA = _sql("schema.sql")
AS_OF_QUERY = _sql("as_of.sql")

SPLIT_QUERY = "SELECT account, quantity FROM allocation_lines WHERE arrival_seq = ? ORDER BY line_no"


def to_db_time(moment: datetime) -> str:
    return moment.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")


def build_database(
    executions: dict[str, Execution],
    events: list[AllocationEvent],
    path: str = ":memory:",
) -> sqlite3.Connection:
    conn = sqlite3.connect(path)
    conn.executescript(SCHEMA)
    conn.executemany(
        "INSERT INTO executions VALUES (?, ?, ?, ?, ?)",
        [(x.trade_id, x.symbol, x.side, x.executed_quantity, to_db_time(x.received_at)) for x in executions.values()],
    )
    arrived = sorted(events, key=lambda e: (e.received_at, e.sequence))
    for seq, event in enumerate(arrived, start=1):
        conn.execute(
            "INSERT INTO allocation_messages VALUES (?, ?, ?, ?, ?)",
            (seq, event.message_id, event.trade_id, event.revision, to_db_time(event.received_at)),
        )
        conn.executemany(
            "INSERT INTO allocation_lines VALUES (?, ?, ?, ?)",
            [(seq, n, account, qty) for n, (account, qty) in enumerate(event.allocations, start=1)],
        )
    conn.commit()
    return conn


def replay_sql(conn: sqlite3.Connection, as_of: datetime, trade_id: str | None = None) -> list[ReconResult]:
    """State only (status, quantities, revision in force). Explanatory notes come from engine.py."""
    results = []
    rows = conn.execute(AS_OF_QUERY, {"as_of": to_db_time(as_of), "trade_id": trade_id}).fetchall()
    for tid, executed, allocated, revision, message_id, arrival_seq, received_text in rows:
        split = tuple(conn.execute(SPLIT_QUERY, (arrival_seq,)).fetchall()) if arrival_seq else ()
        results.append(
            ReconResult(
                trade_id=tid,
                as_of=as_of,
                status=status_for(executed, allocated, trade_known=True),
                executed_quantity=executed,
                allocated_quantity=allocated,
                allocations=split,
                revision_used=revision,
                message_id_used=message_id,
                used_received_at=_from_db_time(received_text) if received_text else None,
            )
        )
    return results


def _from_db_time(text: str) -> datetime:
    return datetime.strptime(text, "%Y-%m-%dT%H:%M:%S.%f").replace(tzinfo=timezone.utc)
