import asyncio
import os
import subprocess
import sys
import wave
from array import array
from pathlib import Path

import pytest
from pipecat.frames.frames import InputAudioRawFrame, OutputAudioRawFrame, TextFrame
from pipecat.processors.frame_processor import FrameDirection
from pipecat.tests.utils import run_test

from server.echo import echo_connection, echo_pipeline, run_echo
from server.stubs import StubLLM, StubSTT, StubTTS, model_stages


async def test_wav_echo_is_non_silent(monkeypatch):
    monkeypatch.setenv("AMES_STUB", "1")
    with wave.open(str(Path(__file__).parent / "fixtures" / "tone.wav"), "rb") as wav:
        assert wav.getsampwidth() == 2
        audio = wav.readframes(wav.getnframes())
        frame = InputAudioRawFrame(audio, wav.getframerate(), wav.getnchannels())

    received, upstream = await asyncio.wait_for(
        run_test(
            echo_pipeline(),
            frames_to_send=[frame],
            expected_down_frames=[OutputAudioRawFrame],
        ),
        timeout=10,
    )
    assert not upstream
    assert received[0].audio == audio
    assert received[0].sample_rate == 16000
    assert received[0].num_channels == 1
    assert max(abs(sample) for sample in array("h", received[0].audio)) > 1000


async def test_control_frames_pass_through(monkeypatch):
    monkeypatch.setenv("AMES_STUB", "1")
    frame = TextFrame("control")
    received, upstream = await run_test(echo_pipeline(), frames_to_send=[frame])
    assert received == [frame]
    assert not upstream


async def test_upstream_audio_is_not_echoed(monkeypatch):
    monkeypatch.setenv("AMES_STUB", "1")
    frame = InputAudioRawFrame(b"\x01\x00" * 160, 16000, 1)
    received, upstream = await run_test(
        echo_pipeline(),
        frames_to_send=[frame],
        frames_to_send_direction=FrameDirection.UPSTREAM,
    )
    assert not received
    assert upstream == [frame]


def test_stub_selection(monkeypatch):
    monkeypatch.setenv("AMES_STUB", "1")
    assert [type(stage) for stage in model_stages()] == [StubSTT, StubLLM, StubTTS]


@pytest.mark.parametrize("value", [None, "0", "true"])
def test_real_models_are_not_implemented(monkeypatch, value):
    monkeypatch.delenv("AMES_STUB", raising=False)
    if value is not None:
        monkeypatch.setenv("AMES_STUB", value)
    with pytest.raises(RuntimeError, match="AMES_STUB=1"):
        model_stages()


async def test_server_ice_servers_are_empty():
    connection = echo_connection()
    try:
        assert connection.ice_servers == []
    finally:
        await connection.disconnect()


async def test_failed_pipeline_setup_closes_connection(monkeypatch):
    monkeypatch.delenv("AMES_STUB", raising=False)
    connection = echo_connection()
    with pytest.raises(RuntimeError, match="AMES_STUB=1"):
        await run_echo(connection)
    assert connection.pc.connectionState == "closed"


def test_stub_import_does_not_load_mlx():
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            """
import importlib.abc
import sys

class BlockMLX(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path, target=None):
        if fullname.split('.')[0] in {'mlx', 'mlx_lm', 'mlx_whisper'}:
            raise AssertionError(f'MLX import attempted: {fullname}')

sys.meta_path.insert(0, BlockMLX())
from server.echo import echo_pipeline
assert echo_pipeline() is not None
""",
        ],
        env={**os.environ, "AMES_STUB": "1"},
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr
