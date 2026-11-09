import asyncio
import sys
from threading import Event, get_ident
from types import SimpleNamespace
from unittest.mock import Mock

import numpy as np
import pytest
from pipecat.frames.frames import (
    AggregatedTextFrame,
    InterruptionFrame,
    LLMContextFrame,
    LLMFullResponseEndFrame,
    LLMFullResponseStartFrame,
    LLMTextFrame,
    StartFrame,
    TTSAudioRawFrame,
    TTSTextFrame,
)
from pipecat.pipeline.pipeline import Pipeline
from pipecat.processors.aggregators.llm_context import LLMContext
from pipecat.tests.utils import SleepFrame, run_test

from server import tts
from server.cascade import LocalLLMService, ReplySanitizer, SentenceSplitter, SpokenHistory
from server.latency import DiscardAudioOutput
from server.tts import MINIMUM, MOOD_INSTRUCTIONS, QwenTTSService


@pytest.fixture
async def voice(tmp_path, monkeypatch):
    threads = []

    def generate(**kwargs):
        threads.append(get_ident())
        yield SimpleNamespace(audio=np.array([-2, -0.5, 0, 0.5, 2]), sample_rate=24000)

    model = SimpleNamespace(generate=Mock(side_effect=generate), parameters=Mock(return_value=[]))
    loader = Mock(return_value=model)
    mx = SimpleNamespace(eval=Mock(), synchronize=Mock())
    monkeypatch.setitem(sys.modules, "mlx", SimpleNamespace(core=mx))
    monkeypatch.setitem(sys.modules, "mlx.core", mx)
    monkeypatch.setitem(sys.modules, "mlx_audio.tts.utils", SimpleNamespace(load_model=loader))
    service = QwenTTSService(tmp_path)
    service.load_model()
    try:
        yield SimpleNamespace(service=service, model=model, loader=loader, threads=threads)
    finally:
        await service.cleanup()


@pytest.mark.parametrize("mood", MOOD_INSTRUCTIONS)
async def test_streaming_options_mood_pcm_and_local_loading(voice, mood):
    voice.service.mood = mood
    frames = [frame async for frame in voice.service.run_tts("Hi.", "sentence")]
    audio = [frame for frame in frames if isinstance(frame, TTSAudioRawFrame)]
    assert len(audio) == 1
    assert audio[0].sample_rate == 24000 and audio[0].num_channels == 1
    assert audio[0].context_id == "sentence"
    np.testing.assert_array_equal(
        np.frombuffer(audio[0].audio, dtype="<i2"), [-32767, -16383, 0, 16383, 32767]
    )
    options = dict(
        text="Hi.", voice="Ryan", lang_code="english", stream=True, streaming_interval=0.1
    )
    if MOOD_INSTRUCTIONS[mood]:
        options["instruct"] = MOOD_INSTRUCTIONS[mood]
    voice.model.generate.assert_called_once_with(**options)
    voice.loader.assert_called_once_with(str(voice.service.model_path), model_type="qwen3_tts")
    assert voice.threads[0] != get_ident()


@pytest.mark.parametrize("factor,refuse", [(0.89, False), (0.9, False), (0.91, True)])
async def test_startup_speed_gate_with_stub_audio(tmp_path, monkeypatch, factor, refuse):
    service = QwenTTSService(tmp_path)
    service.load_model = Mock()
    service.chunks = Mock(side_effect=lambda *args: iter([(b"\x01\x00" * 24000, 24000)]))
    monkeypatch.setattr(tts, "perf_counter", Mock(side_effect=[0, factor]))
    try:
        if refuse:
            with pytest.raises(RuntimeError, match="This Mac is too slow") as error:
                await service.start(StartFrame())
            assert MINIMUM in str(error.value)
            assert not service.ready
        else:
            await service.prepare()
            await service.prepare()
            assert service.ready
        assert service.chunks.call_count == 2  # warm-up, then measured audio
    finally:
        await service.cleanup()


async def test_empty_self_test_refuses_without_fallback(tmp_path):
    service = QwenTTSService(tmp_path)
    service.load_model = Mock()
    service.chunks = Mock(return_value=iter(()))
    try:
        with pytest.raises(RuntimeError, match="Minimum: Apple Silicon with 24 GB"):
            await service.prepare()
    finally:
        await service.cleanup()


async def test_sentence_streaming_sanitizes_and_sets_mood(voice):
    voice.service.ready = True
    received, upstream = await run_test(
        Pipeline([ReplySanitizer(voice.service), SentenceSplitter(), voice.service]),
        frames_to_send=[
            LLMFullResponseStartFrame(),
            LLMTextFrame("<state mood=am"),
            LLMTextFrame("used crisis=0>Hello <laugh>there. Next"),
            SleepFrame(0.05),
            LLMTextFrame(" sentence."),
            LLMFullResponseEndFrame(),
        ],
    )
    assert [call.kwargs["text"] for call in voice.model.generate.call_args_list] == [
        "Hello there.",
        "Next sentence.",
    ]
    assert all(
        call.kwargs["instruct"] == MOOD_INSTRUCTIONS["amused"]
        for call in voice.model.generate.call_args_list
    )
    assert len([frame for frame in received if isinstance(frame, TTSAudioRawFrame)]) == 2


async def test_interruption_stops_audio_and_keeps_only_completed_sentences(voice):
    voice.service.ready = True
    synthesis = []
    cut = Event()

    def chunks(text, mood, cancelled):
        synthesis.append(text)
        try:
            for i in range(40 if text == "Cut sentence." else 2):
                if cancelled.is_set():
                    return
                yield b"\x01\x20" * 480, 24000
        finally:
            if text == "Cut sentence.":
                cut.set()

    voice.service.chunks = chunks
    history = [{"role": "user", "content": "hi"}]
    written = []
    interruption_time = []
    interrupted = asyncio.Event()

    class Output(DiscardAudioOutput):
        async def process_frame(self, frame, direction):
            if isinstance(frame, InterruptionFrame):
                interruption_time.append(asyncio.get_running_loop().time())
                interrupted.set()
            await super().process_frame(frame, direction)

        async def write_audio_frame(self, frame):
            await asyncio.sleep(0.02)
            written.append((asyncio.get_running_loop().time(), frame.audio))
            return True

    output = Output()
    received, upstream = await asyncio.wait_for(
        run_test(
            Pipeline([voice.service, output, SpokenHistory(history)]),
            frames_to_send=[
                LLMFullResponseStartFrame(),
                AggregatedTextFrame("Finished sentence.", "sentence"),
                AggregatedTextFrame("Cut sentence.", "sentence"),
                AggregatedTextFrame("Queued sentence.", "sentence"),
                LLMFullResponseEndFrame(),
                SleepFrame(0.15),
                InterruptionFrame(),
                SleepFrame(0.25),
            ],
        ),
        5,
    )
    assert interrupted.is_set() and cut.is_set()
    assert history == [
        {"role": "user", "content": "hi"},
        {"role": "assistant", "content": "Finished sentence."},
    ]
    assert all(stamp - interruption_time[0] < 0.15 for stamp, audio in written)
    assert [frame.text for frame in received if isinstance(frame, TTSTextFrame)] == [
        "Finished sentence."
    ]


async def test_worker_cancel_drops_remaining_generation(voice):
    entered, release = Event(), Event()
    generated = []

    def generate(**kwargs):
        for index in range(5):
            generated.append(index)
            if index == 1:
                entered.set()
                assert release.wait(2)
            yield SimpleNamespace(audio=np.ones(240), sample_rate=24000)

    voice.model.generate.side_effect = generate

    async def consume():
        return [frame async for frame in voice.service.run_tts("Hi.", "id")]

    task = asyncio.create_task(consume())
    try:
        assert await asyncio.to_thread(entered.wait, 2)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        release.set()
        await asyncio.get_running_loop().run_in_executor(voice.service.executor, lambda: None)
        assert generated == [0, 1]
    finally:
        release.set()


async def test_synthesis_failure_is_private_and_has_no_sentence_receipt(voice):
    voice.model.generate.side_effect = RuntimeError("private model details")
    with pytest.raises(RuntimeError, match="^local_synthesis_failed$"):
        [frame async for frame in voice.service.run_tts("Hi.", "id")]


def test_missing_model_directory_fails(tmp_path):
    with pytest.raises(ValueError, match="existing local model directory"):
        QwenTTSService(tmp_path / "missing")


async def test_interruption_drops_tts_waiting_behind_active_synthesis(voice):
    voice.service.ready = True
    started = Event()
    synthesis = []
    cancelled_flags = []

    def chunks(text, mood, cancelled):
        synthesis.append(text)
        cancelled_flags.append(cancelled)
        started.set()
        assert cancelled.wait(2)
        if not cancelled.is_set():
            yield b"\x01\x00" * 240, 24000

    voice.service.chunks = chunks
    received, upstream = await run_test(
        voice.service,
        frames_to_send=[
            LLMFullResponseStartFrame(),
            AggregatedTextFrame("Long sentence.", "sentence"),
            AggregatedTextFrame("Queued sentence.", "sentence"),
            SleepFrame(0.05),
            InterruptionFrame(),
            SleepFrame(0.05),
        ],
    )
    assert started.is_set()
    assert synthesis == ["Long sentence."]
    assert cancelled_flags[0].is_set()
    assert not any(isinstance(frame, (TTSTextFrame, TTSAudioRawFrame)) for frame in received)


async def test_first_sentence_speaks_while_llm_is_still_generating(voice):
    voice.service.ready = True
    spoken = Event()
    generate = voice.model.generate.side_effect

    def speech(**kwargs):
        spoken.set()
        yield from generate(**kwargs)

    class Model:
        async def tokens(self, messages):
            yield "<state mood=neutral crisis=0>First sentence. Next"
            assert await asyncio.to_thread(spoken.wait, 2)
            yield " sentence."

    voice.model.generate.side_effect = speech
    received, upstream = await run_test(
        Pipeline(
            [
                LocalLLMService(Model()),
                ReplySanitizer(voice.service),
                SentenceSplitter(),
                voice.service,
            ]
        ),
        frames_to_send=[LLMContextFrame(LLMContext([]))],
    )
    assert spoken.is_set()
    assert len([frame for frame in received if isinstance(frame, TTSAudioRawFrame)]) == 2
