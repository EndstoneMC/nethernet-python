"""List NetherNet hosts on the LAN.

Broadcasts a discovery request and prints every host that answers, with the advertisement it
published. Hosts only answer requests carrying their own Application Id.

Pass --loopback to query a host running on this machine instead of broadcasting.
"""

import argparse
import asyncio

import nethernet


async def main(port: int, application_id: int, timeout: float, loopback: bool) -> None:
    options = {"port": port, "application_id": application_id}
    if loopback:
        options = {
            "port": 0,
            "application_id": application_id,
            "bind_host": "127.0.0.1",
            "broadcast_host": "127.0.0.1",
            "broadcast_port": port,
        }

    found = 0
    async for host in nethernet.discover(timeout=timeout, **options):
        found += 1
        print(f"{host.network_id} at {host.address[0]}:{host.address[1]}")
        print(f"  {host.advertisement!r}")
    print(f"{found} host(s) in {timeout:g}s")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=7551)
    parser.add_argument("--app-id", type=lambda v: int(v, 0), default=0xDEADBEEF)
    parser.add_argument("--timeout", type=float, default=3.0)
    parser.add_argument("--loopback", action="store_true", help="query a host on this machine")
    args = parser.parse_args()
    asyncio.run(main(args.port, args.app_id, args.timeout, args.loopback))
