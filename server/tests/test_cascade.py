import asyncio
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
    tts = ToneTTS()
    received, upstream = await run_test(
        Pipeline(
            [
                PlainPrompt(),
                LocalLLMService(model),
                cascade.ReplySanitizer(tts),
                SentenceSplitter(),
                tts,
            ]
        ),
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


def test_real_selection_uses_local_parakeet_and_model_paths(tmp_path, monkeypatch):
    monkeypatch.delenv("AMES_STUB", raising=False)
    monkeypatch.setattr(cascade.platform, "system", lambda: "Darwin")
    monkeypatch.setattr(cascade.platform, "machine", lambda: "arm64")
    monkeypatch.setenv("HF_HOME", "unused")
    monkeypatch.setenv("HF_HUB_OFFLINE", "0")
    monkeypatch.setattr(cascade, "MODEL_DIR", tmp_path)
    parakeet_path = tmp_path / "parakeet-tdt-0.6b-v2"
    parakeet_path.mkdir()
    monkeypatch.setenv("AMES_LLM_MODEL", str(tmp_path))
    parakeet = Mock()
    monkeypatch.setattr(cascade, "ParakeetSTTService", parakeet)
    stt, model = cascade.model_services()
    assert isinstance(model, MLXLanguageModel)
    parakeet.assert_called_once_with(parakeet_path)
    assert stt is parakeet.return_value
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
    with pytest.raises(RuntimeError, match="Install local Parakeet.*parakeet-tdt-0.6b-v2"):
        cascade.model_services()


async def test_raw_user_speech_does_not_cancel_before_interruption_strategy():
    from pipecat.frames.frames import UserStartedSpeakingFrame

    class SlowModel:
        async def tokens(self, messages):
            yield "Hello. "
            await asyncio.sleep(0.1)
            yield "Still here."

    received, upstream = await run_test(
        LocalLLMService(SlowModel()),
        frames_to_send=[
            LLMContextFrame(LLMContext([])),
            SleepFrame(0.02),
            UserStartedSpeakingFrame(),
            SleepFrame(0.15),
        ],
    )
    assert [f.text for f in received if isinstance(f, LLMTextFrame)] == ["Hello. ", "Still here."]
    assert any(isinstance(f, LLMFullResponseEndFrame) for f in received)


@pytest.mark.parametrize("windows,interrupts", [(9, False), (10, True)])
async def test_barge_in_requires_at_least_300ms_of_vad_speech(windows, interrupts):
    from pipecat.audio.vad.vad_analyzer import VADAnalyzer, VADParams
    from pipecat.frames.frames import InputAudioRawFrame

    class VAD(VADAnalyzer):
        def num_frames_required(self):
            return 512

        def voice_confidence(self, audio):
            return 1 if audio.strip(b"\x00") else 0

    vad = VAD(sample_rate=16000, params=VADParams(min_volume=0))
    vad.set_sample_rate(16000)
    stages = cascade.barge_in_stages(vad)
    frames = [InputAudioRawFrame(b"\x00\x40" * 512, 16000, 1) for i in range(windows)]
    frames += [InputAudioRawFrame(b"\x00\x00" * 512, 16000, 1), SleepFrame(0.05)]
    received, upstream = await run_test(Pipeline(stages), frames_to_send=frames)
    assert any(isinstance(f, InterruptionFrame) for f in received) is interrupts
