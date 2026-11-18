# Changelog

## 1.1.0 (2026-11-19)

### Added
- `--engine sql`: the same as-of rules as one SQL query on SQLite, checked against the Python engine at every message time
- `report` command: writes trades that need attention (open breaks, and trades whose latest message was rejected) to a CSV for Excel
- `--save-db` to keep the SQLite database for inspection

### Security and input handling
- IDs, symbols and account names limited to letters, digits and `. _ -`
- Text cells in the CSV report escaped so they can't run as Excel formulas
- Quantities capped at 10^12 to stay inside SQLite's 64-bit integers
- `--save-db` only replaces a file that is already an SQLite database
- CSVs saved from Excel (UTF-8 with a byte-order mark) now load
- CI runs with read-only permissions

### Changed
- Output code moved out of the CLI into `output.py`; statuses are an enum and results are immutable
- A rejected amendment stays flagged until a newer valid revision arrives

## 1.0.0 (2026-11-19)

First release.

- Point-in-time replay of broker executions against client allocation messages
- Revision handling: replace, stale, conflicting and duplicate messages
- `show` and `summary` commands with text or JSON output
- Input validation; a malformed message is rejected without stopping the run
- Tests and CI (ruff, mypy, pytest) on Python 3.10 to 3.13
