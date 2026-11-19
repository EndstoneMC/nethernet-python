# CLAUDE.md

nethernet is a Python implementation of the NetherNet transport, targeting BDS 1.26.60, behind a thin asyncio facade (`connect`, `serve`, `discover`). It publishes to PyPI as `nethernet-python`; the import name is `nethernet`.

The rules live in `.claude/rules/`; some load only when matching files are touched. `.claude/rules/protocol/` is the clean-room spec: RE agents write it from the binaries, and implementers read only it, the code and public docs (`protocol/cleanroom.md`). Procedures live in `.claude/skills/`.

## Commands

- Install: `uv sync --extra dev`
- Tests: `uv run pytest -q`; one test with `uv run pytest tests/<file>.py::<test>`; the byte-exact subset with `uv run pytest -m conformance`
- Lint: `uv run ruff check`
- Format: `uv run ruff format <files>`; check with `uv run ruff format --check`

Run `/verify` before every commit that touches code, tests, examples or `pyproject.toml`.

## Gotchas

- The dev tools are the `dev` extra, not a dependency group: a plain `uv sync` uninstalls pytest, pytest-xdist and ruff, and the `-n auto` in `addopts` then fails with `unrecognized arguments: -n`.
- pytest runs in parallel; `-n0` runs it serially.
- uv warns that `VIRTUAL_ENV` does not match when a sibling repo's venv is active; it still uses `.venv`.
- ruff is pinned in the `dev` extra and `uv.lock` is committed; bump the pin and rerun `uv lock` together.
- pytest and ruff cache under `.cache/`, set in `pyproject.toml`; pass no cache flags.
- Never copy bedrock-headers content or `.cache/re/` notes into the repo, a commit, an issue or a pull request; they are under NDA.

## Code map

- `src/nethernet/api.py`, `transport_api.py`, `__init__.py`: the public facade (`.claude/rules/api.md`).
- `src/nethernet/constants.py`: the configurable defaults; `errors.py`: the enums and exceptions; `network_id.py`: `NetworkID` and connection ids.
- `src/nethernet/session.py`, `session_manager.py`: session state machines and routing.
- `src/nethernet/transport/`: the aiortc peer connection (`peer_connection.py`), fragmentation (`framing.py`) and the aioice STUN integrity patch (`stun_integrity.py`).
- `src/nethernet/signaling/`: the signaling message codec (`messages.py`) and the HTTP channel (`http.py`).
- `src/nethernet/discovery/`: LAN discovery packets (`packets.py`), the AES-ECB and HMAC envelope (`crypto.py`), and the UDP transport (`lan.py`).
- `src/nethernet/identity.py`: `a=identity` assertions.
- `examples/`: `serve_echo.py`, `connect_echo.py` and `discover_hosts.py`; `tests/` imports each one.
