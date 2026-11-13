import subprocess
import sys
from unittest.mock import Mock

import pytest

from server import live
from server.tracing import enable

KEYS = {"LANGFUSE_PUBLIC_KEY": "pk", "LANGFUSE_SECRET_KEY": "sk"}
OFF_CHECK = """
import sys
import server.app, server.live
server.app.start_tracing()
loaded = [m for m in sys.modules if m == "server.tracing" or m.startswith("opentelemetry.exporter")]
assert not loaded, loaded
"""


def test_flag_off_imports_no_tracing_module():
    env = {"PATH": "", "AMES_STUB": "1", "LANGFUSE_BASE_URL": "http://example.com"}
    subprocess.run([sys.executable, "-c", OFF_CHECK], env=env, check=True)


def test_flag_off_leaves_services_and_pipeline_untraced(monkeypatch):
    from server.cascade import LocalLLMService
    from server.stt import ParakeetSTTService
    from server.tts import PocketTTSService

    for method in (
        LocalLLMService.respond,
        ParakeetSTTService.run_stt,
        PocketTTSService.run_tts,
    ):
        assert not hasattr(method, "__wrapped__")
    monkeypatch.delenv("AMES_TRACE", raising=False)
    monkeypatch.setenv("AMES_STUB", "1")
    worker = Mock()
    monkeypatch.setattr(live, "PipelineWorker", worker)
    for name in ("SmallWebRTCTransport", "WorkerRunner", "Pipeline"):
        monkeypatch.setattr(live, name, Mock())
    monkeypatch.setattr(live, "live_stages", Mock(return_value=[]))
    live.build_live(Mock(), Mock(), Mock(), Mock(), Mock())
    assert worker.call_args.kwargs["enable_tracing"] is False


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


def test_missing_dev_packages_exit_with_the_fix(monkeypatch):
    monkeypatch.setitem(sys.modules, "opentelemetry.exporter.otlp.proto.http.trace_exporter", None)
    with pytest.raises(SystemExit, match="uv sync --locked"):
        enable(KEYS)


ON_CHECK = """
import logging, sys
import pipecat.utils.tracing.setup as setup_module
captured = []
setup_module.setup_tracing = lambda name, exporter: captured.append(exporter) or True
from server.tracing import enable
url = sys.argv[1]
enable({"LANGFUSE_PUBLIC_KEY": "pk", "LANGFUSE_SECRET_KEY": "sk", "LANGFUSE_BASE_URL": url})
exporter = captured[0]
assert exporter._endpoint == url + "/api/public/otel/v1/traces", exporter._endpoint
session = exporter._client._transport._session
settings = session.merge_environment_settings(exporter._endpoint, {}, None, None, None)
assert settings["proxies"] == {}, settings["proxies"]
from server.stt import ParakeetSTTService
from server.tts import PocketTTSService
from server.cascade import LocalLLMService
assert hasattr(LocalLLMService.respond, "__wrapped__")
assert hasattr(PocketTTSService.run_tts, "__wrapped__")
assert ParakeetSTTService.push_frame.__stt_tracing_push_frame_wrapped__
"""


@pytest.mark.parametrize(
    "url", ["http://localhost:3300", "http://127.0.0.1:3300", "http://[::1]:3300"]
)
def test_loopback_endpoint_is_accepted_without_proxies(url):
    env = {
        "PATH": "",
        "AMES_STUB": "1",
        "HTTP_PROXY": "http://proxy.example.com:8080",
        "HTTPS_PROXY": "http://proxy.example.com:8080",
    }
    subprocess.run([sys.executable, "-c", ON_CHECK, url], env=env, check=True)
