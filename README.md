# PrimeOps Replay

[![tests](https://github.com/souvik-guha/primeops-replay/actions/workflows/ci.yml/badge.svg)](https://github.com/souvik-guha/primeops-replay/actions/workflows/ci.yml)

PrimeOps Replay checks whether a client's trade allocations add up to the quantity the broker executed, and shows what an analyst could have seen at any point in the day.

When a broker fills a block order, the client sends instructions splitting the shares across its accounts. Those instructions get corrected, resent and sometimes arrive out of order. If the split doesn't match the fill, there's a break to chase before settlement. I built this to work through the rules for deciding which instruction is in force at a given moment, and to be able to answer "what did we know at 09:50?" rather than only "what's true now?".

It is a reconciler, not a prime brokerage system. All data in the repo is synthetic.

## Example

The broker buys 10,000 Tencent (0700.HK) shares for a client:

| Time  | Message | Result |
|-------|---------|--------|
| 09:40 | Execution: 10,000 shares | `AWAITING_ALLOCATION` |
| 09:45 | Allocation rev 1: ACC-A 6,000, ACC-B 3,500 | `BREAK_SHORT`, 500 unallocated |
| 09:52 | Allocation rev 2: ACC-A 6,000, ACC-B 4,000 | `MATCHED` |
| 09:53 | Rev 2 again (duplicate) | still `MATCHED` |

Asked as of 09:50, it still shows the 500-share break, because revision 2 hadn't arrived yet.

## Running it

Python 3.10 or later. No dependencies beyond the standard library; the dev extras add pytest, ruff and mypy.

```bash
git clone https://github.com/souvik-guha/primeops-replay.git
cd primeops-replay
python -m pip install -e ".[dev]"
```

The break, as of 09:50:

```bash
primeops-replay show --trade-id T1001 --as-of 2026-11-18T09:50:00+08:00
```

```
Trade T1001  0700.HK BUY  as of 2026-11-18T09:50:00+08:00
  Status      BREAK_SHORT
  Executed    10,000
  Allocated   9,500  (revision 1, message M-1001)
    ACC-A      6,000
    ACC-B      3,500
  Difference  500 unallocated
```

Resolved, as of 09:53:

```bash
primeops-replay show --trade-id T1001 --as-of 2026-11-18T09:53:00+08:00
```

```
Trade T1001  0700.HK BUY  as of 2026-11-18T09:53:00+08:00
  Status      MATCHED
  Executed    10,000
  Allocated   10,000  (revision 2, message M-1002)
    ACC-A      6,000
    ACC-B      4,000
  Difference  0
  Notes
    - M-1002: duplicate message, ignored
```

All trades:

```bash
primeops-replay summary --as-of 2026-11-18T11:00:00+08:00
```

```
As of 2026-11-18T11:00:00+08:00
  Trade   Status                 Executed  Allocated    Diff
  T1001   MATCHED                  10,000     10,000       0
  T1002   BREAK_OVER                8,000      8,400    -400
  T1003   MATCHED                   5,000      5,000       0
  T1004   MATCHED                   2,000      2,000       0
  T1005   AWAITING_ALLOCATION       3,000          -       -
  message M-4002: rejected, quantity for ACC-A must be a positive whole number, got -500
```

Add `--json` for JSON output. A time without an offset (`2026-11-18T09:50`) is taken as Hong Kong time.

## Rules

1. Only messages received at or before the as-of time count, including one received at exactly that time.
2. Messages are read in the order they arrived; if two share a timestamp, file order decides.
3. A repeated message ID counts once. If the repeat has different content, it is flagged.
4. A higher revision replaces the whole split; it is never added on top. A lower revision that turns up later is stale and ignored. Two different messages with the same revision conflict, and the first one stands.
5. A malformed message is rejected with a reason and the previous valid revision stays in force.
6. The input files are never modified, so replaying an earlier time always gives the same answer.

Statuses: `AWAITING_ALLOCATION`, `BREAK_SHORT` (under-allocated), `BREAK_OVER` (over-allocated), `MATCHED`, `AWAITING_EXECUTION` (allocation came first), `NOT_YET_RECEIVED`, `UNKNOWN_TRADE` (exit code 2).

## Input files

`data/sample/executions.csv`, one row per trade:

```
trade_id,symbol,side,executed_quantity,received_at
T1001,0700.HK,BUY,10000,2026-11-18T09:40:00+08:00
```

`data/sample/allocation_events.jsonl`, one message per line, each with the full split:

```json
{"message_id": "M-1001", "trade_id": "T1001", "revision": 1, "received_at": "2026-11-18T09:45:00+08:00",
 "allocations": [{"account": "ACC-A", "quantity": 6000}, {"account": "ACC-B", "quantity": 3500}]}
```

The sample has one trade per case:

- **T1001:** short allocation, correction and duplicate.
- **T1002:** over-allocation.
- **T1003:** stale revision 1 arriving after revision 2.
- **T1004:** malformed revision 2, so revision 1 stays in force.
- **T1005:** executed but not yet allocated.

Quantities follow each stock's Hong Kong board lot.

## Development

```bash
python -m pytest
ruff check . && ruff format --check .
mypy
```

The tests cover:

- the four scenario checks above;
- each rule, including the exact boundary second and timezones;
- input validation and the CLI.

CI runs all of it on Python 3.10 to 3.13.

```
src/primeops_replay/
  engine.py   reconciliation rules
  loader.py   reading and validating input
  models.py   records and statuses
  output.py   text and JSON output
  cli.py      command line
```

## Changes

See [CHANGELOG.md](CHANGELOG.md).

## Not covered

Settlement (T+2 in Hong Kong), margin, stock borrow and custodian statement matching. This tells you the allocation matched, not that the trade settled.

## License

MIT
