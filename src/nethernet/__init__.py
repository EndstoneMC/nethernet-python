"""nethernet — a cleanroom Python implementation of Minecraft Bedrock's NetherNet transport.

The wire protocol (LAN discovery, LAN signaling, WebRTC session negotiation, and data-channel
framing) is implemented from ``SPEC.md``, with WebRTC provided by aiortc.
"""

from nethernet.errors import EConnectionFlags, ESendType, ESessionError
from nethernet.network_id import NetworkID, NetworkIDType, new_connection_id
from nethernet.session import Session, SessionState
from nethernet.transport_api import Transport

__version__ = "0.0.1"

__all__ = [
    "Transport",
    "NetworkID",
    "NetworkIDType",
    "Session",
    "SessionState",
    "ESendType",
    "ESessionError",
    "EConnectionFlags",
    "new_connection_id",
    "__version__",
]
