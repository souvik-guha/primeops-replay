import json

import pytest

from primeops_replay.loader import (
    MAX_QUANTITY,
    InputError,
    load_allocation_events,
    load_executions,
    parse_timestamp,
)

HEADER = "trade_id,symbol,side,executed_quantity,received_at\n"
GOOD = {
    "message_id": "M1",
    "trade_id": "T1",
    "revision": 1,
    "received_at": "2026-11-18T09:31:00+08:00",
    "allocations": [{"account": "A", "quantity": 100}],
}


def write(tmp_path, name, text, encoding="utf-8"):
    path = tmp_path / name
    path.write_text(text, encoding=encoding)
    return path


def load_messages(tmp_path, *messages):
    lines = "\n".join(m if isinstance(m, str) else json.dumps(m) for m in messages)
    return load_allocation_events(write(tmp_path, "allocation_events.jsonl", lines))


def test_data_timestamps_need_an_offset():
    with pytest.raises(InputError, match="no UTC offset"):
        parse_timestamp("2026-11-18T09:30:00")


def test_z_suffix_works_on_python_310():
    assert parse_timestamp("2026-11-18T01:30:00Z").utcoffset().total_seconds() == 0


def test_duplicate_execution_row_is_an_error(tmp_path):
    rows = HEADER + "T1,X,BUY,100,2026-11-18T09:30:00+08:00\n" * 2
    with pytest.raises(InputError, match="duplicate trade_id T1"):
        load_executions(write(tmp_path, "executions.csv", rows))


@pytest.mark.parametrize("qty", ["0", "-5", "12.5", "abc", str(MAX_QUANTITY + 1)])
def test_bad_execution_quantity(tmp_path, qty):
    with pytest.raises(InputError, match="executed_quantity"):
        load_executions(write(tmp_path, "executions.csv", HEADER + f"T1,X,BUY,{qty},2026-11-18T09:30:00+08:00\n"))


def test_csv_saved_by_excel_with_a_byte_order_mark_loads(tmp_path):
    path = write(tmp_path, "executions.csv", HEADER + "T1,X,BUY,100,2026-11-18T09:30:00+08:00\n", "utf-8-sig")
    assert load_executions(path)["T1"].executed_quantity == 100


@pytest.mark.parametrize("trade_id", ["=HYPERLINK(1)", "+1", "-1", "@x", "T 1", ""])
def test_unsafe_ids_are_refused(tmp_path, trade_id):
    with pytest.raises(InputError, match="trade_id"):
        load_executions(write(tmp_path, "executions.csv", HEADER + f"{trade_id},X,BUY,100,2026-11-18T09:30:00+08:00\n"))


def test_a_bad_message_is_rejected_and_the_rest_still_load(tmp_path):
    bad = dict(GOOD, message_id="M2", revision=2, allocations=[{"account": "A", "quantity": -1}])
    events, rejected = load_messages(tmp_path, GOOD, bad, "{not json")
    assert [e.message_id for e in events] == ["M1"]
    assert rejected[0].trade_id == "T1" and rejected[0].note.startswith("message M2: rejected")
    assert rejected[1].trade_id is None and "not valid JSON" in rejected[1].note


def test_account_with_formula_characters_is_rejected(tmp_path):
    events, rejected = load_messages(tmp_path, dict(GOOD, allocations=[{"account": "=1+1", "quantity": 100}]))
    assert events == [] and "account must be" in rejected[0].note


def test_account_listed_twice_in_one_split(tmp_path):
    split = [{"account": "A", "quantity": 100}, {"account": "A", "quantity": 50}]
    events, rejected = load_messages(tmp_path, dict(GOOD, allocations=split))
    assert events == [] and "appears twice" in rejected[0].note


def test_quantity_too_large_for_sqlite_is_rejected(tmp_path):
    events, rejected = load_messages(tmp_path, dict(GOOD, allocations=[{"account": "A", "quantity": 2**70}]))
    assert events == [] and "between 1 and" in rejected[0].note
