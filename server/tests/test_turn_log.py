import json
import sys
from unittest.mock import Mock

from pipecat.frames.frames import (
    InputAudioRawFrame,
    InterruptionFrame,
    LLMFullResponseEndFrame,
    TTSTextFrame,
)
from pipecat.pipeline.pipeline import Pipeline
from pipecat.tests.utils import SleepFrame, run_test

import server.app as app_module
from server.cascade import StubTranscription, ToneTTS
from server.latency import DiscardAudioOutput
from server.live import Conversation, SessionOutput, live_stages
from server.llm import StubLanguageModel
from server.turn_log import TurnLog


def lines(log):
    return [json.loads(line) for line in log.path.read_text().splitlines()]


def test_turns_and_ratings_append_with_private_permissions(tmp_path, monkeypatch):
    monkeypatch.setenv("AMES_LLM_MODEL", "/models/bolt-e4b")
    log = TurnLog(tmp_path, "abc", lambda: "azelma")
    log.turn(1, "hi", "Hello.", False)
    for turn_id, rating in [
        (1, "good"),
        (1, "bad"),
        (2, "good"),
        (1, "meh"),
        ("1", "good"),
        (True, "good"),
        (1, ["good"]),
        (1, "bad"),
    ]:
        log.rate(turn_id, rating)
    assert (tmp_path / "turns").stat().st_mode & 0o777 == 0o700
    assert log.path.stat().st_mode & 0o777 == 0o600
    turn, *ratings = lines(log)
    assert {
        k: turn[k]
        for k in (
            "type",
            "session",
            "turn_id",
            "user",
            "ames",
            "interrupted",
            "model",
            "voice",
            "latency_ms",
        )
    } == {
        "type": "turn",
        "session": "abc",
        "turn_id": 1,
        "user": "hi",
        "ames": "Hello.",
        "interrupted": False,
        "model": "bolt-e4b",
        "voice": "azelma",
        "latency_ms": {},
    }
    assert len(turn["prompt"]) == 12
    assert [(r["type"], r["turn_id"], r["rating"]) for r in ratings] == [
        ("rating", 1, "good"),
        ("rating", 1, "bad"),
    ]


async def test_live_turn_is_logged_with_latency(tmp_path, monkeypatch):
    monkeypatch.setenv("AMES_STUB", "1")
    monkeypatch.setitem(sys.modules, "pipecat.audio.vad.silero", None)
    log = TurnLog(tmp_path, "abc")
    events = Mock()
    conversation = Conversation(events, log)
    output = DiscardAudioOutput()
    await run_test(
        Pipeline(
            live_stages(StubTranscription(), StubLanguageModel(), ToneTTS(), conversation, output)
        ),
        frames_to_send=[
            *[InputAudioRawFrame(bytes(640), 16000, 1) for i in range(50)],
            *[InputAudioRawFrame(b"\x00\x20\x00\xe0" * 160, 16000, 1) for i in range(50)],
            *[InputAudioRawFrame(bytes(640), 16000, 1) for i in range(50)],
            SleepFrame(0.35),
        ],
        observers=[log.watch(output)],
    )
    [turn] = lines(log)
    assert (turn["user"], turn["ames"], turn["interrupted"]) == (
        "hi there",
        conversation.history[1]["content"],
        False,
    )
    assert {"stt_final", "llm_first_token", "total"} <= turn["latency_ms"].keys()
    assert events.transcript.call_args_list[-1].kwargs == {"rateable": True}


async def test_interrupted_reply_is_logged_once(tmp_path):
    log = TurnLog(tmp_path, "abc")
    conversation = Conversation(Mock(), log)
    conversation.turn_id = 1
    conversation.final = False
    conversation.heard = "hello"
    conversation.history.append({"role": "user", "content": "hello"})
    await run_test(
        SessionOutput(conversation),
        frames_to_send=[
            TTSTextFrame("Finished sentence.", "sentence"),
            SleepFrame(0.05),
            InterruptionFrame(),
            SleepFrame(0.05),
            LLMFullResponseEndFrame(),
        ],
    )
    assert [(t["ames"], t["interrupted"]) for t in lines(log)] == [("Finished sentence.", True)]


async def test_rate_messages_reach_the_log_only_when_enabled(tmp_path, monkeypatch):
    monkeypatch.setenv("AMES_STUB", "1")
    monkeypatch.setattr(app_module, "DATA_DIR", tmp_path)
    monkeypatch.setattr(app_module, "build_live", Mock())
    stt, model = app_module.model_services()

    def prepared():
        handlers = {}
        connection = Mock()
        connection.event_handler = lambda name: lambda handler: handlers.setdefault(name, handler)
        session = app_module.Session(connection, stt, model, Mock(spec=[]))
        session.prepare()
        return session, connection, handlers

    session, connection, handlers = prepared()
    assert session.conversation.log is None
    await handlers["app-message"](connection, {"type": "rate", "turn_id": 1, "rating": "good"})
    assert not (tmp_path / "turns").exists()

    monkeypatch.setenv("AMES_TURN_LOG", "1")
    session, connection, handlers = prepared()
    log = session.conversation.log
    log.turn(1, "hi", "Hello.", False)
    await handlers["app-message"](connection, {"type": "rate", "turn_id": 1, "rating": "bad"})
    assert [line["type"] for line in lines(log)] == ["turn", "rating"]


def test_failed_write_does_not_raise(tmp_path):
    log = TurnLog(tmp_path, "abc")
    log.path.unlink()
    log.path.mkdir()
    log.turn(1, "hi", "Hello.", False)
