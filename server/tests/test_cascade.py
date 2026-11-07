import asyncio
import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from pipecat.frames.frames import (
    AggregatedTextFrame,
    InterruptionFrame,
    LLMContextFrame,
    LLMFullResponseEndFrame,
    LLMFullResponseStartFrame,
    LLMTextFrame,
    TranscriptionFrame,
    TTSAudioRawFrame,
)
from pipecat.pipeline.pipeline import Pipeline
from pipecat.processors.aggregators.llm_context import LLMContext
from pipecat.tests.utils import SleepFrame, run_test

from server import cascade
from server.cascade import LocalLLMService, PlainPrompt, SentenceSplitter, ToneTTS
from server.llm import MLXLanguageModel, StubLanguageModel


async def test_transcript_runs_plain_prompt_through_streaming_sentence_tts():
    model = StubLanguageModel()
    received, upstream = await run_test(
        Pipeline([PlainPrompt(), LocalLLMService(model), SentenceSplitter(), ToneTTS()]),
        frames_to_send=[TranscriptionFrame("hi", "", "now")],
    )
    audio = [frame for frame in received if isinstance(frame, TTSAudioRawFrame)]
    assert len(audio) == 2
    assert all(frame.audio.strip(b"\x00") and frame.sample_rate == 16000 for frame in audio)
    assert sum(isinstance(frame, LLMFullResponseStartFrame) for frame in received) == 1
    assert sum(isinstance(frame, LLMFullResponseEndFrame) for frame in received) == 1
    assert not any(isinstance(frame, LLMContextFrame) for frame in received)


async def test_sentence_splitter_buffers_chunks_and_flushes_last_sentence():
    received, upstream = await run_test(
        SentenceSplitter(),
        frames_to_send=[
            LLMFullResponseStartFrame(),
            LLMTextFrame("Hello"),
            LLMTextFrame(". Next"),
            LLMTextFrame(" one"),
            LLMFullResponseEndFrame(),
        ],
    )
    assert [frame.text for frame in received if isinstance(frame, AggregatedTextFrame)] == [
        "Hello.",
        "Next one",
    ]


async def test_interruption_cancels_stream_and_drops_partial_sentence():
    started = asyncio.Event()
    cancelled = asyncio.Event()

    class SlowModel:
        async def tokens(self, messages):
            try:
                yield "unfinished"
                started.set()
                await asyncio.Event().wait()
            finally:
                cancelled.set()

    received, upstream = await asyncio.wait_for(
        run_test(
            Pipeline([LocalLLMService(SlowModel()), SentenceSplitter()]),
            frames_to_send=[
                LLMContextFrame(LLMContext([{"role": "user", "content": "hi"}])),
                SleepFrame(0.05),
                InterruptionFrame(),
                SleepFrame(0.05),
            ],
        ),
        5,
    )
    assert started.is_set() and cancelled.is_set()
    assert not any(isinstance(frame, AggregatedTextFrame) for frame in received)
    assert not any(isinstance(frame, LLMFullResponseEndFrame) for frame in received)


async def test_token_timeout_emits_error_without_response_end(monkeypatch):
    closed = []
    native_timeout = asyncio.timeout
    monkeypatch.setattr(cascade.asyncio, "timeout", lambda seconds: native_timeout(0.01))

    class HungModel:
        async def tokens(self, messages):
            try:
                await asyncio.Event().wait()
                yield "unreachable"
            finally:
                closed.append(True)

    service = LocalLLMService(HungModel())
    service.push_frame = AsyncMock()
    service.push_error = AsyncMock()
    await service.respond([])
    service.push_error.assert_awaited_once_with("local_generation_failed")
    assert closed == [True]
    assert [type(call.args[0]) for call in service.push_frame.await_args_list] == [
        LLMFullResponseStartFrame
    ]


def test_stub_selection(monkeypatch):
    monkeypatch.setenv("AMES_STUB", "1")
    stt, model = cascade.model_services()
    assert isinstance(stt, cascade.StubTranscription)
    assert isinstance(model, StubLanguageModel)


def test_real_selection_uses_local_whisper_and_model_paths(tmp_path, monkeypatch):
    monkeypatch.delenv("AMES_STUB", raising=False)
    monkeypatch.setattr(cascade.platform, "system", lambda: "Darwin")
    monkeypatch.setattr(cascade.platform, "machine", lambda: "arm64")
    monkeypatch.setenv("HF_HOME", "unused")
    monkeypatch.setenv("HF_HUB_OFFLINE", "0")
    monkeypatch.setattr(cascade, "MODEL_DIR", tmp_path)
    whisper_path = tmp_path / "whisper-large-v3-turbo"
    whisper_path.mkdir()
    monkeypatch.setenv("AMES_LLM_MODEL", str(tmp_path))
    whisper = Mock()
    module = SimpleNamespace(WhisperSTTServiceMLX=whisper)
    monkeypatch.setitem(sys.modules, "pipecat.services.whisper.stt", module)
    stt, model = cascade.model_services()
    assert isinstance(model, MLXLanguageModel)
    whisper.Settings.assert_called_once_with(model=str(whisper_path))
    whisper.assert_called_once_with(settings=whisper.Settings.return_value, sample_rate=16000)
    model.executor.shutdown()


def test_real_selection_requires_mac_and_local_files(monkeypatch, tmp_path):
    monkeypatch.delenv("AMES_STUB", raising=False)
    monkeypatch.setattr(cascade.platform, "system", lambda: "Linux")
    with pytest.raises(RuntimeError, match="Apple Silicon"):
        cascade.model_services()
    monkeypatch.setattr(cascade.platform, "system", lambda: "Darwin")
    monkeypatch.setattr(cascade.platform, "machine", lambda: "arm64")
    monkeypatch.setenv("HF_HOME", "unused")
    monkeypatch.setenv("HF_HUB_OFFLINE", "0")
    monkeypatch.setattr(cascade, "MODEL_DIR", tmp_path)
    monkeypatch.delenv("AMES_LLM_MODEL", raising=False)
    with pytest.raises(RuntimeError, match="Install local Whisper"):
        cascade.model_services()
