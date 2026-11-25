from datetime import datetime, timedelta, timezone
from pathlib import Path

from primeops_replay.models import AllocationEvent, Execution, Fill

SAMPLE = Path(__file__).resolve().parents[1] / "data" / "sample"
HKT = timezone(timedelta(hours=8))


OPEN = datetime(2026, 11, 18, 9, 30, tzinfo=HKT)


def state(result):
    """What the two engines must agree on."""
    return (
        result.trade_id,
        result.status,
        result.executed_quantity,
        result.fill_count,
        result.notional_e4,
        result.allocated_quantity,
        result.revision_used,
        result.message_id_used,
        result.allocations,
    )


def at(hhmm: str) -> datetime:
    """A time on the sample trade date, Hong Kong time."""
    hour, minute = map(int, hhmm.split(":"))
    return datetime(2026, 11, 18, hour, minute, tzinfo=HKT)


def execution(qty: int = 1_000, hhmm: str = "09:30", trade_id: str = "T1") -> dict[str, Execution]:
    return {trade_id: Execution.single(trade_id, "TEST", "BUY", qty, at(hhmm))}


def fills(*rows: tuple[str, int, str, str | None], trade_id: str = "T1") -> dict[str, Execution]:
    """A trade filled in pieces. Rows are (fill_id, quantity, 'HH:MM', price or None), in file order."""
    made = tuple(
        Fill(fill_id, qty, at(hhmm), None if price is None else price_e4(price), sequence)
        for sequence, (fill_id, qty, hhmm, price) in enumerate(rows, start=1)
    )
    return {trade_id: Execution(trade_id, "TEST", "BUY", made)}


def price_e4(text: str) -> int:
    """'74.25' -> 742500"""
    whole, _, fraction = text.partition(".")
    return int(whole) * 10_000 + int(fraction.ljust(4, "0") or 0)


def message(message_id: str, revision: int, hhmm: str, split: dict[str, int], sequence: int = 0) -> AllocationEvent:
    return AllocationEvent(message_id, "T1", revision, at(hhmm), tuple(split.items()), sequence)
