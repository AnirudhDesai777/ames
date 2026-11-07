"""Loopback-only HTTP signaling for the model-free echo."""

import asyncio
import os
import secrets
import sys
import tempfile
import webbrowser
from contextlib import suppress
from pathlib import Path
from uuid import uuid4

from aiohttp import web
from aiortc.sdp import SessionDescription
from loguru import logger

from server.echo import echo_connection, run_echo
from server.events import Events

HOSTS = {"127.0.0.1:8765", "localhost:8765"}
DATA_DIR = Path.home() / "Library/Application Support/Ames"
WEB_ROOT = Path(__file__).resolve().parent.parent / "web/dist"


def write_token(data_dir: Path) -> str:
    data_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    data_dir.chmod(0o700)
    token = secrets.token_urlsafe(32)
    with tempfile.NamedTemporaryFile(mode="w", dir=data_dir, delete=False) as file:
        path = Path(file.name)
        try:
            file.write(token)
            file.flush()
            path.chmod(0o600)
            path.replace(data_dir / "token")
        finally:
            path.unlink(missing_ok=True)
    return token


class Session:
    def __init__(self, connection):
        self.connection = connection
        self.session_id = uuid4().hex
        self.events = Events(connection)
        self.task = None

    async def run(self):
        try:
            await run_echo(self.connection)
        except Exception:
            logger.error("echo_failed")

    async def close(self, code: str, text: str):
        if self.connection.pc.connectionState != "closed":
            self.events.notice(code, text)
        await self.connection.disconnect()
        if self.task:
            self.task.cancel()
            with suppress(asyncio.CancelledError):
                await self.task


def create_app(data_dir: Path = DATA_DIR, web_root: Path = WEB_ROOT) -> web.Application:
    token = ""
    active = None
    lock = asyncio.Lock()

    @web.middleware
    async def authenticate(request, handler):
        hosts = request.headers.getall("Host", [])
        origins = request.headers.getall("Origin", [])
        if len(hosts) != 1 or hosts[0] not in HOSTS:
            raise web.HTTPForbidden(text="Invalid Host")
        if origins and origins != [f"http://{hosts[0]}"]:
            raise web.HTTPForbidden(text="Invalid Origin")
        public = request.method == "GET" and (
            request.path == "/" or request.path.startswith("/assets/")
        )
        tokens = request.headers.getall("X-Ames-Token", [])
        if not public and (
            len(tokens) != 1 or not secrets.compare_digest(tokens[0].encode(), token.encode())
        ):
            raise web.HTTPUnauthorized(text="Missing or invalid token")
        return await handler(request)

    async def lifecycle(app):
        nonlocal token
        if os.environ.get("AMES_STUB") != "1":
            raise RuntimeError("S1b supports only the echo stubs. Set AMES_STUB=1.")
        token = write_token(data_dir)
        yield
        async with lock:
            if active:
                await active.close("session_ended", "The server stopped.")

    async def index(request):
        if not (web_root / "index.html").is_file():
            raise web.HTTPServiceUnavailable(text="Build the client: cd web && npm run build")
        return web.FileResponse(web_root / "index.html", headers={"Cache-Control": "no-store"})

    async def health(request):
        return web.json_response(
            {
                "ready": True,
                "models": [
                    {"role": role, "id": "stub", "loaded": True} for role in ("stt", "llm", "tts")
                ],
            }
        )

    async def offer(request):
        nonlocal active
        try:
            body = await request.json()
        except (ValueError, UnicodeDecodeError):
            raise web.HTTPBadRequest(text="Invalid offer JSON") from None
        if (
            not isinstance(body, dict)
            or body.get("type") != "offer"
            or not isinstance(body.get("sdp"), str)
            or not body["sdp"].strip()
        ):
            raise web.HTTPBadRequest(text="Expected an SDP offer")
        try:
            description = SessionDescription.parse(body["sdp"])
            if description.version != 0 or not description.media:
                raise ValueError("No SDP media")
        except (ValueError, AssertionError, IndexError, KeyError):
            raise web.HTTPBadRequest(text="Invalid SDP offer") from None
        async with lock:
            session = Session(echo_connection())
            try:
                await session.connection.initialize(body["sdp"], body["type"])
            except Exception:
                await session.connection.disconnect()
                raise web.HTTPBadRequest(text="Invalid SDP offer") from None
            if active:
                await active.close("session_replaced", "Another tab started a session.")
            active = session
            session.events.state("neutral", 0)
            session.events.speaking("none")
            session.events.transcript("ames", "Audio echo ready (stub).", True, 0)
            session.task = asyncio.create_task(session.run())
            return web.json_response(
                {**session.connection.get_answer(), "session_id": session.session_id}
            )

    app = web.Application(middlewares=[authenticate])
    app.cleanup_ctx.append(lifecycle)
    app.router.add_get("/", index)
    app.router.add_get("/api/health", health)
    app.router.add_post("/api/offer", offer)
    if (web_root / "assets").is_dir():
        app.router.add_static("/assets/", web_root / "assets")
    return app


def main():
    os.umask(0o077)
    # Library debug logs can include SDP or data-channel messages.
    logger.remove()
    logger.add(sys.stderr, level="ERROR", format="{message}")

    def open_client(message):
        token = (DATA_DIR / "token").read_text()
        webbrowser.get("open -a 'Google Chrome' %s").open(f"http://127.0.0.1:8765/#t={token}")

    app = create_app()
    try:
        web.run_app(app, host="127.0.0.1", port=8765, access_log=None, print=open_client)
    except OSError as error:
        raise SystemExit(
            "Cannot listen on 127.0.0.1:8765; check whether port 8765 is in use."
        ) from error


if __name__ == "__main__":
    main()
