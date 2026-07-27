# nethernet

[![Python](https://img.shields.io/badge/python-3.11%2B-blue.svg)](https://www.python.org/downloads/)
[![License](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)

A cleanroom Python implementation of **NetherNet**, the WebRTC transport Minecraft Bedrock uses
for peer-to-peer and LAN play. It implements both signaling paths — LAN discovery and the
partner HTTP endpoint — over one asyncio connection model, with WebRTC provided by
[aiortc](https://github.com/aiortc/aiortc).

```python
import nethernet

async for host in nethernet.discover(timeout=3):
    async with await nethernet.connect(host) as conn:
        await conn.send(b"\xfe...")        # a Bedrock game packet
        print(await conn.recv())
    break
```

## Installation

The project is not published to PyPI yet; the `nethernet` name there belongs to an unrelated
project. Install from source:

```shell
pip install "nethernet[http] @ git+https://github.com/EndstoneMC/nethernet-python"
```

Requires Python 3.11 or newer. The `http` extra pulls in aiohttp for HTTP signaling; the LAN
path needs only the base install.

## The two signaling paths

Signaling is how two peers exchange their WebRTC session descriptions. NetherNet has two
channels for it, and they produce the same connection:

| Path | Entry points | Used for |
| --- | --- | --- |
| LAN | `serve` / `connect` / `discover` | encrypted UDP broadcast on the local network; how Bedrock finds LAN games |
| HTTP | `serve_http` / `connect_http` | a client posts its offer to `/v1/join/{networkId}`; how Bedrock reaches a partner-hosted server |

Both hand back the same `Connection`, a message pipe with `send` / `recv`. NetherNet is
symmetric peer-to-peer, so one class serves both ends of a connection.

The wire protocol is specified in [`SPEC.md`](SPEC.md), reverse-engineered from Bedrock
Dedicated Server. The HTTP path additionally follows Mojang's
[NetherNet onboarding guide](https://github.com/Mojang/bedrock-protocol-docs/blob/main/NetherNetOnboardingGuide.md).

## Quick start

### LAN server

```python
import asyncio, secrets, nethernet
from nethernet import SendType, NetworkID

async def handle(connection):
    async for packet in connection:         # iterates until the peer disconnects
        await connection.send(packet, SendType.RELIABLE)

async def main():
    local_id = NetworkID.p2p(secrets.randbits(64))
    async with nethernet.serve(handle, local_id, advertisement=b"MCPE;...") as server:
        await server.serve_forever()        # one task per connection

asyncio.run(main())
```

### LAN client

```python
async for host in nethernet.discover(timeout=3):
    print(host.network_id, host.advertisement)
    async with await nethernet.connect(host) as conn:
        await conn.send(b"hello", SendType.RELIABLE)
        reply = await conn.recv()
    break
```

### HTTP signaling

`serve_http` exposes the endpoints a Bedrock client posts its offer to. Every answer must carry
an operator identity assertion or the client refuses the connection:

```python
from nethernet import IdentitySigner, generate_operator_key

key = generate_operator_key()               # long-lived; share one across a fleet
async with nethernet.serve_http(
    handle, port=8080,
    identity_signer=IdentitySigner(key, domain="partner.example"),
) as server:
    await server.serve_forever()
```

```python
conn = await nethernet.connect_http("https://partner.example")
```

More runnable programs are in [examples/](examples).

## Messages

`send` and `recv` move whole application packets; there is no added framing to parse. On the
wire each packet carries a one-byte fragment header, which the library adds and strips for you.
Reliability is per-call:

```python
await conn.send(data, SendType.RELIABLE)     # ordered, retransmitted
await conn.send(data, SendType.UNRELIABLE)   # unordered, may be lost
```

Reliable packets are fragmented as needed, up to about 64 MiB. Unreliable ones are never
fragmented, so anything over 262143 bytes is dropped rather than sent.

## Identity assertions

Both signaling directions can carry an `a=identity` assertion in the SDP: the client asserts an
authenticated player, the server asserts a long-lived operator key. This library implements the
envelope mechanics — canonical JSON, detached JWS over the DTLS fingerprints, the self-signed
`cpk` token — and leaves policy to you.

| Concern | Where it lives |
| --- | --- |
| signing an answer, verifying structure | `IdentitySigner`, `verify_server_identity` |
| authorizing a player from their token | your `validate_offer` callback |
| pinning an operator key across connections | your `on_server_identity` callback |

Validating a `GameServerToken` against Minecraft's auth service, reading XUIDs, and storing
trust-on-first-use pins are all application decisions, so they stay outside the transport.

## Errors

Failures raise exceptions rather than returning sentinels. All inherit from `NetherNetError`.

| Exception | Raised when |
| --- | --- |
| `ConnectionFailed` | a dial never reached the connected state (`.error` carries the cause) |
| `ConnectionClosed` | `send` / `recv` on a closed connection; `.error` is `NONE` for a clean close |
| `InvalidIdentity` | an `a=identity` assertion is missing, malformed, or fails verification |
| `SignalingRejected` | raise it from `validate_offer` to refuse an offer |

`.error` is a `SessionError`, the same code Bedrock reports: negotiation timeouts, ICE
failure, data-channel close.

## Development

This project uses [`uv`](https://docs.astral.sh/uv/).

```shell
uv sync --extra dev
uv run pytest
uv run ruff check
```

## License

MIT. See [LICENSE](LICENSE).
