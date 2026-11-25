"""The SQL engine must agree with the Python engine."""

import random
import sqlite3
from datetime import timedelta, timezone

from helpers import OPEN, at, state

from primeops_replay.engine import replay
from primeops_replay.models import AllocationEvent, Execution, Fill, Status
from primeops_replay.store import build_database, replay_sql


def check_parity(executions, events):
    """Compare every trade at every arrival time and one microsecond either side."""
    conn = build_database(executions, events)
    tick = timedelta(microseconds=1)
    times = {f.received_at for x in executions.values() for f in x.fills} | {e.received_at for e in events}
    instants = sorted({t + d for t in times for d in (-tick, timedelta(0), tick)})
    trade_ids = sorted(set(executions) | {e.trade_id for e in events})
    for as_of in instants:
        sql = {r.trade_id: state(r) for r in replay_sql(conn, as_of)}
        for trade_id in trade_ids:
            assert sql[trade_id] == state(replay(trade_id, as_of, executions, events)), (trade_id, as_of)


def messy_case(rng):
    """Repeated IDs, conflicting and stale revisions, equal timestamps, late executions."""
    executions, events = {}, []
    for t in range(rng.randint(1, 4)):
        trade_id = f"T{t}"
        if rng.random() < 0.85:
            priced = rng.choice(["all", "all", "none", "mixed"])
            pieces = tuple(
                Fill(
                    fill_id=f"F{t}-{rng.randint(0, 3)}",  # a repeated ID is a resend
                    quantity=rng.choice([100, 250, 500]),
                    received_at=OPEN + timedelta(minutes=rng.randint(0, 20)),
                    price_e4=rng.choice([741_000, 742_500, 743_000])
                    if priced == "all" or (priced == "mixed" and rng.random() < 0.5)
                    else None,
                    sequence=n,
                )
                for n in range(1, rng.randint(1, 4) + 1)
            )
            executions[trade_id] = Execution(trade_id, "TEST", "BUY", pieces)
        for _ in range(rng.randint(0, 6)):
            split = tuple((f"ACC-{a}", rng.choice([100, 250, 500])) for a in "AB"[: rng.randint(1, 2)])
            events.append(
                AllocationEvent(
                    message_id=f"M{t}-{rng.randint(0, 3)}",
                    trade_id=trade_id,
                    revision=rng.randint(1, 3),
                    received_at=OPEN + timedelta(minutes=rng.choice([1, 2, 2, 3, 5, 8, 13])),
                    allocations=split,
                    sequence=len(events) + 1,
                )
            )
    return executions, events


def test_sql_matches_python_on_the_sample(sample):
    check_parity(*sample)


def test_sql_matches_python_on_300_random_messy_cases():
    for seed in range(300):
        try:
            check_parity(*messy_case(random.Random(seed)))
        except AssertionError as exc:
            raise AssertionError(f"seed {seed}: {exc}") from None


def test_sql_answers_in_any_timezone(sample):
    conn = build_database(*sample)
    (result,) = replay_sql(conn, at("09:50").astimezone(timezone.utc), "T1001")
    assert result.status is Status.BREAK_SHORT


def test_split_keeps_the_client_order(sample):
    conn = build_database(*sample)
    (result,) = replay_sql(conn, at("11:00"), "T1002")
    assert result.allocations == (("ACC-A", 4_800), ("ACC-C", 3_600))


def test_query_is_valid_outside_sqlite_leniency():
    """Every selected column in a GROUP BY must be grouped or aggregated."""
    from primeops_replay.store import AS_OF_QUERY

    clauses = [part.split("\n", 1)[0] for part in AS_OF_QUERY.split("GROUP BY")[1:]]
    assert len(clauses) == 2  # allocated and executed
    for column in ("m.trade_id", "m.arrival_seq", "m.message_id", "m.revision", "m.received_at"):
        assert column in clauses[0]
    assert clauses[1].strip() == "trade_id"


def test_duplicates_are_stored_not_deleted(sample):
    conn: sqlite3.Connection = build_database(*sample)
    assert conn.execute("SELECT COUNT(*) FROM allocation_messages WHERE message_id = 'M-1002'").fetchone() == (2,)
    assert conn.execute("SELECT COUNT(*) FROM fills WHERE fill_id = 'F-1006-2'").fetchone() == (2,)
