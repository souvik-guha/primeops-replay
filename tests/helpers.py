from datetime import datetime, timedelta, timezone
from pathlib import Path

from primeops_replay.models import AllocationEvent, Execution

SAMPLE = Path(__file__).resolve().parents[1] / "data" / "sample"
HKT = timezone(timedelta(hours=8))


def at(hhmm: str) -> datetime:
    """A time on the sample trade date, Hong Kong time."""
    hour, minute = map(int, hhmm.split(":"))
    return datetime(2026, 11, 18, hour, minute, tzinfo=HKT)


def execution(qty: int = 1_000, hhmm: str = "09:30", trade_id: str = "T1") -> dict[str, Execution]:
    return {trade_id: Execution(trade_id, "TEST", "BUY", qty, at(hhmm))}


def message(message_id: str, revision: int, hhmm: str, split: dict[str, int], sequence: int = 0) -> AllocationEvent:
    return AllocationEvent(message_id, "T1", revision, at(hhmm), tuple(split.items()), sequence)
