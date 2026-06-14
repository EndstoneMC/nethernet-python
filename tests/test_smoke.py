"""Scaffold smoke tests: the package is importable and required deps are installed."""


def test_package_exposes_version():
    import nethernet

    assert nethernet.__version__ == "0.0.1"


def test_aiortc_is_importable():
    # aiortc is the WebRTC engine (ICE/DTLS/SCTP) the transport is built on (SPEC.md s6).
    import aiortc  # noqa: F401


def test_cryptography_is_importable():
    # cryptography provides AES-256-ECB + PKCS7 for the LAN discovery envelope (SPEC.md s8).
    import cryptography  # noqa: F401
