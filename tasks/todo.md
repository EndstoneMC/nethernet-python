# NetherNet (Python / aiortc) — Task List

Cleanroom from [`SPEC.md`](../SPEC.md). Consult `../NetherNet/src` only to resolve ambiguity.
Verification command base: `uv run pytest -q`. Tackle phases top-to-bottom; tasks within
Phase 1 (3–6) may be parallelized.

## Phase 0 — Scaffold & de-risk
- [ ] **1. Project scaffold** — `pyproject.toml` (uv), `src/nethernet/` skeleton, deps
  `aiortc`+`cryptography`+`pytest`/`pytest-asyncio`, smoke test.
  - [ ] `uv sync` ok · `import aiortc` ok · `uv run pytest` green
- [ ] **2. aiortc spike (SPIKE)** — 2 in-process PCs, exact channel params, send a
  **262144-byte** reliable message; record max-message-size behavior, candidate format
  (`candidate:` prefix), non-trickle confirmation → `tasks/spike-aiortc.md`.
  - [ ] Findings written · large message sends or workaround identified

### ▣ Checkpoint A — env builds, biggest WebRTC unknown resolved → review

## Phase 1 — Pure codecs (byte-exact; 3–6 parallelizable)
- [ ] **3. NetworkID & ConnectionId** (§3) — P2P u64 decimal, parse order (decimal→UUID→
  invalid), correlation id (§3.4), random connId (§3.3).
  - [ ] round-trip parse/format · correlation id format · 64-bit random connId
- [ ] **4. Signaling message codec** (§5) — 4 messages; first-two-spaces tokenizer; reject
  unknown id / non-u64 connId / <3 tokens; `errors.py` enums.
  - [ ] SDP-with-newlines round-trips · malformed rejected · exact serialization
- [ ] **5. Discovery packet codec** (§7.2–7.3) — packed 20/24/32-byte headers, LE via
  `struct`, payload caps 1148/1140, computed `PacketLength`.
  - [ ] byte fixtures match offsets · build↔parse round-trip
- [ ] **6. LAN crypto envelope** (§8) — key=SHA256(LE u64 appId); seal=HMAC||AES-256-ECB
  (PKCS7); open w/ constant-time compare + reject rules.
  - [ ] seal→open round-trip · tamper/short rejected · reference vector opens

### ▣ Checkpoint B — codecs green; differential byte-diff vs reference → review

## Phase 2 — WebRTC data path
- [ ] **7. Fragmentation / PacketQueue** (§6.3–6.4) — countdown headers; `>0x3FBFF01` reject;
  unreliable-multi drop; reassembly on `header==0`; FIFO peek/read; zero-length ignored.
  - [ ] fixtures `1` / `FRAGMENT_SIZE` / `FRAGMENT_SIZE+1` / large reassemble · headers
    count to 0
- [ ] **8. aiortc PeerConnection wrapper** (§6.1–6.2, §4) — 2 channels exact params; SDP
  max-message-size munge; candidate↔CANDIDATEADD (prefix-exact); iceServers/RelayOnly;
  `on("datachannel")` by label. *(L — split 8a channels/SDP, 8b candidate xlate+config if
  needed.)*
  - [ ] 2 in-process wrappers exchange app packets (incl. multi-fragment) via framing ·
    channel chosen by exact label

### ▣ Checkpoint C — in-process full WebRTC handshake + framed app traffic → review

## Phase 3 — Session orchestration
- [ ] **9. Dialer state machine** (§9.1) — offer→CONNECTREQUEST; CONNECTRESPONSE→setRemote+
  flush candidates; ICE connected→open; timeouts 14/15; CONNECTERROR→close.
  - [ ] emits expected messages in order · timeout closes with 14
- [ ] **10. Listener state machine** (§9.2) — CONNECTREQUEST→answer→CONNECTRESPONSE; candidate
  buffering; channels via on-datachannel; §5.5 error-19 nuance.
  - [ ] well-formed answer · ignores CONNECTERROR(19) incoming · opens on ICE connected
- [ ] **11. SessionManager + routing** — connId→session map; dispatch inbound; create listener
  on unknown CONNECTREQUEST; ignore unknown/unparseable.
  - [ ] full handshake over one in-memory channel pair; traffic both ways

### ▣ Checkpoint D — end-to-end in-process session (mock signaling), all packet kinds → review

## Phase 4 — LAN binding & public API
- [ ] **12. LanTransport** (§7–§8) — asyncio UDP, v4 broadcast + v6 link-local, seal/open
  every datagram, dispatch by type, ignore own SenderId, peer cache, periodic Request;
  implements signaling channel via Message packets. *(L — split 12a discovery, 12b
  signaling+broadcast.)*
  - [ ] two instances over loopback do Request/Response + Message · self ignored · bad-MAC
    dropped
- [ ] **13. Public Transport API + examples** — config (appId/port/interval/timeout/
  iceServers/flags); advertise+answer; `discover()`; `connect()`; accept incoming; `send`/
  `recv` by ESendType; `close()`; `examples/host.py`, `examples/join.py`.
  - [ ] API documented · examples run · defaults match §10

### ▣ Checkpoint E — two Python processes connect over real LAN UDP end-to-end → review

## Phase 5 — Conformance & live interop
- [ ] **14. Conformance suite** (§11.1/.2/.4) — codec round-trips byte-equal; fragmentation
  fixtures; sealed-envelope vector opens under reference; differential vs reference builders.
  - [ ] all conformance items green (`uv run pytest -q -m conformance`)
- [ ] **15. Live interop** (§11.3) — real Bedrock and/or `../NetherNet` build: discovery
  Request→Response + full session → data channels → app traffic; fix & document deltas.
  - [ ] documented successful interop (or precise logged blocker) in
    `tasks/interop-results.md`

### ▣ Checkpoint F — Complete: conformance green, interop validated, ready for review

---

## Resolved decisions (confirmed 2026-06-14)
- [x] Scope: **LAN-only** (no online/Realms signaling transport; iceServers/RelayOnly config exposed)
- [x] Roles: **both dialer + listener**
- [x] API: **asyncio-native only** (no sync wrapper this round)
- [x] Minimum Python: **3.11**
