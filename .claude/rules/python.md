---
paths:
  - "**/*.py"
---

# Python

## Architecture

- asyncio-native: LAN UDP on asyncio datagram endpoints, sessions and timeouts on one loop. No threads, no locks, no synchronous facade, no blocking I/O inside a coroutine.
- aiortc and aioice are the WebRTC engine. `protocol/transport.md` maps the rules onto them.
- The session core takes a pluggable signaling channel. LAN and HTTP are its two bindings, and a new one is added without touching the core (`protocol/signaling.md`).
- Crypto comes from `cryptography` and the stdlib `hashlib`/`hmac`; compare MACs with `hmac.compare_digest`.
- Byte-exactness is the contract: every codec has round-trip tests and golden bytes (`testing.md`), and packs wire structs with `struct` and an explicit `<` byte order.
- `uv` and `pyproject.toml`, src layout under `src/nethernet/`.

## Style

- Target Python 3.11, the `requires-python` floor: no `type` statements, PEP 695 generics or other 3.12+ syntax.
- Start every non-`__init__` module under `src/nethernet/` with `from __future__ import annotations`.
- Type-hint every signature. There is no type checker; ruff (`E`, `W`, `F`, `I`, `N`, `UP`, `B`, line length 100) is the only gate.
- PEP 8 names everywhere, enum members in `UPPER_SNAKE_CASE`, enum classes with no C++ `E` prefix.
- Wait on a `Future`, `Event` or `Queue`; never poll with `while ...: await asyncio.sleep(...)`.
- Configuration arguments are keyword-only.
- A closing coroutine is `close` or `aclose`, and the object is an async context manager.
- Check an aiortc, aioice, aiohttp or cryptography API against its official reference for the installed version, never from memory.
- Patch aioice or aiortc only under `transport/`, behind a feature check that skips the patch once the library fixes it, as `stun_integrity.install` checks `stun.Message.check_message_integrity`.
- Public failures raise a `NetherNetError` subclass carrying `.error: SessionError`. A receive path drops what the rules say to drop and returns `None` or `False`, never raises.
