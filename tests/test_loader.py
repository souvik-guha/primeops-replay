import json

import pytest

from primeops_replay.loader import (
    MAX_FILL_VALUE_E4,
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


@pytest.mark.parametrize("qty", ["0", "-5", "12.5", "abc", str(MAX_QUANTITY + 1)])
def test_bad_execution_quantity(tmp_path, qty):
    with pytest.raises(InputError, match="executed_quantity"):
        load_executions(write(tmp_path, "executions.csv", HEADER + f"T1,X,BUY,{qty},2026-11-18T09:30:00+08:00\n"))


def test_csv_saved_by_excel_with_a_byte_order_mark_loads(tmp_path):
    path = write(tmp_path, "executions.csv", HEADER + "T1,X,BUY,100,2026-11-18T09:30:00+08:00\n", "utf-8-sig")
    assert load_executions(path)["T1"].fills[0].quantity == 100


@pytest.mark.parametrize("trade_id", ["=HYPERLINK(1)", "+1", "-1", "@x", "T 1", ""])
def test_unsafe_ids_are_refused(tmp_path, trade_id):
    with pytest.raises(InputError, match="trade_id"):
        load_executions(write(tmp_path, "executions.csv", HEADER + f"{trade_id},X,BUY,100,2026-11-18T09:30:00+08:00\n"))


def test_a_bad_message_is_rejected_and_the_rest_still_load(tmp_path):
    bad = dict(GOOD, message_id="M2", revision=2, allocations=[{"account": "A", "quantity": -1}])
    events, rejected = load_messages(tmp_path, GOOD, bad, "{not json")
    assert [e.message_id for e in events] == ["M1"]
    assert rejected[0].trade_id == "T1"
    assert rejected[0].note.startswith("message M2: rejected")
    assert rejected[1].trade_id is None
    assert "not valid JSON" in rejected[1].note


def test_account_with_formula_characters_is_rejected(tmp_path):
    events, rejected = load_messages(tmp_path, dict(GOOD, allocations=[{"account": "=1+1", "quantity": 100}]))
    assert events == []
    assert "account must be" in rejected[0].note


def test_account_listed_twice_in_one_split(tmp_path):
    split = [{"account": "A", "quantity": 100}, {"account": "A", "quantity": 50}]
    events, rejected = load_messages(tmp_path, dict(GOOD, allocations=split))
    assert events == []
    assert "appears twice" in rejected[0].note


def test_quantity_too_large_for_sqlite_is_rejected(tmp_path):
    events, rejected = load_messages(tmp_path, dict(GOOD, allocations=[{"account": "A", "quantity": 2**70}]))
    assert events == []
    assert "between 1 and" in rejected[0].note


# Fills: several rows with the same trade_id, an optional price and an optional fill_id

FILL_HEADER = "trade_id,symbol,side,executed_quantity,received_at,fill_id,price\n"


def load_fills(tmp_path, *rows):
    return load_executions(write(tmp_path, "executions.csv", FILL_HEADER + "\n".join(rows) + "\n"))


def test_rows_with_the_same_trade_id_are_fills_of_one_trade(tmp_path):
    executions = load_fills(
        tmp_path,
        "T1,0941.HK,BUY,1000,2026-11-18T10:40:00+08:00,F1,74.200",
        "T1,0941.HK,BUY,1500,2026-11-18T10:41:00+08:00,F2,74.25",
        "T2,0005.HK,SELL,100,2026-11-18T10:42:00+08:00,F3,61.85",
    )
    assert sorted(executions) == ["T1", "T2"]
    assert [(f.fill_id, f.quantity, f.price_e4, f.sequence) for f in executions["T1"].fills] == [
        ("F1", 1_000, 742_000, 2),
        ("F2", 1_500, 742_500, 3),
    ]
    assert (executions["T1"].symbol, executions["T1"].side) == ("0941.HK", "BUY")


def test_the_old_file_format_still_loads(tmp_path):
    (execution,) = load_executions(
        write(tmp_path, "executions.csv", HEADER + "T1,X,BUY,100,2026-11-18T09:30:00+08:00\n")
    ).values()
    (fill,) = execution.fills
    assert (fill.quantity, fill.price_e4) == (100, None)
    assert fill.fill_id == "T1#2"  # made up from the line; a real ID can't contain '#'


def test_a_blank_fill_id_cell_means_no_id_was_given(tmp_path):
    executions = load_fills(
        tmp_path,
        "T1,X,BUY,100,2026-11-18T09:30:00+08:00,,10",
        "T1,X,BUY,100,2026-11-18T09:30:00+08:00,,10",
    )
    assert len({f.fill_id for f in executions["T1"].fills}) == 2  # two separate fills, not a resend


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("74", 740_000),
        ("74.2", 742_000),
        ("74.25", 742_500),
        ("0.0001", 1),
        ("385.4000", 3_854_000),
        ("999999999.9999", 9_999_999_999_999),
    ],
)
def test_prices_are_read_exactly(tmp_path, text, expected):
    fill = load_fills(tmp_path, f"T1,X,BUY,1,2026-11-18T09:30:00+08:00,F1,{text}")["T1"].fills[0]
    assert fill.price_e4 == expected


@pytest.mark.parametrize(
    "price",
    ["", "0", "0.0000", "-1", "+1", "1.00001", "1e3", '"1,000"', "abc", "nan", "inf", "1.", ".5", "١٢٣", "1234567890"],
)
def test_bad_prices_are_refused(tmp_path, price):
    with pytest.raises(InputError, match="price"):
        load_fills(tmp_path, f"T1,X,BUY,100,2026-11-18T09:30:00+08:00,F1,{price}")


def test_a_price_column_means_every_row_needs_a_price(tmp_path):
    with pytest.raises(InputError, match="line 3: price"):
        load_fills(
            tmp_path,
            "T1,X,BUY,100,2026-11-18T09:30:00+08:00,F1,10",
            "T1,X,BUY,100,2026-11-18T09:31:00+08:00,F2,",
        )


def test_fills_of_one_trade_must_agree_on_symbol_and_side(tmp_path):
    with pytest.raises(InputError, match="line 3: T1 was X BUY"):
        load_fills(
            tmp_path,
            "T1,X,BUY,100,2026-11-18T09:30:00+08:00,F1,10",
            "T1,X,SELL,100,2026-11-18T09:31:00+08:00,F2,10",
        )
    with pytest.raises(InputError, match="line 3: T1 was X BUY"):
        load_fills(
            tmp_path,
            "T1,X,BUY,100,2026-11-18T09:30:00+08:00,F1,10",
            "T1,Y,BUY,100,2026-11-18T09:31:00+08:00,F2,10",
        )


def test_a_resent_fill_is_kept_so_the_engine_can_count_it_once(tmp_path):
    executions = load_fills(
        tmp_path,
        "T1,X,BUY,100,2026-11-18T09:30:00+08:00,F1,10",
        "T1,X,BUY,100,2026-11-18T09:35:00+08:00,F1,10",
    )
    assert [f.fill_id for f in executions["T1"].fills] == ["F1", "F1"]


@pytest.mark.parametrize(
    "second",
    [
        "T1,X,BUY,200,2026-11-18T09:35:00+08:00,F1,10",  # quantity differs
        "T1,X,BUY,100,2026-11-18T09:35:00+08:00,F1,11",  # price differs
        "T2,X,BUY,100,2026-11-18T09:35:00+08:00,F1,10",  # same ID on another trade
    ],
)
def test_a_fill_id_reused_with_different_content_stops_the_load(tmp_path, second):
    with pytest.raises(InputError, match="line 3: fill F1 appears twice with different content"):
        load_fills(tmp_path, "T1,X,BUY,100,2026-11-18T09:30:00+08:00,F1,10", second)


@pytest.mark.parametrize("fill_id", ["=1+1", "F 1", "-F"])
def test_unsafe_fill_ids_are_refused(tmp_path, fill_id):
    with pytest.raises(InputError, match="fill_id"):
        load_fills(tmp_path, f"T1,X,BUY,100,2026-11-18T09:30:00+08:00,{fill_id},10")


def test_one_fill_worth_too_much_is_refused(tmp_path):
    with pytest.raises(InputError, match="quantity x price must stay under 100,000,000,000"):
        load_fills(
            tmp_path,
            f"T1,X,BUY,{MAX_QUANTITY},2026-11-18T09:30:00+08:00,F1,{MAX_FILL_VALUE_E4 // MAX_QUANTITY // 10_000 + 1}",
        )


def test_a_trade_cannot_add_up_to_more_shares_than_allocations_allow(tmp_path):
    half = MAX_QUANTITY // 2 + 1
    with pytest.raises(InputError, match="add up to more than"):
        load_fills(
            tmp_path,
            f"T1,X,BUY,{half},2026-11-18T09:30:00+08:00,F1,0.0001",
            f"T1,X,BUY,{half},2026-11-18T09:31:00+08:00,F2,0.0001",
        )
    # a resend of the same fill is not extra shares
    load_fills(
        tmp_path,
        f"T1,X,BUY,{half},2026-11-18T09:30:00+08:00,F1,0.0001",
        f"T1,X,BUY,{half},2026-11-18T09:31:00+08:00,F1,0.0001",
    )
