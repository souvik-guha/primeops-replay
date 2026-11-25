import pytest
from helpers import SAMPLE
from hypothesis import HealthCheck, settings

from primeops_replay.loader import load_allocation_events, load_executions
from primeops_replay.models import AllocationEvent, Execution


@pytest.fixture(scope="session")
def sample() -> tuple[dict[str, Execution], list[AllocationEvent]]:
    executions = load_executions(SAMPLE / "executions.csv")
    events, _ = load_allocation_events(SAMPLE / "allocation_events.jsonl")
    return executions, events


# Properties build whole data sets and run both engines, so allow time and take more examples than the default.
settings.register_profile("replay", max_examples=200, deadline=None, suppress_health_check=[HealthCheck.too_slow])
settings.load_profile("replay")
