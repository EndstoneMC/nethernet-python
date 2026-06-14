# NetherNet (Python / aiortc) — Task List

Cleanroom from [`SPEC.md`](../SPEC.md). Consult `../NetherNet/src` only to resolve ambiguity.
Verification command base: `uv run pytest -q`. Tackle phases top-to-bottom; tasks within
Phase 1 (3–6) may be parallelized.

## Phase 0 — Scaffold & de-risk
- [x] **1. Project scaffold** — `pyproject.toml` (uv), `src/nethernet/` skeleton, deps
  `aiortc`+`cryptography`+`pytest`/`pytest-asyncio`, smoke test. *(aiortc 1.14.0,
  cryptography 49.0.0, pytest 9.1.0, pytest-asyncio 1.4.0)*
  - [x] `uv sync` ok · `import aiortc` ok · `uv run pytest` green (3 passed)
- [x] **2. aiortc spike (SPIKE)** — 2 in-process PCs, exact channel params, send a
  **262144-byte** reliable message; record max-message-size behavior, candidate format
  (`candidate:` prefix), non-trickle confirmation → `tasks/spike-aiortc.md`.
  - [x] Findings written · **large message sends OK** after munging SDP to
    `max-message-size:262144` (aiortc default 65536); non-trickle confirmed; candidate helpers
    need `candidate:` prefix stripped on parse / prepended on emit

### ▣ Checkpoint A — env builds, biggest WebRTC unknown resolved → review

## Phase 1 — Pure codecs (byte-exact; 3–6 parallelizable)
- [x] **3. NetworkID & ConnectionId** (§3) — P2P u64 decimal, parse order (decimal→UUID→
  invalid), correlation id (§3.4), random connId (§3.3).
  - [x] round-trip parse/format · correlation id format · 64-bit random connId (14 tests)
- [x] **4. Signaling message codec** (§5) — 4 messages; first-two-spaces tokenizer; reject
  unknown id / non-u64 connId / <3 tokens; `errors.py` enums.
  - [x] SDP-with-newlines round-trips · malformed rejected · exact serialization (17 tests)
- [x] **5. Discovery packet codec** (§7.2–7.3) — packed 20/24/32-byte headers, LE via
  `struct`, payload caps 1148/1140, computed `PacketLength`.
  - [x] byte fixtures match offsets · build↔parse round-trip (13 tests)
- [x] **6. LAN crypto envelope** (§8) — key=SHA256(LE u64 appId); seal=HMAC||AES-256-ECB
  (PKCS7); open w/ constant-time compare + reject rules.
  - [x] seal→open round-trip · tamper/short rejected · **byte-exact match vs independent
    OpenSSL golden vector** (9 tests)

### ▣ Checkpoint B — codecs green (56 tests, ruff clean); seal matches OpenSSL vector ✓ → pushed

## Phase 2 — WebRTC data path
- [x] **7. Fragmentation / PacketQueue** (§6.3–6.4) — countdown headers; `>0x3FBFF01` reject;
  unreliable-multi drop; reassembly on `header==0`; FIFO peek/read; zero-length ignored.
  - [x] fixtures `1` / `FRAGMENT_SIZE` / `FRAGMENT_SIZE+1` / 3-fragment reassemble · headers
    count to 0 (12 tests)
- [x] **8. aiortc PeerConnection wrapper** (§6.1–6.2, §4) — 2 channels exact params; SDP
  max-message-size munge; candidate↔CANDIDATEADD (prefix-exact); iceServers/RelayOnly
  (best-effort: aiortc has no relay-only transport policy); `on("datachannel")` by label.
  - [x] 2 in-process wrappers exchange app packets (incl. 262144-byte multi-fragment) via
    framing · channel chosen by exact label (7 tests)

### ▣ Checkpoint C — in-process WebRTC handshake + framed app traffic (75 tests) ✓ → pushed

## Phase 3 — Session orchestration
- [x] **9. Dialer state machine** (§9.1) — offer→CONNECTREQUEST; CONNECTRESPONSE→setRemote+
  flush candidates; ICE connected→open; timeouts 14/15; CONNECTERROR→close.
  - [x] emits expected messages in order · timeout closes with 14 · candidate buffering ·
    error 19 closes dialer (§5.5) (6 tests)
- [x] **10. Listener state machine** (§9.2) — CONNECTREQUEST→answer→CONNECTRESPONSE; candidate
  buffering; channels via on-datachannel; §5.5 error-19 nuance.
  - [x] well-formed answer · ignores CONNECTERROR(19) incoming · timeout 15 · candidate
    buffering (5 tests)
- [x] **11. SessionManager + routing** — connId→session map; dispatch inbound; create listener
  on unknown CONNECTREQUEST; ignore unknown/unparseable.
  - [x] full handshake over one in-memory channel pair; traffic both ways (2 tests)

### ▣ Checkpoint D — end-to-end in-process session (mock signaling), all packet kinds (88 tests) ✓ → pushed

## Phase 4 — LAN binding & public API
- [x] **12. LanTransport** (§7–§8) — asyncio UDP (v4 broadcast; v6 link-local deferred),
  seal/open every datagram, dispatch by type, ignore own SenderId, peer cache, periodic
  Request; implements signaling channel via Message packets; tolerates the §12 empty probe.
  - [x] real-UDP loopback Message exchange + logic tests: Request/Response, self ignored,
    bad-MAC dropped, unicast vs broadcast (12 tests). Added `constants.py` (§10 defaults).
- [x] **13. Public Transport API + examples** — config (appId/port/interval/timeout/
  iceServers/flags); advertise+answer; `discover()`; `connect()`; accept incoming; `send`/
  `recv` by ESendType; `aclose()`; `examples/host.py`, `examples/join.py`.
  - [x] API documented + exported · examples written · defaults match §10 · **trickle-ICE fix**
    (strip candidates from SDP → CANDIDATEADD) so signaling fits the 1140-byte cap (3 tests)

### ▣ Checkpoint E — two Transports connect over real UDP end-to-end (103 tests) ✓ → pushed

## Phase 5 — Conformance & live interop
- [x] **14. Conformance suite** (§11.1/.2/.4) — codec round-trips byte-equal; fragmentation
  fixtures; sealed-envelope vector opens under reference; differential vs hand-derived golden
  byte vectors.
  - [x] 11 conformance tests green (`uv run pytest -m conformance`); golden hex for
    signaling/discovery/envelope
- [~] **15. Live interop** (§11.3) — **BLOCKED: needs an external reference peer.** Requires
  real Minecraft Bedrock on the LAN (or a custom C++ driver — the `../NetherNet` project builds
  a static lib, not a runnable peer). Harness + instructions in `tasks/interop-results.md`.
  - [x] interop harness documented; Python↔Python over real UDP verified as a stand-in
  - [ ] live run against a non-Python reference peer (needs user / hardware)

### ▣ Checkpoint F — Complete pending live interop (Task 15); all else green (114 tests)

## Phase 6 — Public API rework (websockets-style, mirrors BDS) — see `docs/api-design.md`
Mirror BDS's `Connector`/`NetworkPeer` shape: module-level `connect()`/`serve()`/`discover()`
returning a single `Connection` + `Server`; `Transport` demoted to internal engine. Keep the
114 protocol tests green; add `tests/test_api.py`.
- [ ] **16. Public exceptions** — `NetherNetError`, `ConnectionFailed`, `ConnectionClosed`
  (each carrying `ESessionError`) in `errors.py`.
- [ ] **17. Engine hooks** — `LanTransport`: `broadcast_port` (default = `port`); pass source
  `Address` to `on_host_discovered`. `Transport`: `on_session_close` passthrough; thread
  `broadcast_port`; carry addr in discovery. Update the one affected LAN test.
- [ ] **18. `api.py`** — `Connection` (await send/recv/`async for`/close/wait_closed), `Server`
  (serve_forever/aclose), `DiscoveredHost`, and `connect()`/`serve()`/`discover()` over an
  internal `_Endpoint` wrapping `Transport`.
- [ ] **19. Exports** — `__init__.py` exports the new surface; drop `Transport` from public.
- [ ] **20. Examples** — `examples/server.py` + `examples/client.py`; remove `host.py`/`join.py`.

### ▣ Checkpoint G — public async API (connect/serve/Connection) green; examples run

---

## Resolved decisions (confirmed 2026-06-14)
- [x] Scope: **LAN-only** (no online/Realms signaling transport; iceServers/RelayOnly config exposed)
- [x] Roles: **both dialer + listener**
- [x] API: **asyncio-native only** (no sync wrapper this round)
- [x] Minimum Python: **3.11**
