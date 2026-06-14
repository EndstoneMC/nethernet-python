"""Task 2 regression guard: aiortc still supports NetherNet's transport requirements.

This pins the spike findings (examples/spike_aiortc.py) so an aiortc upgrade can't silently
break the assumptions Tasks 7 & 8 depend on. It runs a real loopback ICE/DTLS/SCTP handshake,
so it is a *medium* test (localhost, a few seconds).
"""

import sys
from pathlib import Path

EXAMPLES = Path(__file__).resolve().parents[1] / "examples"
if str(EXAMPLES) not in sys.path:
    sys.path.insert(0, str(EXAMPLES))

from spike_aiortc import run_spike  # noqa: E402


async def test_aiortc_supports_nethernet_transport_requirements():
    f = await run_spike()

    # Two data channels open with the exact reliability params NetherNet requires (SPEC.md s6.2).
    assert f["both_channels_open"] is True
    assert f["unreliable_ordered"] is False
    assert f["unreliable_maxRetransmits"] == 0

    # The critical de-risk: a single 262144-byte (FRAGMENT_SIZE + 1) reliable message survives.
    assert f["large_send_ok"] is True, f.get("large_error")
    assert f["large_received_len"] == 262144

    # aiortc is non-trickle: setLocalDescription blocks until gathering completes (SPEC.md s9.3).
    assert f["gathering_state_after_setlocal"] == "complete"
    assert f["sdp_has_candidates"] is True

    # aiortc advertises less than we need by default, so Task 8 must munge the SDP up to 262144.
    assert isinstance(f["max_message_size_in_sdp"], int)
    assert f["max_message_size_in_sdp"] < 262144
