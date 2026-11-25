# Changelog

## 1.3.0 (2026-11-25)

### Added
- Fills: a trade can now have several rows in `executions.csv`, one per fill. Executed quantity is the sum of the fills received by the as-of time, so a break can open and close while a block order is still filling
- Average price for each trade, weighted by quantity and rounded half up to 4 decimals, in `show`, `summary`, `--json` and the CSV report (new `fills` and `average_price` columns). `--json` also gives the exact total value as `notional`
- Optional `price` and `fill_id` columns in `executions.csv`. A resent fill counts once and gets a note; the same fill ID with different content stops the load. A file with the original five columns loads as before
- Sample trade T1006: three fills with one resent, and an allocation that arrives before the last fill
- Property-based tests with hypothesis: input order, nothing received after the as-of time, resends, splitting a fill, the exact average price, and the Python and SQL engines agreeing

### Changed
- Prices are held as whole units of 0.0001, so sums and averages are exact in Python and in SQLite
- In `schema.sql` the `executions` table is now `trades` plus `fills`, and the as-of query filters resent fills and sums the rest. A database saved with `--save-db` by an earlier version has the old layout
- Two rows with the same `trade_id` used to stop the load as a duplicate; they are now two fills of one trade
- `Execution` holds a trade's fills; `Execution.single()` builds a trade filled in one piece
- The summary table has Fills and Avg price columns, and `show` prints the fill count and price after the executed quantity
- The "Not covered" section is gone from the README and the project page

### Security and input handling
- A price must be a plain number above zero with at most 4 decimal places: no signs, exponents, `nan` or thousands separators
- Quantity times price is capped for each fill and for each trade, so SQLite's sums can't overflow
- Fills of one trade must have the same symbol and side
- hypothesis pinned to 6.168.5, the newest release that supports Python 3.10

## 1.2.0 (2026-11-19)

### Changed
- The SQLite schema and the as-of query moved out of Python strings into `sql/schema.sql` and `sql/as_of.sql`, so the query can be read and run on its own
- Output is unchanged; the SQL-versus-Python tests pass as before

## 1.1.1 (2026-11-19)

### Added
- Project page at https://souvik-guha.github.io/primeops-replay/ (served from `docs/`)

### Changed
- README intro rewritten
- pytest pinned to 9.0.3; 8.3.3 is affected by PYSEC-2026-1845
- Compound test assertions split so a failure names the exact check

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
