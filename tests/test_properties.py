"""Property-based tests (hypothesis).

The example tests check named cases. These check things that must hold for any
input the generators can build: arrival order in memory must not matter, nothing
received after the as-of time may leak in, a resend must change nothing, and the
average price must equal the exact fraction. Both engines are used where it fits.
"""

import dataclasses
import math
import tempfile
from datetime import timedelta, timezone
from decimal import Decimal
from fractions import Fraction
from pathlib import Path

from helpers import OPEN, state
from hypothesis import given
from hypothesis import strategies as st

from primeops_replay.engine import replay, replay_all
from primeops_replay.loader import InputError, _price, load_executions
from primeops_replay.models import BREAKS, AllocationEvent, Execution, Fill, Status
from primeops_replay.store import build_database, replay_sql

QUANTITIES = [100, 250, 500, 1_000]
PRICES_E4 = [741_000, 742_500, 743_000, 750_000]
as_of_seconds = st.integers(
    min_value=-60, max_value=17 * 60
)  # from a minute before the first record to past the last resend


def moment(seconds: int):
    return OPEN + timedelta(seconds=seconds)


@st.composite
def cases(draw, resends: bool = True):
    """Trades with fills and allocation messages that arrive in awkward ways.

    Times fall on a short grid so ties and exact-boundary arrivals are common. A resent
    fill repeats the original's content later, as the loader requires; messages may repeat
    an ID with different content, since the loader allows that.
    """
    price_mode = draw(
        st.sampled_from(["none", "all", "mixed"])
    )  # "mixed" never comes from a file, but the engines must cope
    executions: dict[str, Execution] = {}
    events: list[AllocationEvent] = []
    fill_line = message_line = 0
    for t in range(draw(st.integers(1, 3))):
        trade_id = f"T{t}"
        pieces: list[Fill] = []
        for k in range(draw(st.integers(0, 4))):
            fill_line += 1
            pieces.append(
                Fill(
                    fill_id=f"F{t}-{k}",
                    quantity=draw(st.sampled_from(QUANTITIES)),
                    received_at=moment(60 * draw(st.integers(0, 12))),
                    price_e4=draw(st.sampled_from(PRICES_E4))
                    if price_mode == "all" or (price_mode == "mixed" and draw(st.booleans()))
                    else None,
                    sequence=fill_line,
                )
            )
        if resends:
            for original in list(pieces):
                if draw(st.booleans()):
                    fill_line += 1
                    later = moment(int((original.received_at - OPEN).total_seconds()) + 60 * draw(st.integers(0, 4)))
                    pieces.append(dataclasses.replace(original, received_at=later, sequence=fill_line))
        if pieces:
            executions[trade_id] = Execution(trade_id, "TEST", "BUY", tuple(pieces))
        for _ in range(draw(st.integers(0, 5))):
            message_line += 1
            accounts = draw(st.lists(st.sampled_from("ABC"), min_size=1, max_size=2, unique=True))
            events.append(
                AllocationEvent(
                    message_id=f"M{t}-{draw(st.integers(0, 3))}",
                    trade_id=trade_id,
                    revision=draw(st.integers(1, 3)),
                    received_at=moment(60 * draw(st.integers(0, 12))),
                    allocations=tuple(
                        (f"ACC-{a}", draw(st.sampled_from([100, 250, 500, 1_000, 1_500]))) for a in accounts
                    ),
                    sequence=message_line,
                )
            )
    return executions, events


def by_trade(results):
    return {r.trade_id: r for r in results}


@given(cases(), as_of_seconds)
def test_python_and_sql_engines_agree(case, seconds):
    executions, events = case
    conn = build_database(executions, events)
    sql = by_trade(replay_sql(conn, moment(seconds)))
    for result in replay_all(moment(seconds), executions, events):
        assert state(sql[result.trade_id]) == state(result)


@given(st.data(), cases(), as_of_seconds)
def test_the_order_records_sit_in_memory_does_not_matter(data, case, seconds):
    """Arrival order comes from the timestamps and file lines, not from list order."""
    executions, events = case
    shuffled_events = data.draw(st.permutations(events))
    shuffled_executions = {
        trade_id: dataclasses.replace(x, fills=tuple(data.draw(st.permutations(list(x.fills)))))
        for trade_id, x in executions.items()
    }
    expected = replay_all(moment(seconds), executions, events)
    assert replay_all(moment(seconds), shuffled_executions, shuffled_events) == expected
    sql_before = replay_sql(build_database(executions, events), moment(seconds))
    sql_after = replay_sql(build_database(shuffled_executions, shuffled_events), moment(seconds))
    assert [state(r) for r in sql_after] == [state(r) for r in sql_before]


@given(cases(), as_of_seconds)
def test_nothing_received_after_the_as_of_time_can_change_the_answer(case, seconds):
    executions, events = case
    as_of = moment(seconds)
    kept_events = [e for e in events if e.received_at <= as_of]
    kept_executions = {
        trade_id: dataclasses.replace(x, fills=kept)
        for trade_id, x in executions.items()
        if (kept := tuple(f for f in x.fills if f.received_at <= as_of))
    }
    full = by_trade(replay_all(as_of, executions, events))
    for trade_id, result in by_trade(replay_all(as_of, kept_executions, kept_events)).items():
        assert result == full[trade_id]


@given(st.data(), cases(), as_of_seconds)
def test_a_resend_changes_nothing_except_the_notes(data, case, seconds):
    executions, events = case
    next_fill = max((f.sequence for x in executions.values() for f in x.fills), default=0)
    next_message = max((e.sequence for e in events), default=0)
    more_events = list(events)
    for e in events:
        if data.draw(st.booleans()):
            next_message += 1
            later = e.received_at + timedelta(seconds=data.draw(st.integers(0, 300)))
            more_events.append(dataclasses.replace(e, received_at=later, sequence=next_message))
    more_executions = {}
    for trade_id, x in executions.items():
        copies = []
        for f in x.fills:
            if data.draw(st.booleans()):
                next_fill += 1
                later = f.received_at + timedelta(seconds=data.draw(st.integers(0, 300)))
                copies.append(dataclasses.replace(f, received_at=later, sequence=next_fill))
        more_executions[trade_id] = dataclasses.replace(x, fills=x.fills + tuple(copies))
    quiet = {r.trade_id: dataclasses.replace(r, notes=()) for r in replay_all(moment(seconds), executions, events)}
    after = replay_all(moment(seconds), more_executions, more_events)
    for result in after:
        assert dataclasses.replace(result, notes=()) == quiet[result.trade_id]


@given(cases(resends=False), as_of_seconds)
def test_splitting_a_fill_in_two_changes_neither_quantity_nor_value(case, seconds):
    executions, events = case
    split_executions = {}
    for trade_id, x in executions.items():
        pieces = []
        for f in x.fills:
            half = f.quantity // 2
            pieces += [
                dataclasses.replace(f, fill_id=f.fill_id + "a", quantity=half),
                dataclasses.replace(f, fill_id=f.fill_id + "b", quantity=f.quantity - half),
            ]
        split_executions[trade_id] = dataclasses.replace(x, fills=tuple(pieces))
    before = by_trade(replay_all(moment(seconds), executions, events))
    for result in replay_all(moment(seconds), split_executions, events):
        original = before[result.trade_id]
        assert (result.executed_quantity, result.notional_e4, result.average_price_e4) == (
            original.executed_quantity,
            original.notional_e4,
            original.average_price_e4,
        )
        assert result.status == original.status
        assert result.fill_count == 2 * original.fill_count


@given(st.lists(st.tuples(st.integers(1, 10**9), st.integers(1, 10**9)), min_size=1, max_size=8))
def test_average_price_is_the_exact_fraction_rounded_half_up(pairs):
    """Checked against Fraction arithmetic, a different route from the whole-number rounding in the code."""
    fills = tuple(Fill(f"F{n}", q, OPEN, p, n) for n, (q, p) in enumerate(pairs, start=1))
    result = replay("T", OPEN, {"T": Execution("T", "TEST", "BUY", fills)}, [])
    total_quantity = sum(q for q, _ in pairs)
    total_value = sum(q * p for q, p in pairs)
    assert (result.executed_quantity, result.notional_e4) == (total_quantity, total_value)
    assert result.average_price_e4 == math.floor(Fraction(total_value, total_quantity) + Fraction(1, 2))
    prices = [p for _, p in pairs]
    assert min(prices) <= result.average_price_e4 <= max(prices)  # an average can't leave the range of its prices


@given(cases(), st.lists(as_of_seconds, min_size=2, max_size=6))
def test_fills_only_ever_add_as_time_passes(case, instants):
    executions, events = case
    seen: dict[str, tuple[int, int]] = {}
    for seconds in sorted(instants):
        for result in replay_all(moment(seconds), executions, events):
            now = (result.executed_quantity or 0, result.fill_count)
            before = seen.get(result.trade_id, (0, 0))
            assert now[0] >= before[0]
            assert now[1] >= before[1]
            seen[result.trade_id] = now


@given(
    cases(),
    as_of_seconds,
    st.sampled_from([timezone.utc, timezone(timedelta(hours=-5)), timezone(timedelta(hours=5, minutes=30))]),
)
def test_the_same_instant_gives_the_same_answer_in_any_timezone(case, seconds, zone):
    executions, events = case
    in_hong_kong = replay_all(moment(seconds), executions, events)
    elsewhere = replay_all(moment(seconds).astimezone(zone), executions, events)
    assert [dataclasses.replace(r, as_of=OPEN) for r in elsewhere] == [
        dataclasses.replace(r, as_of=OPEN) for r in in_hong_kong
    ]
    conn = build_database(executions, events)
    assert [state(r) for r in replay_sql(conn, moment(seconds).astimezone(zone))] == [state(r) for r in in_hong_kong]


@given(cases(), as_of_seconds)
def test_executed_quantity_matches_a_plain_restatement_of_the_rules(case, seconds):
    """Each fill ID counts once, from its first arrival, and only if it had arrived by the as-of time."""
    executions, events = case
    as_of = moment(seconds)
    for result in replay_all(as_of, executions, events):
        first_copy: dict[str, Fill] = {}
        for f in sorted(
            executions[result.trade_id].fills if result.trade_id in executions else (),
            key=lambda f: (f.received_at, f.sequence),
        ):
            if f.received_at <= as_of:
                first_copy.setdefault(f.fill_id, f)
        assert result.executed_quantity == (sum(f.quantity for f in first_copy.values()) or None)
        assert result.fill_count == len(first_copy)


@given(cases(), as_of_seconds)
def test_status_always_agrees_with_the_numbers(case, seconds):
    executions, events = case
    for r in replay_all(moment(seconds), executions, events):
        if r.executed_quantity is not None and r.allocated_quantity is not None:
            assert r.difference == r.executed_quantity - r.allocated_quantity
            expected = (
                Status.MATCHED if r.difference == 0 else Status.BREAK_SHORT if r.difference > 0 else Status.BREAK_OVER
            )
            assert r.status is expected
        if r.status in BREAKS:
            assert r.difference not in (None, 0)
        if r.allocated_quantity is not None:
            assert r.allocated_quantity == sum(q for _, q in r.allocations)


@given(
    st.lists(
        st.tuples(
            st.integers(1, 10_000),
            st.decimals(min_value=Decimal("0.0001"), max_value=Decimal("9999999.9999"), places=4),
        ),
        min_size=1,
        max_size=6,
    )
)
def test_prices_read_from_a_file_are_exact(rows):
    lines = ["trade_id,symbol,side,executed_quantity,received_at,fill_id,price"]
    for n, (quantity, price) in enumerate(rows, start=1):
        lines.append(f"T1,X,BUY,{quantity},2026-11-18T09:30:00+08:00,F{n},{price:f}")
    with tempfile.TemporaryDirectory() as folder:
        path = Path(folder) / "executions.csv"
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        loaded = load_executions(path)["T1"].fills
    assert [f.price_e4 for f in loaded] == [int(price * 10_000) for _, price in rows]
    assert [f.quantity for f in loaded] == [quantity for quantity, _ in rows]


# Arbitrary text rarely looks like a number, so half the examples are shaped like one (extra digits or decimals).
@given(st.one_of(st.text(max_size=24), st.from_regex(r"[0-9]{0,11}(\.[0-9]{0,6})?", fullmatch=True)))
def test_a_price_is_either_a_positive_whole_number_of_ticks_or_a_clean_refusal(text):
    try:
        value = _price(text)
    except InputError:
        return
    assert value >= 1
    assert Decimal(text.strip()) * 10_000 == value  # only plain decimals get through, and exactly
