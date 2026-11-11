"""Opt-in OpenTelemetry tracing to a loopback Langfuse, for development only.

Spans carry conversation text, an accepted exception to design 9.2. This module is
imported only when AMES_TRACE=1, so a normal run never loads the tracing packages.
"""

import base64
import os
from urllib.parse import urlsplit

from loguru import logger

LOOPBACK_HOSTS = {"localhost", "127.0.0.1", "::1"}
DEFAULT_URL = "http://localhost:3300"


def enable(environ=os.environ):
    keys = [environ.get("LANGFUSE_PUBLIC_KEY"), environ.get("LANGFUSE_SECRET_KEY")]
    if not all(keys):
        raise SystemExit("AMES_TRACE=1 needs LANGFUSE_PUBLIC_KEY and LANGFUSE_SECRET_KEY.")
    url = environ.get("LANGFUSE_BASE_URL") or DEFAULT_URL
    try:
        parts = urlsplit(url)
        loopback = parts.scheme in ("http", "https") and parts.hostname in LOOPBACK_HOSTS
    except ValueError:
        loopback = False
    if not loopback:
        raise SystemExit(
            "LANGFUSE_BASE_URL must be an http(s) URL on localhost, 127.0.0.1 or ::1; "
            "traces hold conversation text and never leave this machine."
        )
    # Imported only now: nothing is loaded or connected unless tracing is on.
    from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
    from pipecat.utils.tracing.setup import setup_tracing

    auth = base64.b64encode(":".join(keys).encode()).decode()
    exporter = OTLPSpanExporter(
        endpoint=f"{url.rstrip('/')}/api/public/otel/v1/traces",
        headers={"Authorization": f"Basic {auth}", "x-langfuse-ingestion-version": "4"},
    )
    if not setup_tracing("ames", exporter=exporter):
        raise SystemExit("AMES_TRACE=1 could not start tracing; run uv sync --locked.")
    logger.info("tracing_enabled to {}", parts.netloc)
