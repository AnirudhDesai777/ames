import asyncio
import sys
from threading import get_ident
from types import SimpleNamespace
from unittest.mock import Mock

import numpy as np
import pytest
from loguru import logger
from pipecat.frames.frames import (
    ErrorFrame,
    InputAudioRawFrame,
    TranscriptionFrame,
    VADUserStartedSpeakingFrame,
    VADUserStoppedSpeakingFrame,
)
from pipecat.tests.utils import SleepFrame, run_test

from server.stt import ParakeetSTTService


@pytest.fixture
def runtime(monkeypatch):
    threads = []

    def generate(signal):
        threads.append(get_ident())
        return SimpleNamespace(text="hello there")

    model = SimpleNamespace(generate=Mock(side_effect=generate), parameters=Mock(return_value=[]))

    def load(*args, **kwargs):
        threads.append(get_ident())
        return model

    loader = Mock(side_effect=load)
    mx = SimpleNamespace(array=Mock(side_effect=np.asarray), eval=Mock(), synchronize=Mock())
    monkeypatch.setitem(sys.modules, "mlx", SimpleNamespace(core=mx))
    monkeypatch.setitem(sys.modules, "mlx.core", mx)
    monkeypatch.setitem(sys.modules, "mlx_audio.stt.utils", SimpleNamespace(load_model=loader))
    monkeypatch.setenv("HF_HOME", "unused")
    monkeypatch.setenv("HF_HUB_OFFLINE", "0")
    return SimpleNamespace(model=model, mx=mx, loader=loader, threads=threads)


@pytest.fixture
async def service(tmp_path, runtime):
    stt = ParakeetSTTService(tmp_path)
    try:
        await asyncio.get_running_loop().run_in_executor(stt.executor, stt.load_model)
        yield stt
    finally:
        await stt.cleanup()


async def test_pcm16_becomes_float32_and_final_transcript(service, runtime):
    pcm = np.array([-32768, -16384, 0, 16384, 32767], dtype="<i2")
    frames = [frame async for frame in service.run_stt(pcm.tobytes())]
    assert len(frames) == 1
    frame = frames[0]
    assert isinstance(frame, TranscriptionFrame)
    assert frame.text == "hello there" and frame.timestamp and frame.language == "en"
    signal = runtime.model.generate.call_args.args[0]
    assert signal.dtype == np.float32 and signal.ndim == 1
    np.testing.assert_array_equal(signal, [-1, -0.5, 0, 0.5, 32767 / 32768])
    runtime.mx.array.assert_called_once()
    assert len(set(runtime.threads)) == 1
    assert runtime.threads[0] != get_ident()


@pytest.mark.parametrize("text", ["", " \n\t"])
async def test_blank_transcript_yields_no_frame(service, runtime, text):
    runtime.model.generate.side_effect = None
    runtime.model.generate.return_value = SimpleNamespace(text=text)
    assert [frame async for frame in service.run_stt(b"\x00\x00")] == []


async def test_transcription_failure_yields_error_and_worker_recovers(service, runtime):
    failure = RuntimeError("private model details")
    runtime.model.generate.side_effect = failure
    messages = []
    sink = logger.add(messages.append, format="{message}", backtrace=False, diagnose=False)
    try:
        frames = [frame async for frame in service.run_stt(b"\x00\x00")]
    finally:
        logger.remove(sink)
    assert len(frames) == 1 and isinstance(frames[0], ErrorFrame)
    assert frames[0].error == "local_transcription_failed"
    errors = [message for message in messages if message.record["level"].name == "ERROR"]
    assert len(errors) == 1
    assert errors[0].record["message"] == "local_transcription_failed"
    assert errors[0].record["exception"].value is failure
    assert "Traceback (most recent call last)" in errors[0]
    assert "RuntimeError: private model details" in errors[0]
    runtime.model.generate.side_effect = None
    runtime.model.generate.return_value = SimpleNamespace(text="recovered")
    assert [frame.text async for frame in service.run_stt(b"\x00\x00")] == ["recovered"]


async def test_startup_loads_once_and_vad_segments_receive_raw_pcm(tmp_path, runtime):
    stt = ParakeetSTTService(tmp_path)
    pcm = b"\x00\x40" * 320
    received, upstream = await run_test(
        stt,
        frames_to_send=[
            VADUserStartedSpeakingFrame(),
            InputAudioRawFrame(pcm, 16000, 1),
            SleepFrame(0.05),
            VADUserStoppedSpeakingFrame(),
            SleepFrame(0.05),
            VADUserStartedSpeakingFrame(),
            InputAudioRawFrame(pcm, 16000, 1),
            SleepFrame(0.05),
            VADUserStoppedSpeakingFrame(),
        ],
    )
    assert [frame.text for frame in received if isinstance(frame, TranscriptionFrame)] == [
        "hello there",
        "hello there",
    ]
    runtime.loader.assert_called_once_with(str(tmp_path), model_type="parakeet")
    runtime.mx.eval.assert_called_once_with([])
    assert runtime.model.generate.call_count == 2
    for call in runtime.model.generate.call_args_list:
        signal = call.args[0]
        np.testing.assert_array_equal(signal[:320], np.full(320, 0.5, dtype=np.float32))
        np.testing.assert_array_equal(signal[320:], np.zeros(8000, dtype=np.float32))
    assert len(set(runtime.threads)) == 1 and runtime.threads[0] != get_ident()
    with pytest.raises(RuntimeError, match="shutdown"):
        stt.executor.submit(lambda: None)


def test_model_directory_must_exist(tmp_path):
    with pytest.raises(ValueError, match="existing local model directory"):
        ParakeetSTTService(tmp_path / "missing")


async def test_new_session_reuses_loaded_model_with_fresh_processor(service, runtime):
    session = service.new_session()
    try:
        assert session is not service and session.executor is not service.executor
        assert session.model is service.model and session.mx is service.mx
        assert [frame.text async for frame in session.run_stt(b"\x00\x40" * 320)] == ["hello there"]
        runtime.loader.assert_called_once()
    finally:
        await session.cleanup()
