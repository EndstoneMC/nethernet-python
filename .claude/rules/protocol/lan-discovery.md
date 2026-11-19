---
paths:
  - "src/nethernet/discovery/__init__.py"
  - "src/nethernet/discovery/packets.py"
  - "src/nethernet/discovery/lan.py"
  - "tests/test_discovery_packets.py"
  - "tests/test_lan_discovery.py"
  - "tests/test_conformance.py"
---

# LAN discovery

- Socket: SPEC §7.1
- Base header, Request, Response: SPEC §7.2 to §7.4
- Host advertisement: SPEC §7.5
- Message: SPEC §7.6
- Receive: SPEC §7.7
- Peer table and manual hosts: SPEC §7.8
- First-discovery probe: SPEC §7.9
- Broadcast discovery and broadcast addresses: SPEC §7.10
- Suspend and resume: SPEC §7.11

## Socket

- This library's `serve` raises when `lan_port` is in use and does not retry on port 0 (`api.md`).
- This library hands one `socket.AF_INET6` socket, or the IPv4 fallback, to `create_datagram_endpoint(sock=...)`.

## Host advertisement

- This library reads a record as the release client does, except that it drops non-hex text.
- This library has no online signaling link, so it advertises 4.
- This library's `serve` accepts self-signed player tokens, so it advertises `onlineAuth` 1 and `selfSignedAuth` 1, as BDS does with `online-mode=false`.

## Broadcast discovery

- A client sends Requests only while it browses for LAN games, on this discovery port. The same browse also starts the RakNet LAN ping on 19132 and 19133, which is not NetherNet (1.26.60).
- This library's `serve` answers Requests and does not broadcast, and `discover` broadcasts while it runs.

## Send

- A target with no live record gets nothing: no datagram and no log. The LAN send result is an unknown-peer error that no session reports (SPEC §6.2) (1.26.60).
