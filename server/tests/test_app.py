import asyncio
import json
import stat
from unittest.mock import AsyncMock, Mock

import pytest
from aiohttp.test_utils import TestClient, TestServer
from aiortc import RTCConfiguration, RTCPeerConnection, RTCSessionDescription

from server import app as app_module
from server.app import create_app, write_token
from server.events import Events


@pytest.fixture
async def client(tmp_path, monkeypatch):
    monkeypatch.setenv("AMES_STUB", "1")
    web_root = tmp_path / "dist"
    web_root.mkdir()
    (web_root / "index.html").write_text('<script src="/assets/app.js"></script>')
    (web_root / "assets").mkdir()
    (web_root / "assets/app.js").write_text("// bundled client")
    async with TestClient(TestServer(create_app(tmp_path / "data", web_root))) as client:
        client.token = (tmp_path / "data/token").read_text()
        yield client


def headers(client, **extra):
    return {"Host": "127.0.0.1:8765", "X-Ames-Token": client.token, **extra}


@pytest.mark.parametrize(
    "method,path",
    [
        ("GET", "/"),
        ("GET", "/assets/app.js"),
        ("GET", "/api/health"),
        ("POST", "/api/offer"),
    ],
)
@pytest.mark.parametrize(
    "host", ["evil.example:8765", "127.0.0.1", "localhost:9999", "127.0.0.1:8765.evil"]
)
async def test_bad_host_on_every_endpoint(client, method, path, host):
    response = await client.request(method, path, headers=headers(client, Host=host))
    assert response.status == 403
    assert "Access-Control-Allow-Origin" not in response.headers


@pytest.mark.parametrize(
    "method,path",
    [
        ("GET", "/"),
        ("GET", "/assets/app.js"),
        ("GET", "/api/health"),
        ("POST", "/api/offer"),
    ],
)
@pytest.mark.parametrize("origin", ["https://evil.example", "null", "http://localhost:8765", ""])
async def test_bad_origin_on_every_endpoint(client, method, path, origin):
    response = await client.request(method, path, headers=headers(client, Origin=origin))
    assert response.status == 403
    assert "Access-Control-Allow-Origin" not in response.headers


@pytest.mark.parametrize("method,path", [("GET", "/api/health"), ("POST", "/api/offer")])
@pytest.mark.parametrize("token", [None, "wrong", ""])
async def test_missing_or_wrong_token(client, method, path, token):
    request_headers = {"Host": "127.0.0.1:8765"}
    if token is not None:
        request_headers["X-Ames-Token"] = token
    response = await client.request(method, path, headers=request_headers)
    assert response.status == 401


@pytest.mark.parametrize("host", ["127.0.0.1:8765", "localhost:8765"])
async def test_health_with_same_origin_or_no_origin(client, host):
    for origin in [None, f"http://{host}"]:
        request_headers = headers(client, Host=host)
        if origin:
            request_headers["Origin"] = origin
        response = await client.get("/api/health", headers=request_headers)
        assert response.status == 200
        assert await response.json() == {
            "ready": True,
            "models": [
                {"role": role, "id": "stub", "loaded": True} for role in ("stt", "llm", "tts")
            ],
        }
        assert "Access-Control-Allow-Origin" not in response.headers


async def test_page_and_assets_are_public_and_contain_no_token(client):
    for path in ["/", "/assets/app.js"]:
        response = await client.get(path, headers={"Host": "127.0.0.1:8765"})
        assert response.status == 200
        assert client.token not in await response.text()
        assert client.token not in str(response.headers)
    response = await client.get("/token", headers=headers(client))
    assert response.status == 404


def test_token_rotates_and_has_private_permissions(tmp_path):
    data_dir = tmp_path / "data"
    first = write_token(data_dir)
    (data_dir / "token").chmod(0o644)
    second = write_token(data_dir)
    assert first != second
    assert len(second) >= 32
    assert (data_dir / "token").read_text() == second
    assert stat.S_IMODE((data_dir / "token").stat().st_mode) == 0o600
    assert stat.S_IMODE(data_dir.stat().st_mode) == 0o700
    assert list(data_dir.iterdir()) == [data_dir / "token"]


@pytest.mark.parametrize(
    "body",
    [
        None,
        [],
        {},
        {"type": "answer", "sdp": "x"},
        {"type": "offer", "sdp": 7},
        {"type": "offer", "sdp": ""},
    ],
)
async def test_invalid_offer_body(client, body):
    response = await client.post("/api/offer", headers=headers(client), json=body)
    assert response.status == 400


async def test_invalid_sdp_closes_new_connection(client, monkeypatch):
    connection = Mock()
    connection.initialize = AsyncMock(side_effect=ValueError("private SDP"))
    connection.disconnect = AsyncMock()
    monkeypatch.setattr(app_module, "echo_connection", lambda: connection)
    response = await client.post(
        "/api/offer", headers=headers(client), json={"type": "offer", "sdp": "invalid"}
    )
    assert response.status == 400
    assert "private SDP" not in await response.text()
    connection.disconnect.assert_awaited_once()


def test_rtvi_emitters():
    connection = Mock()
    events = Events(connection)
    events.transcript("user", "stub", True, 0)
    events.speaking("none")
    events.state("neutral", 0)
    events.notice("server_error", "stub")
    assert [call.args[0] for call in connection.send_app_message.call_args_list] == [
        {"label": "rtvi-ai", "type": "server-message", "data": data}
        for data in [
            {"type": "transcript", "role": "user", "text": "stub", "final": True, "turn_id": 0},
            {"type": "speaking", "who": "none"},
            {"type": "state", "mood": "neutral", "turn_id": 0},
            {"type": "notice", "code": "server_error", "text": "stub"},
        ]
    ]


async def test_real_offer_rtvi_and_session_replaced(client, monkeypatch):
    connections = []
    factory = app_module.echo_connection

    def connection_factory():
        connection = factory()
        connections.append(connection)
        return connection

    monkeypatch.setattr(app_module, "echo_connection", connection_factory)
    peers = []
    messages = asyncio.Queue()

    async def connect():
        peer = RTCPeerConnection(RTCConfiguration(iceServers=[]))
        peers.append(peer)
        peer.addTransceiver("audio", direction="sendrecv")
        channel = peer.createDataChannel("rtvi-ai")

        @channel.on("message")
        def receive(message):
            messages.put_nowait(json.loads(message))

        await peer.setLocalDescription(await peer.createOffer())
        response = await client.post(
            "/api/offer",
            headers=headers(client),
            json={
                "sdp": peer.localDescription.sdp,
                "type": "offer",
            },
        )
        assert response.status == 200, await response.text()
        answer = await response.json()
        await peer.setRemoteDescription(
            RTCSessionDescription(sdp=answer["sdp"], type=answer["type"])
        )
        return answer

    try:
        first = await connect()
        received = [await asyncio.wait_for(messages.get(), 10) for count in range(3)]
        assert {message["data"]["type"] for message in received} == {
            "state",
            "speaking",
            "transcript",
        }
        assert all(
            message["label"] == "rtvi-ai" and message["type"] == "server-message"
            for message in received
        )
        second = await connect()
        assert first["session_id"] != second["session_id"]
        notice = await asyncio.wait_for(messages.get(), 10)
        assert notice["data"]["code"] == "session_replaced"
        assert connections[0].pc.connectionState == "closed"
        assert connections[1].pc.connectionState != "closed"
    finally:
        await client.close()
        await asyncio.gather(*(peer.close() for peer in peers))
    assert all(connection.pc.connectionState == "closed" for connection in connections)
