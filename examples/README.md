# Examples

Runnable examples for the `nethernet` package. Install it first (`uv sync --extra dev`, or
`pip install -e ".[http]"` for the HTTP signaling pair), then run any script directly.

| Script | What it shows |
| --- | --- |
| [echo_server.py](echo_server.py) / [echo_client.py](echo_client.py) | LAN discovery and signaling: a host advertises itself, a client finds it and echoes messages. |
| [http_echo_server.py](http_echo_server.py) / [http_echo_client.py](http_echo_client.py) | The same over HTTP signaling, with operator identity assertions. |
| [discover_hosts.py](discover_hosts.py) | List the NetherNet hosts advertising on the LAN. |

Start a server in one terminal and the matching client in another. The LAN pair finds each
other by UDP broadcast, which a machine does not deliver to its own processes, so on one
machine pass `--loopback` to both ends:

```shell
python echo_server.py --loopback
python echo_client.py --loopback
```

Across two machines drop the flag. Both ends must agree on `--port` and `--app-id`, since a
host only answers discovery requests carrying its own Application Id. The HTTP scripts need no
such flag — they connect to an address — and take `--host` / `--port` and `--url`. See `--help`
on any script.

`spike_aiortc.py` is not an example — it probes the installed aiortc for the behavior
[`tasks/spike-aiortc.md`](../tasks/spike-aiortc.md) records, and `tests/test_aiortc_spike.py`
runs it to catch changes on upgrade.
