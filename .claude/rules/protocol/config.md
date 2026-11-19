---
paths:
  - "src/nethernet/constants.py"
  - "src/nethernet/api.py"
  - "src/nethernet/transport_api.py"
---

# Config

Configurable defaults. Every one stays configurable.

## Tunables

| Setting | Default | Unit | Side | Dedicated-server knob | Tag |
| --- | --- | --- | --- | --- | --- |
| LAN discovery port | 7551 | UDP port, `u16` | both | none: fixed whatever `server-port` says | (1.26.60) |
| Application id | `0xDEADBEEF`, 3735928559 | `u64` | both | none | (1.26.60) |
| Broadcast interval | 2 | s | the peer with broadcast discovery on | none | (1.26.60) |
| Broadcast discovery | off on a dedicated server | on or off | the peer that searches | none | (1.26.60) |
| Negotiation timeout | 10 | s | dialer only (`session.md`) | none | (1.26.60) |
| HTTP signaling server | on for a dedicated server, off for a hosting client | on or off | listener | none: on with `transport=nethernet` | (1.26.60) |
| HTTP signaling port | 19132 | TCP port | listener | `server-port` | (1.26.60) |
| HTTP signaling bind address | `[::]`, dual stack | address | listener | `server-ip` | (1.26.60) |
| LAN signaling | on | on or off | both | none: forced on at creation | (1.26.60) |
| LAN visibility | on | on or off | listener | `enable-lan-visibility` | (1.26.60) |
| Trickle ICE | on | on or off | both, LAN sessions override it (`session.md`) | none | (1.26.60) |
| ICE servers | none | list of servers (`transport.md`) | both | none | (1.26.60) |
| WebRTC UDP ports | shipped `19132`; unset means OS-chosen | comma-separated entries | both | `server-udp-ports` | (1.26.60) |
| Port mappings | none | one `A:B` | listener | `server-port-mappings` | (1.26.60) |
| Reflector reply IP | none: the request's source IP | IP address | listener | `server-public-ip` | (1.26.60) |

- Negotiation timeout: a negative value counts as 0 (1.26.60).
- LAN visibility off only stops discovery Responses (`lan-discovery.md`). LAN Messages and LAN signaling carry on, the discovery port stays bound, the `GET /v1/join` body is empty (`http-signaling.md`) and `POST /v1/join/{networkId}` still answers (1.26.60).
- HTTP signaling bind address: `server-ip` sets the listener's address, in one address family only. Empty means dual-stack `[::]`. An address that does not parse, or one the host lacks, leaves no HTTP listener, and the server starts with no error. The discovery socket ignores `server-ip` (1.26.60).
- This library raises from `serve()` on a bad bind address instead (`api.md`).
- LAN signaling: a dedicated server turns it on when it creates the transport and never turns it off, whatever `server.properties` says (1.26.60).
- ICE servers: BDS sets an empty relay list after it signs in, so a dedicated server never uses ICE servers (1.26.60).
- The session sweep period, the probe timeout, the HTTP join and receive timeouts and the fragment size are fixed. They live in `session.md`, `http-signaling.md` and `framing.md`.

### WebRTC UDP ports

`server-udp-ports` is a comma-separated list. Each entry is one of these forms (1.26.60):

| Form | Meaning |
| --- | --- |
| `P` | a bare port |
| `L-H` | a bare range, `H` not below `L` |
| `A:B` | public port or range `A` maps to local port or range `B`, of equal length |
| `IP:A:B`, `[IPv6]:A:B` | the same, with the reflector reply IP |

- Every WebRTC socket binds in one port range, the union of every bare entry and every local side `B` (1.26.60). A bare entry only widens it and carries no IP.
- Each mapping adds a STUN reflector entry (`transport.md`): source ports `B`, offset `A - B`, reply IP from the entry's IP or `server-public-ip` (1.26.60). At most 16 mappings.
- Every per-entry IP and `server-public-ip` must name the same IP (1.26.60).
- `server-port-mappings` feeds the same mappings and accepts only single-port `A:B`, with no IP, range or bare port (1.26.60).
- With a mapping in `server-udp-ports` (`A:B`, `IP:A:B`, a range) or in `server-port-mappings`, the answer carries only `typ srflx <public IP or interface IP>:A raddr <interface IP> rport B`, no host candidate, and only B is bound (1.26.60).
- `server-public-ip` replaces the srflx address and does nothing without a mapping. `server-port-mappings` next to `server-udp-ports=19132` has no visible effect: the answer carries host 19132 only (1.26.60).
- The shipped `19132` binds that one port and adds no reflector entry. The answer SDP advertises it, and BDS binds it on the host's interface address only while a session is live (1.26.60).
- A new socket takes a free port in the range. With none free it shares the live socket that has the fewest users, so with one port every session shares one socket (`transport.md`) (1.26.60).

## Host defaults

A dedicated server with the shipped `server.properties`, observed on BDS 1.26.60.6 (1.26.60):

- `transport=nethernet` is the shipped value.
- It serves HTTP signaling on plain TCP `[::]:19132` (`http-signaling.md`) and LAN discovery and LAN signaling on UDP `[::]:7551` (`lan-discovery.md`).
- It sends no discovery Requests and answers each one with a Response.
- It opens no socket on `server-portv6`, and none on `server-udp-ports` until a session is live.
- It also signs in to the online signaling service, which this library does not implement.
- A client joined it over LAN signaling.

A hosting client, read from the Windows client 1.26.60.5 binary, not observed (1.26.60):

- It serves LAN discovery and LAN signaling only. It runs no HTTP signaling server.
- It uses the same discovery port, broadcast interval, application id and negotiation timeout as the dedicated server.
- Its WebRTC ports are OS-chosen and it has no ICE servers when the transport is created.
- LAN signaling stays on: a release client never turns it off (1.26.60).

## Library

- Defaults live in `constants.py`, in seconds for a duration. Never invent one: a default comes from this table.
- `serve()` takes the dedicated-server shape: HTTP signaling on `[::]:19132` and LAN on 7551 with the default application id, answering Requests without broadcasting them.
- The `discover` deadline and the HTTP POST timeout are this library's choices, not BDS settings.
