import subprocess
import sys

import pytest

from server.tracing import enable

KEYS = {"LANGFUSE_PUBLIC_KEY": "pk", "LANGFUSE_SECRET_KEY": "sk"}
OFF_CHECK = """
import socket, sys
def refuse(*args, **kwargs):
    raise AssertionError("connection attempted")
socket.socket.connect = refuse
import server.app, server.live
server.app.start_tracing()
loaded = [m for m in sys.modules if m == "server.tracing" or m.startswith("opentelemetry.exporter")]
assert not loaded, loaded
"""


def test_flag_off_imports_no_tracing_and_connects_nowhere():
    env = {"PATH": "", "AMES_STUB": "1", "LANGFUSE_BASE_URL": "http://example.com"}
    subprocess.run([sys.executable, "-c", OFF_CHECK], env=env, check=True)


@pytest.mark.parametrize(
    "url",
    [
        "http://example.com",
        "http://10.0.0.5:3300",
        "http://localhost.example.com:3300",
        "http://localhost@example.com",
        "ftp://localhost",
        "localhost:3300",
    ],
)
def test_non_loopback_endpoint_is_rejected(url):
    with pytest.raises(SystemExit, match="LANGFUSE_BASE_URL"):
        enable({**KEYS, "LANGFUSE_BASE_URL": url})


@pytest.mark.parametrize("missing", list(KEYS))
def test_missing_keys_are_rejected(missing):
    with pytest.raises(SystemExit, match="LANGFUSE_PUBLIC_KEY and LANGFUSE_SECRET_KEY"):
        enable({key: value for key, value in KEYS.items() if key != missing})
