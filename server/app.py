"""Loopback-only HTTP signaling for the local voice pipeline."""

import asyncio
import os
import secrets
import tempfile
import traceback
import webbrowser
from contextlib import suppress
from pathlib import Path
from uuid import uuid4

from aiohttp import web
from aiortc.sdp import SessionDescription
from loguru import logger

from server.cascade import StubTranscription, ToneTTS, log_errors, model_services, tts_service
from server.echo import echo_connection
from server.events import Events
from server.live import Conversation, build_live

HOSTS = {"127.0.0.1:8765", "localhost:8765"}
DATA_DIR = Path.home() / "Library/Application Support/Ames"
WEB_ROOT = Path(__file__).resolve().parent.parent / "web/dist"


def write_token(data_dir: Path, token: str):
    data_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    data_dir.chmod(0o700)
    with tempfile.NamedTemporaryFile(mode="w", dir=data_dir, delete=False) as file:
        path = Path(file.name)
        try:
            file.write(token)
            file.flush()
            path.chmod(0o600)
            path.replace(data_dir / "token")
        finally:
            path.unlink(missing_ok=True)


def log_failure(code: str, error: Exception):
    # Exception messages and values can hold user speech; the type and place cannot.
    frame = traceback.extract_tb(error.__traceback__)[-1]
    logger.error(
        "{}: {} at {}:{}", code, type(error).__name__, Path(frame.filename).name, frame.lineno
    )


class Session:
    def __init__(self, connection, stt, model, tts):
        self.connection = connection
        self.session_id = uuid4().hex
        self.events = Events(connection)
        self.task = None
        self.stt = stt
        self.model = model
        self.tts = tts
        self.conversation = Conversation(self.events)

    def prepare(self):
        stt = self.stt.new_session() if hasattr(self.stt, "new_session") else StubTranscription()
        tts = self.tts.new_session() if hasattr(self.tts, "new_session") else ToneTTS()
        self.live = build_live(self.connection, stt, self.model, tts, self.conversation)

    async def run(self):
        try:
            await self.live()
        except Exception as error:
            log_failure("pipeline_failed", error)
            self.events.notice("session_ended", "The voice pipeline stopped. Start a new session.")
        finally:
            self.conversation.history.clear()
            self.conversation.reply = ""
            await self.model.clear_history()
            await self.connection.disconnect()

    async def close(self, code: str, text: str):
        if self.connection.pc.connectionState != "closed":
            self.events.notice(code, text)
        await self.connection.disconnect()
        if self.task:
            self.task.cancel()
            with suppress(asyncio.CancelledError):
                await self.task
            # Closing can cancel natural teardown while it awaits the executor.
            await self.model.clear_history()


def create_app(token: str, web_root: Path = WEB_ROOT) -> web.Application:
    active = None
    stt = model = tts = None
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
            len(tokens) != 1
            or not secrets.compare_digest(
                tokens[0].encode(errors="surrogateescape"), token.encode()
            )
        ):
            raise web.HTTPUnauthorized(text="Missing or invalid token")
        return await handler(request)

    async def lifecycle(app):
        nonlocal stt, model, tts
        try:
            stt, model = model_services()
            tts = tts_service()
            await model.prepare()
            if hasattr(stt, "load_model"):
                await asyncio.get_running_loop().run_in_executor(stt.executor, stt.load_model)
            if hasattr(tts, "prepare"):
                await tts.prepare()
            yield
        finally:
            async with lock:
                if active:
                    await active.close("session_ended", "The server stopped.")
            if stt:
                await stt.cleanup()
            if tts:
                await tts.cleanup()
            if model:
                await model.close()

    async def index(request):
        if not (web_root / "index.html").is_file():
            raise web.HTTPServiceUnavailable(text="Build the client: cd web && npm run build")
        return web.FileResponse(web_root / "index.html", headers={"Cache-Control": "no-store"})

    async def health(request):
        models = [
            {
                "role": role,
                "id": str(getattr(service, "model_path", "stub")),
                "loaded": getattr(service, "model", True) is not None,
            }
            for role, service in (("stt", stt), ("llm", model), ("tts", tts))
        ]
        return web.json_response(
            {"ready": all(item["loaded"] for item in models), "models": models}
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
            session = Session(echo_connection(), stt, model, tts)
            try:
                await session.connection.initialize(body["sdp"], body["type"])
            except Exception:
                await session.connection.disconnect()
                raise web.HTTPBadRequest(text="Invalid SDP offer") from None
            try:
                session.prepare()
            except Exception as error:
                log_failure("pipeline_setup_failed", error)
                await session.connection.disconnect()
                raise web.HTTPInternalServerError(
                    text="The voice pipeline could not start. Check the server log."
                ) from None
            if active:
                try:
                    await active.close("session_replaced", "Another tab started a session.")
                except Exception:
                    await session.connection.disconnect()
                    raise
            active = session
            session.events.speaking("none")
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


def start_tracing():
    if os.environ.get("AMES_TRACE") == "1":
        from server.tracing import enable

        enable()


def main():
    os.umask(0o077)
    log_errors()
    start_tracing()
    token = secrets.token_urlsafe(32)

    def open_client(message):
        # aiohttp calls this only after every listener has bound successfully.
        write_token(DATA_DIR, token)
        webbrowser.get("open -a 'Google Chrome' %s").open(f"http://127.0.0.1:8765/#t={token}")

    app = create_app(token)
    try:
        web.run_app(app, host="127.0.0.1", port=8765, access_log=None, print=open_client)
    except OSError as error:
        raise SystemExit(
            "Cannot listen on 127.0.0.1:8765; check whether port 8765 is in use."
        ) from error


if __name__ == "__main__":
    main()
