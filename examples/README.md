# Examples

Runnable examples for the `nethernet` package. Install it first with `uv sync --extra dev`, then run any script directly.

| Script | What it shows |
| --- | --- |
| [serve_echo.py](serve_echo.py) | A host that echoes every packet back. |
| [connect_echo.py](connect_echo.py) | A client that sends three lines and prints the echoes. |
| [discover_hosts.py](discover_hosts.py) | List the hosts that answer LAN discovery. |

Start the host in one terminal and the client in another:

```shell
python serve_echo.py
python connect_echo.py 127.0.0.1
python discover_hosts.py
```

A machine does not deliver its own UDP broadcast to itself, so on one machine point discovery at the host's LAN port instead:

```shell
python discover_hosts.py --broadcast-host 127.0.0.1
```

`connect_echo.py` takes a `host`, `host:port` or URL. See `--help` on any script.
