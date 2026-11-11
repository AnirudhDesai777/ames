"""Opt-in OpenTelemetry tracing to a loopback Langfuse, for development only.

Spans carry conversation text, an accepted exception to design 9.2. This module is
imported only when AMES_TRACE=1. Only then are the exporter, the provider and the
tracing wrappers on the STT, LLM and TTS services installed, and only then does the
server make a connection for tracing. The OpenTelemetry API itself is imported by
Pipecat whenever the dev group is installed, flag or not.
"""

import base64
import logging
import os
from urllib.parse import urlsplit

from loguru import logger

LOOPBACK_HOSTS = {"localhost", "127.0.0.1", "::1"}
DEFAULT_URL = "http://localhost:3300"


def trace_services():
    from pipecat.utils.tracing.service_decorators import traced_llm, traced_stt, traced_tts

    from server.cascade import LocalLLMService
    from server.stt import ParakeetSTTService
    from server.tts import QwenTTSService

    LocalLLMService.respond = traced_llm(LocalLLMService.respond)
    # These decorators return a descriptor that installs itself in __set_name__,
    # which Python calls only inside a class body, so call it here.
    for cls, name, decorate in (
        (ParakeetSTTService, "run_stt", traced_stt),
        (QwenTTSService, "run_tts", traced_tts),
    ):
        decorate(vars(cls)[name]).__set_name__(cls, name)


def enable(environ=os.environ):
    keys = [environ.get("LANGFUSE_PUBLIC_KEY"), environ.get("LANGFUSE_SECRET_KEY")]
    if not all(keys):
        raise SystemExit("AMES_TRACE=1 needs LANGFUSE_PUBLIC_KEY and LANGFUSE_SECRET_KEY.")
    url = environ.get("LANGFUSE_BASE_URL") or DEFAULT_URL
    try:
        parts = urlsplit(url)
        loopback = parts.scheme in ("http", "https") and parts.hostname in LOOPBACK_HOSTS
        port = parts.port
    except ValueError:
        loopback = False
    if not loopback:
        raise SystemExit(
            "LANGFUSE_BASE_URL must be an http(s) URL on localhost, 127.0.0.1 or ::1, "
            "so traces go only to this machine."
        )
    try:
        import requests
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
        from pipecat.utils.tracing.setup import setup_tracing
    except ImportError:
        raise SystemExit("AMES_TRACE=1 needs the dev dependencies; run uv sync --locked.") from None

    # Pipecat reports tracing errors through the root logger with the exception text,
    # which can hold conversation text; drop those and quiet OpenTelemetry's own logs.
    logging.getLogger().addFilter(lambda record: "pipecat/utils/tracing" not in record.pathname)
    logging.getLogger("opentelemetry").setLevel(logging.ERROR)
    # No proxy variables: a session that trusts the environment could route spans off
    # this machine. The exporter itself does not follow redirects.
    session = requests.Session()
    session.trust_env = False
    auth = base64.b64encode(":".join(keys).encode()).decode()
    exporter = OTLPSpanExporter(
        endpoint=f"{url.rstrip('/')}/api/public/otel/v1/traces",
        headers={"Authorization": f"Basic {auth}", "x-langfuse-ingestion-version": "4"},
        session=session,
    )
    if not setup_tracing("ames", exporter=exporter):
        raise SystemExit("AMES_TRACE=1 could not start tracing; run uv sync --locked.")
    trace_services()
    logger.info("tracing_enabled to {}:{}", parts.hostname, port or "default")
