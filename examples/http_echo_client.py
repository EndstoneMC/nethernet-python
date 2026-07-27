"""Echo client using HTTP signaling.

Posts an SDP offer to http_echo_server.py, verifies the operator identity in the answer,
sends a few messages and prints the replies.

The identity callback shows where trust policy belongs. Over HTTPS the TLS certificate is
the anchor and there is nothing more to do; over plaintext HTTP a real client pins
key_digest on first use and compares it on every later connection.
"""

import argparse
import asyncio

import nethernet
from nethernet import ESendType, ServerIdentity


def check_identity(identity: ServerIdentity) -> None:
    print(f"operator {identity.domain} key {identity.key_digest}")


async def main(url: str) -> None:
    connection = await nethernet.connect_http(url, on_server_identity=check_identity)
    async with connection:
        print("connected")
        for line in ("hello", "nethernet", "goodbye"):
            await connection.send(line.encode(), ESendType.RELIABLE)
            reply = await asyncio.wait_for(connection.recv(), timeout=5)
            print(f"echoed: {reply.decode()}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default="http://127.0.0.1:8080")
    args = parser.parse_args()
    asyncio.run(main(args.url))
