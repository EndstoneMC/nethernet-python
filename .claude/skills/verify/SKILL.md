---
name: verify
description: Proves a nethernet change with ruff lint, the ruff format check and the full pytest suite. Use before every commit that touches code, tests, examples or pyproject.toml, after a refactor that must not change behaviour, or when asked to verify, check or test a change.
---

# Verify

Run every step from the repository root, and report each command with its result. On a failure, fix it and start again from step 1.

1. Environment, when `.venv` is missing or `pyproject.toml` changed: `uv sync --extra dev`
2. Lint: `uv run ruff check`
3. Format: `uv run ruff format --check`
4. Tests: `uv run pytest -q`. For a wire change, also report `uv run pytest -q -m conformance` on its own line.

## Symptom → action

| Symptom | Do |
| --- | --- |
| uv warns that `VIRTUAL_ENV` does not match | harmless; uv uses the repository's `.venv` |
| `Would reformat: <file>` | `uv run ruff format <file>`, then rerun from step 1 |
| `unrecognized arguments: -n`, or `pytest` or `ruff` not found | the dev extra is missing: a plain `uv sync` removes pytest-xdist, pytest and ruff; run step 1. With `-p no:xdist` passed by hand, use `-n0` instead |
| a `tests/test_peer_connection.py` loopback test fails after an aiortc upgrade | aiortc changed a behaviour `rules/protocol/transport.md` depends on; check its aiortc mapping against the new release |
| a handshake test times out on Windows with WinError 1214 | aioice gathered on a LAN address against a loopback-only peer (`testing.md`) |
