"""Read and validate executions.csv and allocation_events.jsonl.

IDs, symbols and account names are restricted to letters, digits and . _ -
so nothing from the files can be read as a formula when the report is opened
in Excel. Quantities, prices and fill values are capped well below SQLite's
64-bit integer limit, so the SQL engine's sums are exact.
"""

from __future__ import annotations

import csv
import json
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, NamedTuple

from .models import PRICE_SCALE, AllocationEvent, Execution, Fill, Split

HONG_KONG = timezone(timedelta(hours=8), "HKT")
MAX_QUANTITY = 10**12
MAX_FILL_VALUE_E4 = 10**15  # quantity x price for one fill, in units of 0.0001 (100 billion)
MAX_TRADE_VALUE_E4 = 10**18  # the same, summed over a trade's fills
_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}")
_PRICE = re.compile(r"[0-9]{1,9}(?:\.[0-9]{1,4})?")


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
    """One row per fill. Rows with the same trade_id are fills of the same trade.

    Optional columns: price (then every row needs one) and fill_id. Any bad row
    stops the load: executions are the reference side.
    """
    fills: dict[str, list[Fill]] = {}
    terms: dict[str, tuple[str, str]] = {}  # trade_id -> (symbol, side)
    seen_ids: dict[str, tuple[str, str, str, int, int | None]] = {}
    with open(path, newline="", encoding="utf-8-sig") as handle:
        reader = csv.DictReader(handle)
        columns = set(reader.fieldnames or [])
        for line_no, row in enumerate(reader, start=2):
            try:
                trade_id = _identifier(row["trade_id"], "trade_id")
                symbol = _identifier(row["symbol"], "symbol")
                side = str(row["side"]).strip().upper()
                quantity = _quantity(row["executed_quantity"], "executed_quantity")
                received_at = parse_timestamp(str(row["received_at"]))
                price_e4 = _price(row["price"]) if "price" in columns else None
                given_id = _optional_identifier(row.get("fill_id"), "fill_id") if "fill_id" in columns else None
            except KeyError as exc:
                raise InputError(f"{path.name} line {line_no}: missing column {exc}") from None
            except InputError as exc:
                raise InputError(f"{path.name} line {line_no}: {exc}") from None
            label = f"{path.name} line {line_no}"
            if side not in ("BUY", "SELL"):
                raise InputError(f"{label}: side must be BUY or SELL")
            if price_e4 is not None and quantity * price_e4 > MAX_FILL_VALUE_E4:
                raise InputError(f"{label}: quantity x price must stay under {MAX_FILL_VALUE_E4 // PRICE_SCALE:,}")
            if terms.setdefault(trade_id, (symbol, side)) != (symbol, side):
                was_symbol, was_side = terms[trade_id]
                raise InputError(
                    f"{label}: {trade_id} was {was_symbol} {was_side} on an earlier row, not {symbol} {side}"
                )
            fill_id = given_id or f"{trade_id}#{line_no}"  # '#' can't appear in a real ID, so this never collides
            content = (trade_id, symbol, side, quantity, price_e4)
            if given_id and seen_ids.setdefault(given_id, content) != content:
                raise InputError(f"{label}: fill {given_id} appears twice with different content")
            fills.setdefault(trade_id, []).append(Fill(fill_id, quantity, received_at, price_e4, line_no))
    executions = {t: Execution(t, *terms[t], tuple(rows)) for t, rows in fills.items()}
    for execution in executions.values():
        _check_trade_totals(path, execution)
    return executions


def _check_trade_totals(path: Path, execution: Execution) -> None:
    """Keep a trade's total quantity and value inside the limits SQLite can add up exactly."""
    unique = {fill.fill_id: fill for fill in execution.fills}.values()  # a resend counts once
    if sum(f.quantity for f in unique) > MAX_QUANTITY:
        raise InputError(f"{path.name}: the fills of {execution.trade_id} add up to more than {MAX_QUANTITY:,} shares")
    if sum(f.notional_e4 or 0 for f in unique) > MAX_TRADE_VALUE_E4:
        raise InputError(f"{path.name}: the fills of {execution.trade_id} add up to too large a value")


def load_allocation_events(path: Path) -> tuple[list[AllocationEvent], list[Rejection]]:
    """A bad message is rejected and reported, not fatal: one bad message must not hide every other trade."""
    events: list[AllocationEvent] = []
    rejected: list[Rejection] = []
    with open(path, encoding="utf-8-sig") as handle:
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
    if not _ID.fullmatch(text):
        raise InputError(f"{what} must be 1-64 letters, digits, '.', '_' or '-', got {text[:40]!r}")
    return text


def _optional_identifier(value: object, what: str) -> str | None:
    """A blank cell means the file gave no ID."""
    if value is None or not str(value).strip():
        return None
    return _identifier(value, what)


def _price(value: object) -> int:
    """A price as whole units of 0.0001. Plain decimals only: up to 4 decimal places, above zero."""
    text = "" if value is None else str(value).strip()
    if not _PRICE.fullmatch(text):
        raise InputError(f"price must be a positive number with at most 4 decimal places, got {text[:40]!r}")
    whole, _, fraction = text.partition(".")
    price_e4 = int(whole) * PRICE_SCALE + int(fraction.ljust(4, "0"))
    if price_e4 == 0:
        raise InputError(f"price must be above zero, got {text!r}")
    return price_e4


def _quantity(value: object, what: str) -> int:
    text = str(value).strip()
    if isinstance(value, bool) or not (text.isascii() and text.isdigit()):
        raise InputError(f"{what} must be a positive whole number, got {value!r}")
    number = int(text)
    if not 0 < number <= MAX_QUANTITY:
        raise InputError(f"{what} must be between 1 and {MAX_QUANTITY:,}, got {value!r}")
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
