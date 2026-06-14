# Implementation Plan: NetherNet in Python (aiortc)

## Overview

Build a **cleanroom Python implementation** of the NetherNet P2P/LAN transport defined in
[`SPEC.md`](../SPEC.md), using **[aiortc](https://github.com/aiortc/aiortc)** as the WebRTC
engine (ICE + DTLS + SCTP data channels). The library lets a Python process (e.g. an Endstone
Bedrock server) **advertise a host on the LAN**, **discover hosts**, and **establish P2P
sessions** with NetherNet peers — exchanging application packets over two SCTP data channels
with the exact framing, signaling, discovery, and crypto formats on the wire.

The work splits into three independent, fully-testable **codec cores** (signaling strings,
discovery packets, AES+HMAC envelope) plus a **WebRTC data path** (fragmentation + aiortc),
joined by **session state machines** and a **LAN binding**, and validated by **conformance and
live-interop** tests.

## Source-of-truth policy ("SPEC-first, source-verified")

- **`SPEC.md` is the protocol contract** — the primary source for all wire/observable behavior.
- **`../NetherNet/src` (decompiled reference) is an authoritative reference** for *exact*
  behavior when the spec is ambiguous (e.g. struct packing, buffer ordering). The byte-critical
  paths (crypto envelope, packed discovery headers, signaling tokenizer, fragmentation
  countdown) are already cross-checked and match the spec. Consult it freely for behavioral
  questions; do **not** port its C++ structure/threading model into Python.
- **Framework usage is documentation-verified (source-driven-development).** Any aiortc- or
  `cryptography`-specific API decision is checked against the **official docs** for the pinned
  version and **cited** (full URL in a code comment / PR note). No framework APIs from memory;
  anything unverifiable is flagged `UNVERIFIED`.

## Architecture Decisions

- **asyncio-native.** aiortc is asyncio-based, so the whole stack (LAN UDP via asyncio
  datagram endpoints, sessions, timeouts) is asyncio. A synchronous/thread-bridged convenience
  wrapper is deferred (see Open Questions) — the C++ reference's thread model is *not* ported.
- **Non-trickle ICE by default.** aiortc's `setLocalDescription()` blocks until ICE gathering
  completes, embedding all candidates in the SDP. SPEC §9.3 explicitly allows a non-trickle
  peer that emits **no** `CANDIDATEADD` and still **accepts** inbound `CANDIDATEADD`. We adopt
  that: send the full offer/answer, accept inbound candidates via `addIceCandidate`. This
  removes the hardest aiortc unknown from the critical path.
- **Crypto via `cryptography`** (already an aiortc transitive dep): AES-256-ECB + PKCS7 from
  `hazmat.primitives.ciphers`/`padding`; SHA-256 + HMAC from stdlib `hashlib`/`hmac`.
  Key = `SHA256(appId as 8 LE bytes)`, used as **both** AES key and HMAC key; envelope =
  `HMAC(32) || AES-ECB(PKCS7(plaintext))`; verify in **constant time** (`hmac.compare_digest`).
- **Pluggable signaling channel.** SPEC §5.1 defines an abstract channel. We ship **one**
  concrete binding — the LAN discovery `Message` transport (§7.4). The abstraction keeps an
  online/Realms binding addable later without touching the session core.
- **Package tooling: `uv`** (already installed) + `pyproject.toml`; tests with `pytest` +
  `pytest-asyncio`. Layout `src/nethernet/...` (src-layout).
- **Byte-exactness is the contract.** Every codec has encode↔decode round-trip tests and fixed
  byte fixtures; little-endian everywhere; packed structs built with `struct` (no padding).

## Component dependency graph

```
constants ── errors ── network_id            (leaf foundations, no deps)
     │          │           │
     │          │           ├── signaling/messages ─────────────┐
     │          │           ├── discovery/packets ──────┐        │
     │          │                                        │        │
     └── transport/framing (PacketQueue)                 │        │
                                                          │        │
        discovery/crypto ─────────────────┐              │        │
                                           ▼              ▼        │
                              discovery/lan (LanTransport, UDP)    │
                                           │     implements        │
                              signaling/channel (abstract) ◄───────┤
                                                                   │
        aiortc ── transport/peer_connection (offer/answer,         │
                  data channels, SDP munge, candidate xlate) ◄─────┤
                                           │                       │
                                           ▼                       ▼
                              session (dialer + listener state machines)
                                           │
                              session_manager (route connId → session)
                                           │
                              transport_api (public façade: connect/listen/discover/send/recv)
```

Build order is **bottom-up**, but delivered as **vertical slices** so each phase ends with a
runnable, tested capability rather than a horizontal layer.

## aiortc mapping (key spec → aiortc)

| Spec requirement | aiortc mechanism | Notes / risk |
|---|---|---|
| Dialer creates 2 data channels | `pc.createDataChannel("ReliableDataChannel")`, `pc.createDataChannel("UnreliableDataChannel", ordered=False, maxRetransmits=0)` | Listener uses `@pc.on("datachannel")`, selects by `channel.label`. |
| `a=max-message-size:262144` (§6.1) | aiortc SCTP `maxMessageSize`; **munge SDP** if aiortc's default differs; raise local SCTP limit so a 262144-byte channel message is accepted | **HIGH risk — de-risked by spike (Task 2).** |
| Offer/answer (§5.3) | `createOffer/createAnswer` → `setLocalDescription` → read `pc.localDescription.sdp` | setLocalDescription blocks until gathering complete (non-trickle). |
| `CANDIDATEADD` text (§5.4, §12) | `aiortc.sdp.candidate_from_sdp` / `candidate_to_sdp` | Round-trip the `candidate:` prefix exactly (WebRTC `ToString` includes it; aiortc parser may not). |
| Candidates before remote desc (§5.3) | buffer, then `addIceCandidate` after `setRemoteDescription` | |
| `RelayOnly` → relay-only ICE (§4) | `RTCConfiguration(iceTransportPolicy="relay")` + `iceServers` (TURN) | Other `EConnectionFlags` filters: best-effort / documented gaps. |
| DTLS `setup` role | handled by aiortc via SDP | |

## Task List

### Phase 0 — Scaffold & de-risk

- **Task 1: Project scaffold.** `pyproject.toml` (uv), `src/nethernet/` skeleton with
  `__init__.py`, deps `aiortc` + `cryptography` + dev `pytest`/`pytest-asyncio`, a trivial
  smoke test. *AC:* `uv sync` succeeds; `uv run pytest` green; `python -c "import aiortc"`
  works. *Verify:* `uv run pytest -q`. *Files:* `pyproject.toml`, `src/nethernet/__init__.py`,
  `tests/test_smoke.py`. *Scope: S.*
- **Task 2: aiortc capability spike (SPIKE, throwaway/example).** Two in-process
  `RTCPeerConnection`s, create the two channels with the exact reliability params, connect by
  feeding SDP directly (no signaling layer), send a message each way, send one **262144-byte**
  message on the reliable channel. Record findings: does aiortc emit `max-message-size` and at
  what value; whether a 262144-byte send succeeds or needs munging/`maxMessageSize` override;
  exact candidate string format (`candidate:` prefix?); confirm setLocalDescription blocks
  until gathering complete. *AC:* findings written to `tasks/spike-aiortc.md`; the big message
  either sends or the required workaround is identified. *Verify:* run the spike script.
  *Files:* `examples/spike_aiortc.py`, `tasks/spike-aiortc.md`. *Scope: S.* **Do first — its
  findings parameterize Tasks 7 & 8.**

> **Checkpoint A (after 1–2):** env builds, tests run, the single biggest WebRTC unknown
> (large-message support + candidate format) is resolved on paper. Review before building.

### Phase 1 — Pure codecs (offline, byte-exact; tasks 3–6 parallelizable)

- **Task 3: `NetworkID` & `ConnectionId`.** P2P (`u64` decimal) form, parse order per §3.2
  (decimal → UUID → invalid), `toString`, diagnostic correlation id (§3.4, MSW-first 4×4 hex),
  random `u64` ConnectionId generation (§3.3). *AC:* parse/format round-trips; correlation id
  matches the `<nethernet>WWWW-…` format; ConnectionId is 64-bit and well-distributed.
  *Verify:* `uv run pytest tests/test_network_id.py`. *Files:* `src/nethernet/network_id.py`,
  `tests/test_network_id.py`. *Scope: S.*
- **Task 4: Signaling message codec.** `CONNECTREQUEST/CONNECTRESPONSE/CANDIDATEADD/
  CONNECTERROR` encode + parse; tokenizer splits on **first two spaces only** (§5.2), `data`
  may contain spaces/newlines; reject (return None) on unknown identifier, non-`u64` connId,
  or <3 tokens; `CONNECTERROR` data is decimal `ESessionError`. *AC:* SDP-with-newlines
  survives round-trip; malformed inputs rejected; serialization is exact
  `ID + " " + connId + " " + data`. *Verify:* `uv run pytest tests/test_signaling_messages.py`.
  *Files:* `src/nethernet/errors.py` (ESessionError/ESendType/EConnectionFlags),
  `src/nethernet/signaling/messages.py`, tests. *Scope: M.*
- **Task 5: Discovery packet codec.** Packed base header (20B: len/type/senderId@4/pad@12),
  `Request`(20B), `Response` header(24B)+payload (cap 1148), `Message` header(32B)+
  recipientId@20+len@28+payload (cap 1140); all little-endian via `struct`; `PacketLength`
  computed; parse validates lengths. *AC:* byte fixtures match offsets in §7.2–7.3;
  build↔parse round-trip; payloads truncated to caps as in spec. *Verify:* `uv run pytest
  tests/test_discovery_packets.py`. *Files:* `src/nethernet/discovery/packets.py`, tests.
  *Scope: M.*
- **Task 6: LAN crypto envelope.** `key = SHA256(LE u64 appId)`; `seal` = `HMAC-SHA256(key,P)
  || AES-256-ECB(key, PKCS7(P))`; `open` = length≥32 check, AES-ECB decrypt + PKCS7 unpad,
  recompute HMAC over plaintext, **constant-time** compare, reject on any failure. *AC:*
  seal→open round-trip; tampered MAC/ciphertext rejected; sub-32-byte input rejected; a sealed
  packet decrypts under the reference scheme (vector check). *Verify:* `uv run pytest
  tests/test_crypto_envelope.py`. *Files:* `src/nethernet/discovery/crypto.py`, tests.
  *Scope: M.*

> **Checkpoint B (after 3–6):** all four codecs pass round-trip + fixture tests. Generate a
> sealed discovery packet and a signaling string and diff byte-for-byte against the reference
> (differential test, §11.2). Review before building the data path.

### Phase 2 — WebRTC data path

- **Task 7: Fragmentation / `PacketQueue` (§6.3–6.4).** Sender: `N ≤ 262143` → 1 fragment
  `header=0`; `N > 0x3FBFF01` → reject; non-reliable multi-fragment → drop; reliable
  multi-fragment → countdown headers `fragments-1 … 0`, chunks ≤ `FRAGMENT_SIZE`. Receiver:
  ordered channel reassembles, delivers on `header==0`; unordered strips header, delivers;
  zero-length ignored. FIFO recv queue with `peek`/`read` partial-copy semantics. Built over an
  **injected send callback** + a fed inbound-bytes hook (no aiortc dependency → unit-testable).
  *AC:* fixtures at sizes `1`, `FRAGMENT_SIZE`, `FRAGMENT_SIZE+1`, and a large multi-fragment
  packet reassemble identically; headers count down to 0; oversize/unreliable-multi dropped.
  *Verify:* `uv run pytest tests/test_framing.py`. *Files:* `src/nethernet/transport/
  framing.py`, tests. *Scope: M.*
- **Task 8: aiortc PeerConnection wrapper.** Build offer with the two channels (exact params);
  answer path consuming a remote offer; **SDP max-message-size munge** + SCTP limit per Task 2;
  candidate ↔ `CANDIDATEADD` translation (prefix-exact); `iceServers`/`iceTransportPolicy`
  config incl. `RelayOnly` (§4); `on("datachannel")` routing by label to the framing queues;
  expose ICE connection-state + channel-open events. *AC:* two in-process wrappers wired by
  passing SDP/candidates connect and exchange app packets (incl. one multi-fragment reliable)
  through Task 7's framing; channel selected by exact label. *Verify:* `uv run pytest
  tests/test_peer_connection.py`. *Files:* `src/nethernet/transport/peer_connection.py`, tests.
  *Scope: L → split if it exceeds one session (e.g. 8a SDP/channels, 8b candidate xlate +
  config).* 

> **Checkpoint C (after 7–8):** two in-process peer connections complete a full WebRTC
> handshake (SDP passed by hand) and exchange both reliable (single + multi-fragment) and
> unreliable application packets end-to-end through the framing layer. Review.

### Phase 3 — Session orchestration

- **Task 9: Dialer state machine (§9.1).** open → create PC + 2 channels → CreateOffer →
  SetLocalDescription → emit `CONNECTREQUEST`; on `CONNECTRESPONSE` SetRemoteDescription(answer)
  + flush buffered candidates; on ICE connected → session open; timeouts → close(14/15); on
  `CONNECTERROR` → close(that error). *AC:* drives a mock signaling channel through the dialer
  path; emits exactly the expected messages in order; timeout closes with code 14. *Verify:*
  `uv run pytest tests/test_session_dialer.py`. *Files:* `src/nethernet/session.py` (+ shared
  session base), tests. *Scope: M.*
- **Task 10: Listener state machine (§9.2).** on `CONNECTREQUEST` → create PC (apply flags /
  RelayOnly) → SetRemoteDescription(offer) → CreateAnswer → SetLocalDescription → emit
  `CONNECTRESPONSE`; buffer/flush candidates; channels arrive via `on("datachannel")`; honor
  §5.5 (error 19 must **not** close an incoming session). *AC:* responds to a dialer's offer
  with a well-formed answer; ignores `CONNECTERROR(19)` on the incoming side; opens on ICE
  connected. *Verify:* `uv run pytest tests/test_session_listener.py`. *Files:*
  `src/nethernet/session.py`, tests. *Scope: M.*
- **Task 11: SessionManager + signaling routing.** Map `connId → session`; dispatch inbound
  parsed signals to the right session; create a listener session on an unknown inbound
  `CONNECTREQUEST`; ignore unparseable/unknown messages; surface session open/close + errors.
  *AC:* with the dialer and listener wired through one in-memory channel pair, a full handshake
  completes and app traffic flows both directions. *Verify:* `uv run pytest
  tests/test_session_loopback.py`. *Files:* `src/nethernet/session_manager.py`, tests.
  *Scope: M.*

> **Checkpoint D (after 9–11):** **End-to-end in-process session** — dialer ↔ listener over a
> mock signaling channel: full offer/answer/candidate exchange, both data channels open, and
> application packets (unreliable single + reliable single + reliable multi-fragment) delivered
> intact. This is the core protocol working without any network. Review.

### Phase 4 — LAN binding & public API

- **Task 12: `LanTransport` (§7–§8).** asyncio UDP endpoint bound to the broadcast port
  (default 7551, `SO_REUSEADDR`/`SO_BROADCAST`); send to IPv4 broadcast + IPv6 all-hosts
  link-local; seal/open **every** datagram (Task 6); dispatch by `PacketType`, **ignore own
  `SenderId`**; Request→build Response from app advertisement; Response→deliver discovered host;
  Message→if `RecipientId==self` feed to signaling; peer/address cache for unicast; periodic
  Request broadcast (default 2000 ms). Implements the §5.1 signaling channel by wrapping
  outbound signals in `Message` packets. Must tolerate the **optional first-discovery probe**
  (§12 note): a `Message` with `SenderId = 0` and empty `messageData` — empty payload is ignored
  by signaling parsing and `SenderId = 0` must not be recorded as a new peer (emitting it is
  optional; receiving it must not break). *AC:* two `LanTransport`s over loopback exchange
  Request/Response (discovery) and Message (signaling) successfully; self-packets ignored;
  bad-MAC datagrams dropped; an empty `SenderId=0` Message is dropped without side effects. *Verify:* `uv run pytest tests/test_lan_discovery.py`. *Files:*
  `src/nethernet/discovery/lan.py`, `src/nethernet/signaling/channel.py`, tests. *Scope: L →
  split (12a discovery Request/Response, 12b signaling Message binding + periodic broadcast).* 
- **Task 13: Public Transport API + examples.** Façade tying SessionManager + LanTransport:
  configurable appId/port/interval/negotiation-timeout/iceServers/connection-flags; advertise a
  host (app-supplied advertisement bytes) and answer Requests; `discover()` hosts;
  `connect(remoteId)` → session; accept incoming sessions; `send(type, bytes)` / `recv()` by
  `ESendType`; clean `close()`. Example `host.py` (advertise + accept + echo) and `join.py`
  (discover + connect + send). *AC:* documented API; examples run; config defaults match §10.
  *Verify:* `uv run pytest tests/test_transport_api.py` + manual run of examples on one host.
  *Files:* `src/nethernet/transport_api.py`, `src/nethernet/__init__.py` exports,
  `examples/host.py`, `examples/join.py`, tests. *Scope: M.*

> **Checkpoint E (after 12–13):** **Two Python processes on the same machine/LAN** discover
> each other and complete a real session over actual UDP (discovery + LAN signaling + WebRTC +
> app echo). Review.

### Phase 5 — Conformance & live interop

- **Task 14: Conformance suite (§11).** Codec round-trips with byte-equality assertions;
  fragmentation fixtures (`1`, `FRAGMENT_SIZE`, `FRAGMENT_SIZE+1`, large); a sealed-envelope
  vector proven to open under the reference scheme; differential fixtures (signaling strings,
  discovery builders, AES+HMAC envelope) checked against reference-produced bytes. *AC:* all
  §11.1/§11.2/§11.4 items covered and green. *Verify:* `uv run pytest -q -m conformance`.
  *Files:* `tests/conformance/...`, fixtures. *Scope: M.*
- **Task 15: Live interop (§11.3).** Against a reference NetherNet peer (real Minecraft Bedrock
  on LAN, and/or the `../NetherNet` C++ build): complete a discovery exchange (Request →
  Response) and a full P2P session (offer/answer/candidates → open data channels → application
  traffic). Capture and fix interop deltas (SDP quirks, candidate formatting, DTLS/ICE timing,
  max-message-size). Document results. *AC:* a documented successful LAN discovery + session
  with a non-Python peer (or a precise, logged blocker if the reference peer is unavailable).
  *Verify:* manual interop run + capture; notes in `tasks/interop-results.md`. *Scope: L,
  uncertain — last because it may feed fixes back into Tasks 8/12.*

> **Checkpoint F (Complete):** all acceptance criteria met; conformance green; interop
> validated (or blocker documented). Ready for review/merge.

## Risks and Mitigations

| Risk | Impact | Mitigation |
|---|---|---|
| aiortc won't send a 262144-byte channel message / `max-message-size` mismatch | High | **Spike (Task 2)** before building; munge SDP + raise SCTP `maxMessageSize`; our framing caps each channel message at 262144. |
| Candidate string format mismatch (`candidate:` prefix; mid/mline) | Med | Round-trip via `aiortc.sdp` helpers; assert exact prefix in Task 8; verify in interop (15). |
| ICE/DTLS interop with libwebrtc (Bedrock) fails despite valid SDP | Med | Non-trickle keeps timing simple; isolate in Task 15; consult reference SDP only for ambiguity. |
| Bedrock peer expects trickle timing / early candidates | Med | Spec allows non-trickle; if needed, investigate aiortc candidate events as a follow-up — not on critical path. |
| Windows UDP: dual-stack bind, IPv6 link-local scope, broadcast perms | Med | `SO_REUSEADDR`+`SO_BROADCAST`; separate v4/v6 sockets; per-interface scope ids; test on loopback first (12). |
| AES-256-**ECB** + PKCS7 specifics in `cryptography` | Low | Supported directly; constant-time compare via `hmac.compare_digest`; vector test (6/14). |
| Reference peer unavailable for live interop | Med | Conformance + differential vs `../NetherNet` builders stands alone; document interop as best-effort. |
| aiortc/asyncio integration into a (threaded) Endstone host | Med (future) | Keep API asyncio-native now; thread-bridge wrapper deferred (Open Questions). |

## Resolved Decisions (confirmed by reviewer 2026-06-14)

1. **Scope: LAN-only.** Ship the LAN discovery/signaling binding (§7–§8) only. Expose
   `iceServers`/`RelayOnly` config, but implement **no** online/Realms signaling transport.
2. **Roles: both dialer and listener.** Server needs listener + discovery advertisement; dialer
   enables outbound connect and end-to-end self-testing.
3. **API: asyncio-native only.** No synchronous/threaded façade this round (can be added later).
4. **Minimum Python: 3.11.**

## Verification (pre-implementation gate)

- [ ] Every task has acceptance criteria and a `uv run pytest …` (or manual) verification step
- [ ] Dependencies ordered bottom-up; each phase ends in a working, tested state
- [ ] No task exceeds ~5 files except the two flagged `L` tasks (8, 12), which have split plans
- [ ] Checkpoints A–F sit between phases
- [ ] Highest-risk unknown (aiortc large-message/candidate) is de-risked first (Task 2)
- [ ] Human has reviewed and approved this plan
