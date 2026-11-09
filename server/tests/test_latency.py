import asyncio
import json
import os
import subprocess
import sys
import wave
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from pipecat.frames.frames import (
    AggregatedTextFrame,
    LLMTextFrame,
    OutputAudioRawFrame,
    TranscriptionFrame,
    TTSAudioRawFrame,
    UserStoppedSpeakingFrame,
)
from pipecat.observers.base_observer import FramePushed
from pipecat.processors.frame_processor import FrameDirection

from server import latency
from server.latency import (
    MANIFEST,
    STAGES,
    LatencyObserver,
    measure,
    percentile,
    read_audio,
    read_manifest,
    report,
)


async def test_observer_times_first_events_and_audio_after_transport_write():
    source, sink, output = object(), object(), object()
    observer = LatencyObserver(output)

    async def push(frame, timestamp, source=source, direction=FrameDirection.DOWNSTREAM):
        await observer.on_push_frame(
            FramePushed(source, sink, frame, direction, timestamp * 1000000)
        )

    await push(UserStoppedSpeakingFrame(), 100)
    await push(TranscriptionFrame("hi", "", "now"), 300)
    await push(LLMTextFrame("Hi"), 450)
    await push(LLMTextFrame("."), 800)
    await push(AggregatedTextFrame("Hi.", "sentence"), 800)
    await push(TTSAudioRawFrame(b"\x01\x00" * 320, 16000, 1), 1200)
    await push(OutputAudioRawFrame(b"\x01\x00" * 320, 16000, 1), 1300)
    assert "audio_sent" not in observer.timestamps
    await push(OutputAudioRawFrame(b"\x01\x00" * 320, 16000, 1), 1400, source=output)
    await push(OutputAudioRawFrame(b"\x01\x00" * 320, 16000, 1), 1450, source=output)
    assert observer.durations() == dict(
        stt_final=200,
        llm_first_token=150,
        first_sentence=350,
        tts_first_chunk=400,
        transport_send=200,
        total=1300,
    )


def test_observer_rejects_incomplete_and_out_of_order_turns():
    observer = LatencyObserver(object())
    with pytest.raises(RuntimeError, match="Missing"):
        observer.durations()
    observer.timestamps = {"turn_end": 2, "stt_final": 1}
    with pytest.raises(RuntimeError, match="Out-of-order"):
        observer.durations()


async def test_all_manifest_records_run_through_stub_pipeline(monkeypatch, capsys):
    monkeypatch.setenv("AMES_STUB", "1")
    results = await measure()
    assert len(results) == len(read_manifest(MANIFEST)) == 30
    for result in results:
        assert set(result) == set(STAGES)
        assert all(value >= 0 for value in result.values())
        assert result["total"] == pytest.approx(sum(v for k, v in result.items() if k != "total"))
    report(results)
    output = capsys.readouterr().out
    assert "p50_ms" in output and "p90_ms" in output
    assert all(name in output for name in STAGES)
    assert "not a hardware benchmark" in output


def test_cli_does_not_import_models_or_need_recorded_wavs():
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            """
import importlib.abc
import sys
class BlockModels(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path, target=None):
        if fullname.split('.')[0] in {
            'mlx', 'mlx_audio', 'mlx_lm', 'mlx_whisper', 'faster_whisper', 'torch'
        }:
            raise AssertionError(f'Model import attempted: {fullname}')
sys.meta_path.insert(0, BlockModels())
from server.latency import main
main()
""",
        ],
        env={**os.environ, "AMES_STUB": "1"},
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr
    assert "30 turns" in result.stdout
    assert "p50_ms" in result.stdout and "p90_ms" in result.stdout


@pytest.mark.parametrize("records", [[], [{}], [dict(id="1", wav="a", transcript="")], [1]])
def test_invalid_manifest_fails(tmp_path, records):
    manifest = tmp_path / "manifest.jsonl"
    manifest.write_text("\n".join(json.dumps(record) for record in records))
    with pytest.raises(ValueError):
        read_manifest(manifest)


def test_duplicate_ids_fail(tmp_path):
    manifest = tmp_path / "manifest.jsonl"
    record = json.dumps(dict(id="1", wav="a", transcript="hi"))
    manifest.write_text(record + "\n" + record)
    with pytest.raises(ValueError, match="Duplicate"):
        read_manifest(manifest)


async def test_real_mode_requires_recordings_before_loading_models(monkeypatch, tmp_path):
    monkeypatch.delenv("AMES_STUB", raising=False)
    manifest = tmp_path / "manifest.jsonl"
    manifest.write_text(
        json.dumps(dict(id="missing", wav=str(tmp_path / "missing.wav"), transcript="hi"))
    )
    with pytest.raises(FileNotFoundError):
        await measure(manifest)


async def test_failed_transport_write_cannot_produce_a_latency_result(monkeypatch):
    monkeypatch.setenv("AMES_STUB", "1")

    async def fail(self, frame):
        return False

    monkeypatch.setattr(latency.DiscardAudioOutput, "write_audio_frame", fail)
    with pytest.raises(RuntimeError, match="Missing latency timestamp: transport_send"):
        await measure()


async def test_empty_transcription_fails_without_waiting_for_a_reply(monkeypatch):
    monkeypatch.setenv("AMES_STUB", "1")

    async def empty(self, audio):
        yield TranscriptionFrame("", "", "now")

    monkeypatch.setattr(latency.StubTranscription, "run_stt", empty)
    with pytest.raises(RuntimeError, match="No final transcript"):
        await measure()


def test_cli_reports_pipeline_startup_error(monkeypatch, capsys):
    monkeypatch.setenv("AMES_STUB", "1")
    wait_for = asyncio.wait_for

    async def short_wait(awaitable, timeout):
        return await wait_for(awaitable, 0.01)

    monkeypatch.setattr(latency.asyncio, "wait_for", short_wait)
    monkeypatch.setattr(
        latency.WorkerRunner, "run", AsyncMock(side_effect=RuntimeError("STT model load failed"))
    )
    monkeypatch.setattr(sys, "argv", ["server.latency"])
    with pytest.raises(SystemExit) as error:
        latency.main()
    assert error.value.code == 1
    assert "Latency harness: STT model load failed" in capsys.readouterr().err


@pytest.mark.parametrize(
    "rate,channels,width,frames",
    [(8000, 1, 2, b"00"), (16000, 2, 2, b"0000"), (16000, 1, 1, b"0"), (16000, 1, 2, b"")],
)
def test_invalid_wav_fails(tmp_path, rate, channels, width, frames):
    path = tmp_path / "invalid.wav"
    with wave.open(str(path), "wb") as wav:
        wav.setparams((channels, width, rate, 0, "NONE", "not compressed"))
        wav.writeframes(frames)
    with pytest.raises(ValueError):
        read_audio(path)


def test_nearest_rank_percentiles():
    assert percentile(list(range(1, 31)), 0.5) == 15
    assert percentile(list(range(1, 31)), 0.9) == 27


def test_cli_reports_malformed_wav_without_traceback(monkeypatch, tmp_path, capsys):
    monkeypatch.delenv("AMES_STUB", raising=False)
    wav = tmp_path / "invalid.wav"
    wav.write_bytes(b"invalid wav container")
    manifest = tmp_path / "manifest.jsonl"
    manifest.write_text(json.dumps(dict(id="invalid", wav=str(wav), transcript="hi")))
    monkeypatch.setattr(sys, "argv", ["server.latency", "--manifest", str(manifest)])
    with pytest.raises(SystemExit) as error:
        latency.main()
    assert error.value.code == 1
    assert "Latency harness:" in capsys.readouterr().err


def test_tts_startup_refusal_exits_before_pipeline_start(monkeypatch, capsys):
    from server.tts import MINIMUM

    monkeypatch.setenv("AMES_STUB", "1")
    stt = SimpleNamespace(cleanup=AsyncMock())
    model = SimpleNamespace(close=AsyncMock())
    voice = SimpleNamespace(
        prepare=AsyncMock(
            side_effect=RuntimeError(f"This Mac is too slow for her voice. Minimum: {MINIMUM}")
        ),
        cleanup=AsyncMock(),
    )
    monkeypatch.setattr(latency, "model_services", lambda: (stt, model))
    monkeypatch.setattr(latency, "tts_service", lambda: voice)
    worker = Mock()
    monkeypatch.setattr(latency, "PipelineWorker", worker)
    monkeypatch.setattr(sys, "argv", ["server.latency"])
    with pytest.raises(SystemExit) as error:
        latency.main()
    assert error.value.code == 1
    assert MINIMUM in capsys.readouterr().err
    worker.assert_not_called()
    stt.cleanup.assert_awaited_once()
    model.close.assert_awaited_once()
    voice.cleanup.assert_awaited_once()
