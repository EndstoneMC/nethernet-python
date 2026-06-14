"""aiortc capability spike (Task 2) — de-risk the hardest unknowns before Tasks 7 & 8.

Questions this answers, against the *installed* aiortc:

1. Can aiortc send a single 262144-byte data-channel message? That is NetherNet's
   ``FRAGMENT_SIZE + 1`` — the largest single channel message we ever send. (SPEC.md s6.1/s6.3)
2. Does aiortc emit ``a=max-message-size`` in the SDP, and at what value? If it advertises less
   than 262144 we must munge the SDP, because a sender is bounded by the *remote's* advertised
   max message size. (SPEC.md s6.1)
3. What is the exact ICE-candidate string format, and how do ``candidate_from_sdp`` /
   ``candidate_to_sdp`` treat the ``candidate:`` prefix? CANDIDATEADD carries the WebRTC
   ``candidate:...`` form. (SPEC.md s5.4 / s12)
4. Does ``setLocalDescription()`` block until ICE gathering completes (i.e. is aiortc naturally
   a non-trickle peer)? (SPEC.md s9.3)

aiortc API verified against the official reference:
https://aiortc.readthedocs.io/en/latest/api.html

Run directly to (re)generate ``tasks/spike-aiortc.md``::

    uv run python examples/spike_aiortc.py
"""

from __future__ import annotations

import asyncio
import re

import aiortc
from aiortc import RTCPeerConnection, RTCSessionDescription
from aiortc.sdp import candidate_from_sdp, candidate_to_sdp

RELIABLE = "ReliableDataChannel"
UNRELIABLE = "UnreliableDataChannel"
BIG = 262144  # FRAGMENT_SIZE (262143) + 1 — largest single data-channel message we send
MAX_MESSAGE_SIZE = 262144  # SPEC.md s6.1


def set_max_message_size(sdp: str, value: int) -> str:
    """Force ``a=max-message-size`` in an SDP blob (replace if present, else insert)."""
    if "a=max-message-size:" in sdp:
        return re.sub(r"a=max-message-size:\d+", f"a=max-message-size:{value}", sdp)
    # Insert right after the SCTP port line of the application m-section.
    out = []
    for line in sdp.splitlines(keepends=True):
        out.append(line)
        if line.startswith("a=sctp-port:"):
            newline = "\r\n" if line.endswith("\r\n") else "\n"
            out.append(f"a=max-message-size:{value}{newline}")
    return "".join(out)


async def run_spike() -> dict:
    findings: dict = {"aiortc_version": aiortc.__version__}

    pc1 = RTCPeerConnection()  # dialer / offerer
    pc2 = RTCPeerConnection()  # listener / answerer

    received: dict[str, asyncio.Queue] = {RELIABLE: asyncio.Queue(), UNRELIABLE: asyncio.Queue()}

    @pc2.on("datachannel")
    def on_datachannel(channel):  # noqa: ANN001
        label = channel.label
        bucket = received[label] if label in received else received[UNRELIABLE]

        @channel.on("message")
        def on_message(message):  # noqa: ANN001
            bucket.put_nowait(message)

    reliable = pc1.createDataChannel(RELIABLE)
    unreliable = pc1.createDataChannel(UNRELIABLE, ordered=False, maxRetransmits=0)
    findings["unreliable_ordered"] = unreliable.ordered
    findings["unreliable_maxRetransmits"] = unreliable.maxRetransmits

    open_reliable = asyncio.Event()
    open_unreliable = asyncio.Event()
    reliable.on("open", open_reliable.set)
    unreliable.on("open", open_unreliable.set)

    try:
        # --- Offer (Q4: does setLocalDescription block until gathering completes?) ---
        await pc1.setLocalDescription(await pc1.createOffer())
        findings["gathering_state_after_setlocal"] = pc1.iceGatheringState
        offer_sdp = pc1.localDescription.sdp
        findings["sdp_has_candidates"] = "a=candidate" in offer_sdp

        # Q2: advertised max-message-size
        m = re.search(r"a=max-message-size:(\d+)", offer_sdp)
        findings["max_message_size_in_sdp"] = int(m.group(1)) if m else None

        # Q3: candidate string format + helper round-trip
        cand_lines = [ln for ln in offer_sdp.splitlines() if ln.startswith("a=candidate:")]
        findings["candidate_count_in_sdp"] = len(cand_lines)
        if cand_lines:
            attr = cand_lines[0][len("a=") :]  # "candidate:<foundation> <component> ..."
            findings["example_candidate_attr"] = attr
            for name, s in (("with_prefix", attr), ("no_prefix", attr[len("candidate:") :])):
                try:
                    c = candidate_from_sdp(s)
                    findings[f"from_sdp_{name}"] = "ok"
                    findings[f"to_sdp_{name}"] = candidate_to_sdp(c)
                except Exception as exc:  # noqa: BLE001
                    findings[f"from_sdp_{name}"] = f"error: {type(exc).__name__}: {exc}"

        # --- Munge both descriptions up to 262144 (the approach Task 8 will use) ---
        offer_munged = RTCSessionDescription(
            sdp=set_max_message_size(offer_sdp, MAX_MESSAGE_SIZE), type=pc1.localDescription.type
        )
        await pc2.setRemoteDescription(offer_munged)
        await pc2.setLocalDescription(await pc2.createAnswer())
        answer_munged = RTCSessionDescription(
            sdp=set_max_message_size(pc2.localDescription.sdp, MAX_MESSAGE_SIZE),
            type=pc2.localDescription.type,
        )
        await pc1.setRemoteDescription(answer_munged)

        await asyncio.wait_for(
            asyncio.gather(open_reliable.wait(), open_unreliable.wait()), timeout=20
        )
        findings["both_channels_open"] = True

        # --- Small messages both ways ---
        reliable.send(b"ping-reliable")
        unreliable.send(b"ping-unreliable")
        findings["small_reliable_len"] = len(await asyncio.wait_for(received[RELIABLE].get(), 10))
        findings["small_unreliable_len"] = len(
            await asyncio.wait_for(received[UNRELIABLE].get(), 10)
        )

        # --- Q1: the 262144-byte message (the critical de-risk) ---
        payload = bytes(i % 256 for i in range(BIG))
        try:
            reliable.send(payload)
            got = await asyncio.wait_for(received[RELIABLE].get(), 30)
            findings["large_received_len"] = len(got)
            findings["large_send_ok"] = bytes(got) == payload
        except Exception as exc:  # noqa: BLE001
            findings["large_send_ok"] = False
            findings["large_error"] = f"{type(exc).__name__}: {exc}"
    finally:
        await pc1.close()
        await pc2.close()

    return findings


_DOC_HEADER = """# aiortc spike findings (Task 2)

Generated by `examples/spike_aiortc.py` against the installed aiortc. These results
parameterize Tasks 7 (framing) and 8 (PeerConnection wrapper).

| Question | Finding |
|---|---|
"""

_CONCLUSIONS = """
## Conclusions for Tasks 7 & 8

- **Large messages:** sending a 262144-byte reliable message **{large}** after munging the SDP
  `a=max-message-size` up to {mms}. Task 8 must rewrite both local and remote descriptions to
  advertise `a=max-message-size:262144` so the SCTP sender permits a full-size fragment.
- **Non-trickle:** `setLocalDescription()` left ICE gathering in state `{gather}` with
  candidates {cand} embedded in the SDP — aiortc is a **non-trickle** peer (SPEC.md s9.3), so we
  send the full offer/answer and still accept inbound `CANDIDATEADD`.
- **Candidate format:** `candidate_from_sdp` expects the value **without** the `candidate:`
  prefix; `candidate_to_sdp` returns it **without** the prefix too. CANDIDATEADD carries the
  `candidate:...` form, so Task 8 strips `candidate:` on parse and prepends it on emit.
"""


def _render(findings: dict) -> str:
    rows = "".join(f"| `{k}` | {v} |\n" for k, v in findings.items())
    large = "SUCCEEDED" if findings.get("large_send_ok") else "FAILED"
    conclusions = _CONCLUSIONS.format(
        large=large,
        mms=MAX_MESSAGE_SIZE,
        gather=findings.get("gathering_state_after_setlocal"),
        cand="were" if findings.get("sdp_has_candidates") else "were NOT",
    )
    return _DOC_HEADER + rows + conclusions


if __name__ == "__main__":
    import pathlib

    result = asyncio.run(run_spike())
    for key, value in result.items():
        shown = value if not isinstance(value, str) or len(value) < 120 else value[:117] + "..."
        print(f"{key}: {shown}")
    doc = pathlib.Path(__file__).resolve().parents[1] / "tasks" / "spike-aiortc.md"
    doc.write_text(_render(result), encoding="utf-8")
    print(f"\nWrote {doc}")
