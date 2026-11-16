import json
from datetime import datetime
from unittest.mock import Mock

import pytest
from pipecat.frames.frames import TranscriptionFrame, TTSTextFrame
from pipecat.pipeline.pipeline import Pipeline
from pipecat.tests.utils import run_test

from server.cascade import StubTranscription, ToneTTS
from server.latency import DiscardAudioOutput
from server.live import CRISIS_LINE, FALLBACK, Conversation, live_stages
from server.memory.repository import Repository
from server.tool_calls import FILLER, result_message
from server.tools import execute, local_time


async def test_golden_live_turns_stream_cutoff_and_receipt(monkeypatch):
    monkeypatch.setenv("AMES_STUB", "1")
    prompts = []
    call = '<state crisis=0>Looking it up.<tool_call>{"name":"get_time","arguments":{}}</tool_call>'

    class Model:
        async def assemble(self, history, text):
            from server.prompt import PromptAssembler

            return PromptAssembler(lambda value: len(value.split()), tools=True).assemble(
                history, text
            )

        async def tokens(self, messages):
            prompts.append(messages)
            answer = call if len(prompts) == 1 else "<state crisis=0>It is noon."
            for chunk in [answer[:28], answer[28:42], answer[42:]]:
                yield chunk

    conversation = Conversation(Mock())
    calls = []
    stages = live_stages(
        StubTranscription(), Model(), ToneTTS(), conversation, DiscardAudioOutput()
    )
    from server.live import ToolLoop

    loop = next(stage for stage in stages if isinstance(stage, ToolLoop))

    async def fake_execute(request):
        calls.append(request)
        return {"datetime": "2026-11-15T12:00:00-05:00", "timezone": "America/New_York"}

    loop.executor = fake_execute
    received, unused = await run_test(
        Pipeline(stages), frames_to_send=[TranscriptionFrame("What time is it?", "", "now")]
    )
    spoken = " ".join(frame.text for frame in received if isinstance(frame, TTSTextFrame))
    assert spoken == "Looking it up. It is noon."
    assert calls == [{"name": "get_time", "arguments": {}}]
    receipt = result_message("get_time", data=await fake_execute(calls[0]))
    assert conversation.history == [
        {"role": "user", "content": "What time is it?"},
        {"role": "assistant", "content": call},
        receipt,
        {"role": "assistant", "content": "<state crisis=0>It is noon."},
    ]
    assert prompts[1] == prompts[0] + conversation.history[1:3]
    finals = [call.args for call in conversation.events.transcript.call_args_list if call.args[2]]
    assert finals[-1] == ("ames", "Looking it up. It is noon.", True, 1)
    assert "Ames tool protocol v1" in prompts[0][0]["content"]


@pytest.mark.parametrize(
    "name,arguments,receipt",
    [
        (
            "recall_memory",
            {"query": "last job decision"},
            {"episodes": ["Candid feedback mattered."]},
        ),
        ("remember", {"fact": "The user prefers one question."}, {"saved": True}),
        ("web_search", {"query": "four day work week evidence"}, {"results": []}),
        ("get_time", {}, {"datetime": "2026-11-15T12:00:00-05:00", "timezone": "America/New_York"}),
    ],
)
async def test_protocol_examples_through_live_pipeline(monkeypatch, name, arguments, receipt):
    monkeypatch.setenv("AMES_STUB", "1")
    wire = (
        "<state crisis=0><tool_call>"
        + json.dumps({"name": name, "arguments": arguments}, separators=(",", ":"))
        + "</tool_call>"
    )
    if name == "web_search":
        wire = wire.replace("<tool_call>", "I'm looking that up on the web.<tool_call>")

    class Model:
        async def assemble(self, history, text):
            return [{"role": "system", "content": "prompt"}, {"role": "user", "content": text}]

        async def tokens(self, messages):
            yield wire if len(messages) == 2 else "<state crisis=0>Done."

    conversation = Conversation(Mock())
    stages = live_stages(
        StubTranscription(), Model(), ToneTTS(), conversation, DiscardAudioOutput()
    )
    from server.live import ToolLoop

    calls = []

    async def fake_execute(call):
        calls.append(call)
        return receipt

    next(stage for stage in stages if isinstance(stage, ToolLoop)).executor = fake_execute
    received, unused = await run_test(
        Pipeline(stages), frames_to_send=[TranscriptionFrame("Please check", "", "now")]
    )
    assert calls == [{"name": name, "arguments": arguments}]
    assert " ".join(frame.text for frame in received if isinstance(frame, TTSTextFrame)) == (
        ("I'm looking that up on the web." if name == "web_search" else FILLER[name]) + " Done."
    )
    assert conversation.history == [
        {"role": "user", "content": "Please check"},
        {"role": "assistant", "content": wire},
        result_message(name, data=receipt),
        {"role": "assistant", "content": "<state crisis=0>Done."},
    ]


@pytest.mark.parametrize(
    "first,following,spoken,executions",
    [
        (
            '<state crisis=0><tool_call>{"name":"get_time","arguments":{}}</tool_call>',
            '<state crisis=0><tool_call>{"name":"get_time","arguments":{}}</tool_call>',
            "Let me check the time. " + FALLBACK,
            1,
        ),
        (
            "<state crisis=0>Wait.<tool_call>{broken}</tool_call>",
            "<state crisis=0>That failed.",
            "Wait. That failed.",
            0,
        ),
        ("<state crisis=1><tool_call>{broken}</tool_call>", None, CRISIS_LINE, 0),
    ],
)
async def test_invalid_calls_and_crisis(monkeypatch, first, following, spoken, executions):
    monkeypatch.setenv("AMES_STUB", "1")

    class Model:
        async def assemble(self, history, text):
            return [{"role": "system", "content": "prompt"}, {"role": "user", "content": text}]

        async def tokens(self, messages):
            yield first if len(messages) == 2 else following

    conversation = Conversation(Mock())
    stages = live_stages(
        StubTranscription(), Model(), ToneTTS(), conversation, DiscardAudioOutput()
    )
    from server.live import ToolLoop

    count = []

    async def fake_execute(call):
        count.append(call)
        return {"datetime": "2026-11-15T12:00:00-05:00", "timezone": "America/New_York"}

    next(stage for stage in stages if isinstance(stage, ToolLoop)).executor = fake_execute
    received, unused = await run_test(
        Pipeline(stages), frames_to_send=[TranscriptionFrame("Hi", "", "now")]
    )
    assert " ".join(frame.text for frame in received if isinstance(frame, TTSTextFrame)) == spoken
    assert len(count) == executions
    if following:
        assert json.loads(conversation.history[2]["content"])["ames_tool_result"]["ok"] is (
            executions == 1
        )


def test_migrate_v1_database_without_losing_facts(tmp_path):
    import sqlite3

    from server.memory.repository import SCHEMA

    path = tmp_path / "ames.db"
    connection = sqlite3.connect(path)
    legacy = (
        SCHEMA[: SCHEMA.index("CREATE TABLE episode")].replace(
            "INSERT INTO schema_version VALUES (2)", "INSERT INTO schema_version VALUES (1)"
        )
        + "COMMIT;"
    )
    connection.executescript(legacy)
    connection.execute(
        "INSERT INTO relation(subject_id, predicate, value, created_at) "
        "VALUES (1, 'likes', 'tea', 'now')"
    )
    connection.commit()
    connection.close()
    store = Repository(tmp_path)
    assert store.connection.execute("SELECT version FROM schema_version").fetchone()[0] == 2
    assert store.list_relations()[0]["value"] == "tea"
    store.remember("Tea matters")
    assert store.recall("Tea") == ["Tea matters"]
    store.close()


def test_memory_fts_delete_and_migrate(tmp_path):
    store = Repository(tmp_path)
    store.remember("I prefer candid feedback to prestige")
    assert store.recall('candid OR "') == ["I prefer candid feedback to prestige"]
    store.close()
    store = Repository(tmp_path)
    assert store.recall("feedback") == ["I prefer candid feedback to prestige"]
    store.delete_everything()
    assert store.recall("feedback") == []
    store.close()


async def test_registry_routes_memory_and_time(tmp_path, monkeypatch):
    from server.memory import repository

    monkeypatch.setattr(repository, "DATA_DIR", tmp_path)
    monkeypatch.setattr("server.tools.Repository", lambda: Repository(tmp_path))
    assert await execute({"name": "remember", "arguments": {"fact": "I like tea"}}) == {
        "saved": True
    }
    assert await execute({"name": "recall_memory", "arguments": {"query": "tea"}}) == {
        "episodes": ["I like tea"]
    }
    time = await execute({"name": "get_time", "arguments": {}})
    assert datetime.fromisoformat(time["datetime"]).utcoffset() is not None
    assert isinstance(time["timezone"], str) and time["timezone"]


def test_local_time_has_timezone():
    assert datetime.fromisoformat(local_time()["datetime"]).tzinfo is not None
