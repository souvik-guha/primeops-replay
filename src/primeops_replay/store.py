"""The same as-of rules as engine.py, written as SQL on SQLite.

Timestamps are stored as fixed-width UTC text, so comparing text compares time.
Duplicate messages are stored as they arrived and filtered by the query.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone

from .models import AllocationEvent, Execution, ReconResult, status_for

SCHEMA = """
CREATE TABLE executions (
    trade_id          TEXT PRIMARY KEY,
    symbol            TEXT NOT NULL,
    side              TEXT NOT NULL CHECK (side IN ('BUY', 'SELL')),
    executed_quantity INTEGER NOT NULL CHECK (executed_quantity > 0),
    received_at       TEXT NOT NULL
);

CREATE TABLE allocation_messages (
    arrival_seq INTEGER PRIMARY KEY,  -- arrival order: received_at, then file line
    message_id  TEXT NOT NULL,        -- not unique: duplicates are kept
    trade_id    TEXT NOT NULL,
    revision    INTEGER NOT NULL CHECK (revision > 0),
    received_at TEXT NOT NULL
);

CREATE TABLE allocation_lines (
    arrival_seq INTEGER NOT NULL REFERENCES allocation_messages (arrival_seq),
    line_no     INTEGER NOT NULL,
    account     TEXT NOT NULL,
    quantity    INTEGER NOT NULL CHECK (quantity > 0),
    PRIMARY KEY (arrival_seq, line_no)
);
"""

AS_OF_QUERY = """
WITH visible AS (
    SELECT * FROM allocation_messages WHERE received_at <= :as_of
),
first_copies AS (
    SELECT *, ROW_NUMBER() OVER (
        PARTITION BY trade_id, message_id ORDER BY arrival_seq
    ) AS copy_no
    FROM visible
),
in_force AS (
    -- highest revision wins; an equal revision that arrives later does not
    SELECT *, ROW_NUMBER() OVER (
        PARTITION BY trade_id ORDER BY revision DESC, arrival_seq
    ) AS rank_no
    FROM first_copies
    WHERE copy_no = 1
),
allocated AS (
    SELECT m.trade_id, m.arrival_seq, m.message_id, m.revision, m.received_at,
           SUM(l.quantity) AS allocated_quantity
    FROM in_force m
    JOIN allocation_lines l ON l.arrival_seq = m.arrival_seq
    WHERE m.rank_no = 1
    GROUP BY m.trade_id, m.arrival_seq, m.message_id, m.revision, m.received_at
),
trades AS (
    SELECT trade_id FROM executions
    UNION
    SELECT trade_id FROM allocation_messages
)
SELECT t.trade_id,
       CASE WHEN e.received_at <= :as_of THEN e.executed_quantity END,
       a.allocated_quantity,
       a.revision,
       a.message_id,
       a.arrival_seq,
       a.received_at
FROM trades t
LEFT JOIN executions e ON e.trade_id = t.trade_id
LEFT JOIN allocated a ON a.trade_id = t.trade_id
WHERE :trade_id IS NULL OR t.trade_id = :trade_id
ORDER BY t.trade_id
"""

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
