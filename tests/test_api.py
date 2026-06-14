"""Public async API surface — see docs/api-design.md.

connect() / serve() / discover() + Connection / Server / DiscoveredHost, layered over the
internal Transport engine. These exercise the public contract only.
"""

from __future__ import annotations

from nethernet.errors import (
    ConnectionClosed,
    ConnectionFailed,
    ESessionError,
    NetherNetError,
)

# --- Exceptions (docs/api-design.md s3.5) ---


def test_exception_hierarchy_and_error_payload():
    assert issubclass(NetherNetError, Exception)
    assert issubclass(ConnectionFailed, NetherNetError)
    assert issubclass(ConnectionClosed, NetherNetError)

    failed = ConnectionFailed(ESessionError.ICE)
    assert failed.error is ESessionError.ICE
    assert "ICE" in str(failed)

    closed = ConnectionClosed(ESessionError.NONE)
    assert closed.error is ESessionError.NONE
