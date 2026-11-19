---
paths:
  - "src/nethernet/constants.py"
  - "src/nethernet/api.py"
  - "src/nethernet/transport_api.py"
---

# Config

- Tunables and their defaults: SPEC §14
- Host defaults, LAN visibility, bind address: SPEC §14.1
- WebRTC UDP ports and the STUN reflector: SPEC §14.2
- Negotiation timeout and sweep: SPEC §12.7

Configurable defaults, listed in SPEC §14. Every one stays configurable.

## Library

- Defaults live in `constants.py`, in seconds for a duration. Never invent one: a default comes from SPEC §14.
- `serve()` takes the dedicated-server shape: HTTP signaling on `[::]:19132` and LAN on 7551 with the default application id, answering Requests without broadcasting them.
- The `discover` deadline is this library's choice, not a BDS setting. The HTTP POST timeout is the negotiation timeout, and a LAN host lookup is bounded by the `timeout` argument of `connect` (`api.md`).
- The library dialer never trickles: it sends one full-ICE offer. The listener accepts trickled candidates.
- WebRTC UDP ports are OS-chosen, one ephemeral port per peer connection.
