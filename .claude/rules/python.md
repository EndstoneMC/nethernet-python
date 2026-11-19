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
- Type-hint every signature. There is no type checker; ruff (`E`, `W`, `F`, `I`, `N`, `UP`, `B`, `D`, line length 100) is the only gate.
- PEP 8 names everywhere, enum members in `UPPER_SNAKE_CASE`, enum classes with no C++ `E` prefix.
- Wait on a `Future`, `Event` or `Queue`; never poll with `while ...: await asyncio.sleep(...)`.
- Configuration arguments are keyword-only.
- A closing coroutine is `close` or `aclose`, and the object is an async context manager.
- Check an aiortc, aioice, aiohttp or cryptography API against its official reference for the installed version, never from memory.
- Patch aioice or aiortc only under `transport/`, behind a feature check that skips the patch once the library fixes it, as `stun_integrity.install` checks `stun.Message.check_message_integrity`.
- Public failures raise a `NetherNetError` subclass carrying `.error: SessionError | None`, plus the builtins `TimeoutError`, `OSError` and `socket.gaierror` from `serve` and `connect`. A receive path drops what the rules say to drop and returns `None` or `False`, never raises.

## Docstrings

- Every docstring is Google style (pyguide 3.8), enforced by ruff `D` with `convention = "google"`.
- Use triple double quotes. The summary is one physical line ending in a period, in imperative mood: `Open a session.`
- Leave a blank line between the summary and the body, and align the body with the opening quotes.
- Add `Args:`, `Returns:` (`Yields:` for a generator), `Raises:` and `Attributes:` only when they say something the signature does not; omit an empty or restating section.
- Write each entry as `name: description`, indented four spaces under its heading. Continuation lines indent four more.
- Give no types in `Args:`; the signature is annotated.
- List `*args` and `**kwargs` with their stars.
- `Raises:` names only exceptions the interface promises, not those from API misuse.
- A module docstring is a one-line summary, and only exported modules carry one.
- A class docstring says what an instance represents. Public attributes that are not properties go under `Attributes:`.
- Document `__init__` arguments in the class docstring under `Args:`, and give `__init__` no docstring.
- A property docstring reads like an attribute: `The local peer id.`
- An `@override` method has no docstring.
- Ruff checks the form of every docstring that exists; review checks that every exported name has one.
