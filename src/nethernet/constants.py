"""Configurable protocol defaults — SPEC.md s10."""

from __future__ import annotations

DEFAULT_BROADCAST_PORT = 7551  # u16 (SPEC.md s7.1)
DEFAULT_APPLICATION_ID = 0xDEADBEEF  # u64, 3735928559 (SPEC.md s7.1)
DEFAULT_BROADCAST_INTERVAL = 2.0  # seconds, i.e. 2000 ms (SPEC.md s7.1)
DEFAULT_NEGOTIATION_TIMEOUT = 10.0  # seconds (SPEC.md s9.4)
