"""Scaffold smoke tests: the package is importable and required deps are installed."""


def test_package_exposes_version():
    from importlib.metadata import version

    import nethernet

    assert nethernet.__version__ == version("nethernet-python")


def test_aiortc_is_importable():
    import aiortc  # noqa: F401


def test_cryptography_is_importable():
    import cryptography  # noqa: F401
