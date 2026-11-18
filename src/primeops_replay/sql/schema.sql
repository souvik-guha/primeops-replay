-- Tables for the SQL engine. Timestamps are fixed-width UTC text,
-- so comparing the text compares the time.

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
