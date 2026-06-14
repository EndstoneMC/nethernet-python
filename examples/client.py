"""Example NetherNet client: discover a host on the LAN, connect, and send a message.

Run ``server.py`` on one machine, then on another (same LAN / Application Id / port)::

    uv run python examples/client.py
"""

from __future__ import annotations

import asyncio

import nethernet
from nethernet import ESendType


async def main() -> None:
    print("discovering hosts...")
    hosts = [host async for host in nethernet.discover(timeout=3.0)]
    for host in hosts:
        print(f"  found {host.network_id}: {host.advertisement!r}")
    if not hosts:
        print("no hosts found")
        return

    host = hosts[0]
    print(f"connecting to {host.network_id}...")
    async with await nethernet.connect(host) as conn:
        print("connected; sending hello")
        await conn.send(b"hello from client.py", ESendType.RELIABLE)
        reply = await conn.recv()
        print(f"reply from host: {reply!r}")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
