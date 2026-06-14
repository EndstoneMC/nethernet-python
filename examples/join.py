"""Example NetherNet client: discover a host on the LAN, connect, and send a message.

Run ``host.py`` on one machine, then on another (same LAN / Application Id / port)::

    uv run python examples/join.py
"""

from __future__ import annotations

import asyncio
import secrets

from nethernet import ESendType, NetworkID, Session, SessionState, Transport


async def main() -> None:
    local_id = NetworkID.p2p(secrets.randbits(64))
    reply = asyncio.Event()

    def on_packet(session: Session, data: bytes) -> None:
        print(f"reply from host: {data!r}")
        reply.set()

    transport = Transport(local_id, on_packet=on_packet)
    await transport.start()
    try:
        print("discovering hosts...")
        hosts = await transport.discover(timeout=3.0)
        for host_id, advertisement in hosts.items():
            print(f"  found {host_id}: {advertisement!r}")
        if not hosts:
            print("no hosts found")
            return

        host_id = next(iter(hosts))
        print(f"connecting to {host_id}...")
        session = await transport.connect(host_id)

        deadline = asyncio.get_event_loop().time() + 15
        while session.state is not SessionState.CONNECTED:
            if asyncio.get_event_loop().time() > deadline:
                print("connection timed out")
                return
            await asyncio.sleep(0.05)

        print("connected; sending hello")
        session.send(b"hello from join.py", ESendType.RELIABLE)
        await asyncio.wait_for(reply.wait(), timeout=10)
    finally:
        await transport.aclose()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
