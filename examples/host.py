"""Example NetherNet host: advertise on the LAN, accept sessions, and echo packets.

Run on one machine::

    uv run python examples/host.py

then run ``join.py`` on another machine on the same LAN (same Application Id and port).
"""

from __future__ import annotations

import asyncio
import secrets

from nethernet import ESendType, NetworkID, Session, Transport


async def main() -> None:
    local_id = NetworkID.p2p(secrets.randbits(64))

    def on_session(session: Session) -> None:
        print(f"incoming session {session.connection_id} from {session.remote_id}")

    def on_packet(session: Session, data: bytes) -> None:
        print(f"recv {len(data)} bytes; echoing back")
        session.send(data, ESendType.RELIABLE)

    transport = Transport(
        local_id,
        advertisement=b"MCPE;NetherNet-Python;0;0.0.0;0;10;",
        on_session=on_session,
        on_packet=on_packet,
    )
    await transport.start()
    transport.start_broadcasting()
    print(f"hosting as {local_id} ({local_id.correlation_id()})")
    try:
        await asyncio.Future()  # run until cancelled
    finally:
        await transport.aclose()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
