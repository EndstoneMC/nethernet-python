---
paths:
  - "tests/**"
  - "examples/**"
---

# Testing

- `uv run pytest` runs with `-n auto` from `addopts`. Run serially with `-n0`; `-p no:xdist` fails on the inherited `-n`.
- `asyncio_mode = "auto"`: an `async def test_*` needs no marker.
- Name a test for the behaviour it proves, as a lower-case sentence: `test_unauthenticated_error_is_ignored_then_success_resolves`.
- Byte-exact cases carry `pytest.mark.conformance`: codec round trips for signaling strings, discovery packets and sealed envelopes, golden bytes, and fragments at 1, `FRAGMENT_SIZE`, `FRAGMENT_SIZE + 1` and a multi-fragment packet whose headers count down to 0. Run them with `uv run pytest -m conformance`.
- Rule-to-code tracing lives only in each rule file's `paths:` frontmatter; tests carry no comments.
- Conformance goldens are hex literals in `tests/test_conformance.py`, derived from the offset tables in `rules/protocol/`, never computed by re-implementing the codec. The LAN envelope golden is an OpenSSL vector.
- Drive real aiortc and aioice objects over `127.0.0.1`. A double subclasses the real class and overrides only its I/O, as `Harness(ice.StunProtocol)` overrides `send_stun`.
- Python-to-Python over real UDP stands in for a reference peer. Live interop with BDS, a LAN discovery exchange and a full session carrying traffic, runs through endgate's integration suite and endstone's `tests/plugin` runner with `--transport nethernet`.
- On Windows aioice gathers on LAN addresses, and a socket on one cannot send to a peer bound only to `127.0.0.1` (WinError 1214). A test against such a peer gathers on the loopback, as endgate's `harness.py` does.
- A peer built with no ICE servers gathers host candidates only and contacts no STUN server (`protocol/transport.md`).
- Every example exposes `main()` and runs nothing on import; `test_examples_import_and_expose_main` loads each one.
- The loopback tests in `tests/test_peer_connection.py`, from `test_in_process_connect_and_exchange` on, connect two real peers and catch aiortc behaviour that changes on upgrade.
- A machine does not deliver its own UDP broadcast to itself: point `discover_hosts.py` at the host on one machine with `--broadcast-host 127.0.0.1 --broadcast-port <the host's --lan-port>`.
