from datetime import timedelta, timezone

from helpers import at, execution, message

from primeops_replay.engine import replay, replay_all
from primeops_replay.models import Status

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
    }
