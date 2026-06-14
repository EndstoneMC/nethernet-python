"""nethernet — a cleanroom Python implementation of Minecraft Bedrock's NetherNet transport.

The wire protocol (LAN discovery, LAN signaling, WebRTC session negotiation, and data-channel
framing) is implemented from ``SPEC.md``, with WebRTC provided by aiortc.

Public API (see ``docs/api-design.md``): module-level :func:`connect` / :func:`serve` /
:func:`discover` returning a :class:`Connection` and :class:`Server`. The ``Transport`` engine
is internal (import from ``nethernet.transport_api`` for advanced use).
"""

from nethernet.api import (
    Connection,
    DiscoveredHost,
    Server,
    connect,
    discover,
    serve,
)
from nethernet.discovery.lan import Address
from nethernet.errors import (
    ConnectionClosed,
    ConnectionFailed,
    EConnectionFlags,
    ESendType,
    ESessionError,
    NetherNetError,
)
from nethernet.network_id import NetworkID, NetworkIDType, new_connection_id
from nethernet.session import SessionState

__version__ = "0.0.1"

__all__ = [
    # entry points
    "connect",
    "serve",
    "discover",
    # connection objects
    "Connection",
    "Server",
    "DiscoveredHost",
    # identity
    "NetworkID",
    "NetworkIDType",
    "new_connection_id",
    "Address",
    # state / enums
    "SessionState",
    "ESendType",
    "ESessionError",
    "EConnectionFlags",
    # exceptions
    "NetherNetError",
    "ConnectionFailed",
    "ConnectionClosed",
    "__version__",
]
