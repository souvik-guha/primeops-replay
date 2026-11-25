from datetime import timedelta, timezone

import pytest
from helpers import at, execution, fills, message

from primeops_replay.engine import replay, replay_all
from primeops_replay.models import Status, format_e4, round_average_e4

# The four release criteria, on sample trade T1001.


def test_short_allocation_opens_a_500_share_break(sample):
    result = replay("T1001", at("09:45"), *sample)
    assert result.status is Status.BREAK_SHORT
    assert (result.executed_quantity, result.allocated_quantity, result.difference) == (10_000, 9_500, 500)


def test_correction_resolves_the_break(sample):
    result = replay("T1001", at("09:52"), *sample)
    assert result.status is Status.MATCHED
    assert result.revision_used == 2
    assert dict(result.allocations) == {"ACC-A": 6_000, "ACC-B": 4_000}


def test_duplicate_message_changes_nothing(sample):
    before = replay("T1001", at("09:52"), *sample)
    after = replay("T1001", at("09:53"), *sample)
    assert after.allocated_quantity == before.allocated_quantity == 10_000
    assert after.message_id_used == "M-1002"
    assert "M-1002: duplicate message, ignored" in after.notes


def test_replay_before_the_correction_still_shows_the_break(sample):
    result = replay("T1001", at("09:50"), *sample)
    assert result.status is Status.BREAK_SHORT
    assert result.message_id_used == "M-1001"


# Time


def test_a_message_counts_from_the_exact_second_it_arrives(sample):
    assert replay("T1001", at("09:52"), *sample).revision_used == 2
    assert replay("T1001", at("09:52") - timedelta(seconds=1), *sample).revision_used == 1


def test_execution_before_any_allocation(sample):
    assert replay("T1001", at("09:40"), *sample).status is Status.AWAITING_ALLOCATION
    assert replay("T1001", at("09:39"), *sample).status is Status.NOT_YET_RECEIVED


def test_same_instant_in_utc_gives_the_same_answer(sample):
    assert replay("T1001", at("09:50").astimezone(timezone.utc), *sample).status is Status.BREAK_SHORT


# Revisions


def test_a_revision_replaces_the_split_rather_than_adding_to_it():
    events = [message("M1", 1, "09:31", {"A": 600}), message("M2", 2, "09:32", {"A": 600, "B": 400})]
    result = replay("T1", at("09:35"), execution(), events)
    assert result.allocated_quantity == 1_000
    assert result.status is Status.MATCHED


def test_a_late_lower_revision_is_stale():
    events = [message("M2", 2, "09:31", {"A": 1_000}), message("M1", 1, "09:32", {"A": 400})]
    result = replay("T1", at("09:35"), execution(), events)
    assert result.revision_used == 2
    assert "M1: stale revision 1 arrived after revision 2, ignored" in result.notes


def test_same_revision_twice_keeps_the_first():
    events = [message("M1", 1, "09:31", {"A": 1_000}), message("M9", 1, "09:32", {"A": 700})]
    result = replay("T1", at("09:35"), execution(), events)
    assert result.message_id_used == "M1"
    assert "M9: revision 1 conflicts with M1, ignored" in result.notes


def test_repeated_message_id_with_different_content_is_flagged():
    events = [message("M1", 1, "09:31", {"A": 1_000}), message("M1", 1, "09:32", {"A": 900})]
    result = replay("T1", at("09:35"), execution(), events)
    assert result.allocated_quantity == 1_000
    assert "M1: repeated message ID with different content, ignored" in result.notes


def test_equal_timestamps_follow_file_order_not_list_order():
    first = message("M1", 1, "09:31", {"A": 1_000}, sequence=1)
    second = message("M2", 1, "09:31", {"A": 500}, sequence=2)
    for events in ([first, second], [second, first]):
        assert replay("T1", at("09:35"), execution(), events).message_id_used == "M1"


# Statuses


def test_over_allocation(sample):
    result = replay("T1002", at("11:00"), *sample)
    assert result.status is Status.BREAK_OVER
    assert result.difference == -400


def test_allocation_that_arrives_before_the_execution():
    events = [message("M1", 1, "09:31", {"A": 1_000})]
    result = replay("T1", at("09:35"), execution(hhmm="09:40"), events)
    assert result.status is Status.AWAITING_EXECUTION


def test_unknown_trade():
    assert replay("NOPE", at("09:35"), execution(), []).status is Status.UNKNOWN_TRADE


def test_replay_all_covers_both_files(sample):
    statuses = {r.trade_id: r.status for r in replay_all(at("11:00"), *sample)}
    assert statuses == {
        "T1001": Status.MATCHED,
        "T1002": Status.BREAK_OVER,
        "T1003": Status.MATCHED,
        "T1004": Status.MATCHED,
        "T1005": Status.AWAITING_ALLOCATION,
        "T1006": Status.MATCHED,
    }


# Fills: a trade filled in pieces

THREE_FILLS = fills(
    ("F1", 1_000, "10:40", "74.2"),
    ("F2", 1_500, "10:41", "74.25"),
    ("F3", 500, "10:44", "74.3"),
)


def test_executed_quantity_is_the_sum_of_the_fills_that_had_arrived():
    assert replay("T1", at("10:39"), THREE_FILLS, []).executed_quantity is None
    assert replay("T1", at("10:40"), THREE_FILLS, []).executed_quantity == 1_000
    assert replay("T1", at("10:43"), THREE_FILLS, []).executed_quantity == 2_500
    assert replay("T1", at("10:44"), THREE_FILLS, []).executed_quantity == 3_000


def test_average_price_is_weighted_by_quantity_and_rounded_half_up():
    two = replay("T1", at("10:43"), THREE_FILLS, [])
    assert format_e4(two.average_price_e4) == "74.2300"  # (1000 x 74.20 + 1500 x 74.25) / 2500, exact
    three = replay("T1", at("10:44"), THREE_FILLS, [])
    assert (three.fill_count, three.notional_e4) == (3, 2_227_250_000)
    assert format_e4(three.average_price_e4) == "74.2417"  # 74.24166...


@pytest.mark.parametrize(
    ("notional_e4", "quantity", "expected"),
    [
        (1, 2, 1),  # 0.5 rounds up
        (3, 2, 2),  # 1.5 rounds up
        (5, 4, 1),  # 1.25 rounds down
        (7, 4, 2),  # 1.75 rounds up
        (10, 3, 3),  # 3.33 rounds down
        (11, 3, 4),  # 3.67 rounds up
        (10**18, 10**12, 10**6),  # at the loader's limits
    ],
)
def test_rounding_is_half_up_in_whole_numbers(notional_e4, quantity, expected):
    assert round_average_e4(notional_e4, quantity) == expected


def test_a_break_can_open_and_close_while_fills_are_still_arriving():
    events = [message("M1", 1, "10:42", {"A": 3_000})]
    assert replay("T1", at("10:42"), THREE_FILLS, events).status is Status.BREAK_OVER
    assert replay("T1", at("10:43"), THREE_FILLS, events).difference == -500
    assert replay("T1", at("10:44"), THREE_FILLS, events).status is Status.MATCHED


def test_a_resent_fill_counts_once():
    resent = fills(("F1", 1_000, "10:40", "74.2"), ("F1", 1_000, "10:45", "74.2"))
    before, after = replay("T1", at("10:44"), resent, []), replay("T1", at("10:45"), resent, [])
    assert (after.executed_quantity, after.fill_count, after.notional_e4) == (1_000, 1, before.notional_e4)
    assert after.notes == ("F1: duplicate fill, ignored",)
    assert before.notes == ()


def test_a_repeated_fill_id_with_different_content_is_flagged_and_the_first_stands():
    # The loader refuses this file; the engine still behaves if it is handed the data directly.
    odd = fills(("F1", 1_000, "10:40", "74.2"), ("F1", 2_000, "10:45", "74.2"))
    result = replay("T1", at("10:46"), odd, [])
    assert result.executed_quantity == 1_000
    assert result.notes == ("F1: repeated fill ID with different content, ignored",)


def test_without_prices_there_is_no_average():
    result = replay("T1", at("10:44"), fills(("F1", 1_000, "10:40", None), ("F2", 500, "10:41", None)), [])
    assert (result.executed_quantity, result.fill_count) == (1_500, 2)
    assert (result.average_price_e4, result.notional_e4) == (None, None)


def test_one_fill_without_a_price_withholds_the_average_rather_than_guessing():
    mixed = fills(("F1", 1_000, "10:40", "74.2"), ("F2", 500, "10:41", None))
    result = replay("T1", at("10:44"), mixed, [])
    assert result.executed_quantity == 1_500
    assert result.average_price_e4 is None
    assert replay("T1", at("10:40"), mixed, []).average_price_e4 == 742_000  # before the unpriced fill arrived


def test_fills_at_the_same_second_are_all_counted():
    same = fills(("F1", 100, "10:40", "10"), ("F2", 200, "10:40", "11"))
    result = replay("T1", at("10:40"), same, [])
    assert (result.executed_quantity, result.fill_count) == (300, 2)


def test_allocation_before_the_first_fill_is_still_awaiting_execution():
    events = [message("M1", 1, "10:30", {"A": 3_000})]
    assert replay("T1", at("10:35"), THREE_FILLS, events).status is Status.AWAITING_EXECUTION


def test_a_trade_with_no_fill_yet_has_no_fill_count():
    result = replay("T1", at("10:00"), THREE_FILLS, [])
    assert (result.fill_count, result.notional_e4, result.average_price_e4) == (0, None, None)
    assert result.status is Status.NOT_YET_RECEIVED


def test_fill_order_in_memory_does_not_change_the_answer():
    base = THREE_FILLS["T1"]
    reordered = {"T1": type(base)(base.trade_id, base.symbol, base.side, tuple(reversed(base.fills)))}
    for hhmm in ("10:40", "10:43", "10:44"):
        assert replay("T1", at(hhmm), reordered, []) == replay("T1", at(hhmm), THREE_FILLS, [])


def test_sample_trade_t1006_walks_through_the_fill_rules(sample):
    assert replay("T1006", at("10:42"), *sample).status is Status.BREAK_OVER  # 2,500 filled, 3,000 allocated
    resent = replay("T1006", at("10:43"), *sample)
    assert (resent.executed_quantity, resent.fill_count, resent.notes) == (
        2_500,
        2,
        ("F-1006-2: duplicate fill, ignored",),
    )
    done = replay("T1006", at("10:44"), *sample)
    assert (done.status, done.executed_quantity, format_e4(done.average_price_e4)) == (Status.MATCHED, 3_000, "74.2417")
