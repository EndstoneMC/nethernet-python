# nethernet-python

A cleanroom Python implementation of Minecraft Bedrock's **NetherNet** peer-to-peer LAN
transport, built on [aiortc](https://github.com/aiortc/aiortc) (WebRTC: ICE + DTLS + SCTP
data channels).

The wire protocol is implemented from [`SPEC.md`](SPEC.md). It covers LAN discovery, LAN
signaling, WebRTC session negotiation, and data-channel framing, so a Python process (e.g. an
Endstone Bedrock server) can advertise a host on the LAN, discover hosts, and exchange
application packets with NetherNet peers.

> Status: **in development.** See [`tasks/plan.md`](tasks/plan.md) and
> [`tasks/todo.md`](tasks/todo.md) for the implementation plan.

## Development

This project uses [`uv`](https://docs.astral.sh/uv/).

```sh
uv sync --extra dev   # create the environment and install dependencies
uv run pytest         # run the test suite
```

Requires Python 3.11+.

## License

MIT
