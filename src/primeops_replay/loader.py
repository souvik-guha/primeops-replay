"""Read and validate executions.csv and allocation_events.jsonl."""

from __future__ import annotations

import csv
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, NamedTuple

from .models import AllocationEvent, Execution, Split

HONG_KONG = timezone(timedelta(hours=8), "HKT")


class InputError(ValueError):
    pass


class Rejection(NamedTuple):
    trade_id: str | None
    note: str
    received_at: datetime | None  # None if the timestamp itself was unreadable


def parse_timestamp(text: str, *, default_tz: timezone | None = None) -> datetime:
    """Parse ISO 8601. Data files must carry an offset; default_tz is for command-line input."""
    value = text.strip()
    if value.endswith("Z"):  # fromisoformat only accepts "Z" from Python 3.11
        value = value[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        raise InputError(f"not an ISO 8601 timestamp: {text!r}") from None
    if parsed.tzinfo is None:
        if default_tz is None:
            raise InputError(f"timestamp has no UTC offset: {text!r}")
        parsed = parsed.replace(tzinfo=default_tz)
    return parsed


def load_executions(path: Path) -> dict[str, Execution]:
    """One row per trade ID. Any bad row stops the load: executions are the reference side."""
    executions: dict[str, Execution] = {}
    with open(path, newline="", encoding="utf-8") as handle:
        for line_no, row in enumerate(csv.DictReader(handle), start=2):
            try:
                execution = Execution(
                    trade_id=_identifier(row["trade_id"], "trade_id"),
                    symbol=_identifier(row["symbol"], "symbol"),
                    side=str(row["side"]).strip().upper(),
                    executed_quantity=_quantity(row["executed_quantity"], "executed_quantity"),
                    received_at=parse_timestamp(str(row["received_at"])),
                )
            except KeyError as exc:
                raise InputError(f"{path.name} line {line_no}: missing column {exc}") from None
            except InputError as exc:
                raise InputError(f"{path.name} line {line_no}: {exc}") from None
            if execution.side not in ("BUY", "SELL"):
                raise InputError(f"{path.name} line {line_no}: side must be BUY or SELL")
            if execution.trade_id in executions:
                raise InputError(f"{path.name} line {line_no}: duplicate trade_id {execution.trade_id}")
            executions[execution.trade_id] = execution
    return executions


def load_allocation_events(path: Path) -> tuple[list[AllocationEvent], list[Rejection]]:
    """A bad message is rejected and reported, not fatal: one bad message must not hide every other trade."""
    events: list[AllocationEvent] = []
    rejected: list[Rejection] = []
    with open(path, encoding="utf-8") as handle:
        for line_no, line in enumerate(handle, start=1):
            if not line.strip():
                continue
            label = f"{path.name} line {line_no}"
            raw: Any = None
            try:
                raw = json.loads(line)
                if not isinstance(raw, dict):
                    raise InputError("each line must be a JSON object")
                message_id = _identifier(raw["message_id"], "message_id")
                label = f"message {message_id}"
                events.append(
                    AllocationEvent(
                        message_id=message_id,
                        trade_id=_identifier(raw["trade_id"], "trade_id"),
                        revision=_quantity(raw["revision"], "revision"),
                        received_at=parse_timestamp(str(raw["received_at"])),
                        allocations=_split(raw["allocations"]),
                        sequence=line_no,
                    )
                )
            except json.JSONDecodeError:
                rejected.append(Rejection(None, f"{label}: rejected, not valid JSON", None))
            except KeyError as exc:
                rejected.append(Rejection(_trade_of(raw), f"{label}: rejected, missing field {exc}", _time_of(raw)))
            except InputError as exc:
                rejected.append(Rejection(_trade_of(raw), f"{label}: rejected, {exc}", _time_of(raw)))
    return events, rejected


def _identifier(value: object, what: str) -> str:
    text = str(value).strip() if value is not None else ""
    if not text:
        raise InputError(f"{what} is missing")
    return text


def _quantity(value: object, what: str) -> int:
    text = str(value).strip()
    if isinstance(value, bool) or not (text.isascii() and text.isdigit()):
        raise InputError(f"{what} must be a positive whole number, got {value!r}")
    number = int(text)
    if number <= 0:
        raise InputError(f"{what} must be a positive whole number, got {value!r}")
    return number


def _split(raw: object) -> Split:
    if not isinstance(raw, list) or not raw:
        raise InputError("allocations must be a non-empty list")
    split: list[tuple[str, int]] = []
    for item in raw:
        if not isinstance(item, dict):
            raise InputError("each allocation needs an account and a quantity")
        account = _identifier(item.get("account"), "account")
        if any(account == seen for seen, _ in split):
            raise InputError(f"account {account} appears twice in one split")
        split.append((account, _quantity(item.get("quantity"), f"quantity for {account}")))
    return tuple(split)


def _trade_of(raw: Any) -> str | None:
    if not isinstance(raw, dict):
        return None
    try:
        return _identifier(raw.get("trade_id"), "trade_id")
    except InputError:
        return None


def _time_of(raw: Any) -> datetime | None:
    if not isinstance(raw, dict):
        return None
    try:
        return parse_timestamp(str(raw["received_at"]))
    except (KeyError, InputError):
        return None
