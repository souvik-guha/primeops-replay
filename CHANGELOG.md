# Changelog

## 1.0.0 (2026-11-19)

First release.

- Point-in-time replay of broker executions against client allocation messages
- Revision handling: replace, stale, conflicting and duplicate messages
- `show` and `summary` commands with text or JSON output
- Input validation; a malformed message is rejected without stopping the run
- Tests and CI (ruff, mypy, pytest) on Python 3.10 to 3.13
