import argparse
import asyncio

import nethernet


def print_identity(identity: nethernet.ServerIdentity) -> None:
    print(f"host key {identity.key_pin}")


async def main(target: str) -> None:
    async with nethernet.connect(target, on_server_identity=print_identity) as connection:
        print(f"connected to {connection.remote_id}")
        for line in ("hello", "nethernet", "goodbye"):
            await connection.send(line.encode())
            reply = await asyncio.wait_for(connection.recv(), timeout=5)
            print(f"echoed: {reply.decode()}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("target", nargs="?", default="127.0.0.1")
    args = parser.parse_args()
    asyncio.run(main(args.target))
