from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum


class Status(str, Enum):
    UNKNOWN_TRADE = "UNKNOWN_TRADE"  # trade ID is in neither file
    NOT_YET_RECEIVED = "NOT_YET_RECEIVED"  # nothing had arrived by as_of
    AWAITING_EXECUTION = "AWAITING_EXECUTION"  # allocation arrived before the execution
    AWAITING_ALLOCATION = "AWAITING_ALLOCATION"
    BREAK_SHORT = "BREAK_SHORT"  # fewer shares allocated than executed
    BREAK_OVER = "BREAK_OVER"  # more shares allocated than executed
    MATCHED = "MATCHED"

    def __str__(self) -> str:
        return self.value


BREAKS = (Status.BREAK_SHORT, Status.BREAK_OVER)

Split = tuple[tuple[str, int], ...]  # ((account, quantity), ...)


@dataclass(frozen=True)
class Execution:
    trade_id: str
    symbol: str
    side: str
    executed_quantity: int
    received_at: datetime


@dataclass(frozen=True)
class AllocationEvent:
    """One allocation message. It always carries the full split across accounts."""

    message_id: str
    trade_id: str
    revision: int
    received_at: datetime
    allocations: Split
    sequence: int = 0  # line number in the input file, breaks timestamp ties

    @property
    def allocated_quantity(self) -> int:
        return sum(quantity for _, quantity in self.allocations)


@dataclass(frozen=True)
class ReconResult:
    trade_id: str
    as_of: datetime
    status: Status
    executed_quantity: int | None = None
    allocated_quantity: int | None = None
    allocations: Split = ()
    revision_used: int | None = None
    message_id_used: str | None = None
    used_received_at: datetime | None = None  # when the instruction in force arrived
    notes: tuple[str, ...] = ()

    @property
    def difference(self) -> int | None:
        """Executed minus allocated: positive means unallocated shares, negative over-allocated."""
        if self.executed_quantity is None or self.allocated_quantity is None:
            return None
        return self.executed_quantity - self.allocated_quantity


def status_for(executed: int | None, allocated: int | None, *, trade_known: bool) -> Status:
    """The status rule. Both the Python and SQL engines use this."""
    if not trade_known:
        return Status.UNKNOWN_TRADE
    if executed is None:
        return Status.NOT_YET_RECEIVED if allocated is None else Status.AWAITING_EXECUTION
    if allocated is None:
        return Status.AWAITING_ALLOCATION
    if allocated < executed:
        return Status.BREAK_SHORT
    if allocated > executed:
        return Status.BREAK_OVER
    return Status.MATCHED
