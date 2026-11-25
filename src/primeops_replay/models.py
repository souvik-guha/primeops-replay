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

# Prices are held as whole units of 0.0001, so sums and averages stay exact in Python and in SQLite.
PRICE_SCALE = 10_000


def round_average_e4(notional_e4: int, quantity: int) -> int:
    """Total value divided by quantity, in units of 0.0001, rounded half up. Whole-number arithmetic only."""
    return (2 * notional_e4 + quantity) // (2 * quantity)


def format_e4(value_e4: int) -> str:
    """4 decimal places, no float involved: 3854400 -> '385.4400'."""
    whole, fraction = divmod(value_e4, PRICE_SCALE)
    return f"{whole}.{fraction:04d}"


@dataclass(frozen=True)
class Fill:
    """One execution report: part of a trade, filled at one price at one time."""

    fill_id: str
    quantity: int
    received_at: datetime
    price_e4: int | None = None  # None if the file has no price column
    sequence: int = 0  # line number in the input file, breaks timestamp ties

    @property
    def notional_e4(self) -> int | None:
        return None if self.price_e4 is None else self.quantity * self.price_e4


@dataclass(frozen=True)
class Execution:
    """A trade as the broker filled it: one or more fills, all in the same symbol and side."""

    trade_id: str
    symbol: str
    side: str
    fills: tuple[Fill, ...]

    @classmethod
    def single(
        cls,
        trade_id: str,
        symbol: str,
        side: str,
        quantity: int,
        received_at: datetime,
        price_e4: int | None = None,
    ) -> Execution:
        """A trade filled in one piece."""
        return cls(trade_id, symbol, side, (Fill(f"{trade_id}-1", quantity, received_at, price_e4, 1),))


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
    fill_count: int = 0  # fills counted by as_of (a resent fill counts once)
    notional_e4: int | None = None  # quantity x price summed over those fills; None without prices

    @property
    def difference(self) -> int | None:
        """Executed minus allocated: positive means unallocated shares, negative over-allocated."""
        if self.executed_quantity is None or self.allocated_quantity is None:
            return None
        return self.executed_quantity - self.allocated_quantity

    @property
    def average_price_e4(self) -> int | None:
        """Quantity-weighted average fill price in units of 0.0001, rounded half up."""
        if self.notional_e4 is None or not self.executed_quantity:
            return None
        return round_average_e4(self.notional_e4, self.executed_quantity)


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
