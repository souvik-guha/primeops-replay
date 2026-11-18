import pytest
from helpers import SAMPLE

from primeops_replay.loader import load_allocation_events, load_executions
from primeops_replay.models import AllocationEvent, Execution


@pytest.fixture(scope="session")
def sample() -> tuple[dict[str, Execution], list[AllocationEvent]]:
    executions = load_executions(SAMPLE / "executions.csv")
    events, _ = load_allocation_events(SAMPLE / "allocation_events.jsonl")
    return executions, events
