-- Every trade's state as of :as_of (or one trade, if :trade_id is set).
--   visible:      messages received by the as-of time
--   first_copies: keep the first copy of each message ID, drop resends
--   in_force:     the highest revision; an equal revision that arrives later loses
--   allocated:    the total of the split in force

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
