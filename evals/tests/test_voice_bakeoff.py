import csv
import json
import os
import subprocess
import sys
import wave
from types import SimpleNamespace

import pytest

from evals import voice_bakeoff as bakeoff


@pytest.mark.parametrize(
    "reference,hypothesis,errors,words",
    [
        ("Hello, WORLD!", "hello world", 0, 2),
        ("one two three", "one four", 2, 3),
        ("one", "one two three", 2, 1),
        ("one two", "", 2, 2),
    ],
)
def test_word_edit_distance(reference, hypothesis, errors, words):
    assert bakeoff.word_errors(reference, hypothesis) == (errors, words)


def test_chunk_timing_accounts_for_playback_buffer_and_ignores_empty_chunks():
    ticks = iter([0, 0.1, 0.2, 0.3, 0.6, 0.7])
    sample, pcm, rate = bakeoff.tts_sample(
        iter([(b"12" * 3200, 16000), (b"", 16000), (b"34" * 3200, 16000), (b"56" * 3200, 16000)]),
        clock=lambda: next(ticks),
    )
    assert sample["latency_ms"] == pytest.approx(100)
    assert sample["rtf"] == pytest.approx(0.7 / 0.6)
    assert sample["underruns"] == 1
    assert rate == 16000 and len(pcm) == 19200


@pytest.mark.parametrize(
    "chunks", [[], [(b"1", 16000)], [(b"12", 0)], [(b"12", 8000), (b"12", 16000)]]
)
def test_invalid_tts_output_cannot_produce_passing_metrics(chunks):
    with pytest.raises(ValueError):
        bakeoff.tts_sample(iter(chunks))


def test_summary_uses_p90_and_corpus_wer_instead_of_mean_wer():
    samples = [{"latency_ms": n, "rtf": n / 100, "errors": 1, "words": n} for n in range(1, 31)]
    result = bakeoff.summarize(samples, "stt", 24 * 1024**3)
    assert result["latency_p50_ms"] == 15
    assert result["latency_p90_ms"] == 27
    assert result["wer"] == pytest.approx(30 / sum(range(1, 31)))
    assert result["within_budgets"]
    samples[-1]["latency_ms"] = 9999
    assert bakeoff.summarize(samples, "stt", 24 * 1024**3)["within_budgets"]


@pytest.mark.parametrize("latency,rtf,rss", [(401, 0.1, 1), (1, 0.91, 1)])
def test_tts_budget_failures(latency, rtf, rss):
    result = bakeoff.summarize(
        [{"latency_ms": latency, "rtf": rtf, "underruns": 2}],
        "tts",
        rss,
    )
    assert not result["within_budgets"]
    assert result["underruns"] == 2


def test_three_warm_passes_reuse_all_recordings_without_exposing_gold_to_stt(monkeypatch, tmp_path):
    calls = []

    class Voice:
        def transcribe(self, audio):
            calls.append(audio)
            return "unrelated transcript"

    monkeypatch.setattr(bakeoff, "StubVoice", Voice)
    result = bakeoff.benchmark("whisper", {}, tmp_path, True)
    assert len(calls) == 30 * 4
    assert len(set(calls)) > 1
    assert len(result["samples"]) == 30 * 3
    assert {row["repeat"] for row in result["samples"]} == {1, 2, 3}
    assert result["summary"]["wer"] > 0
    assert result["summary"]["peak_rss_bytes"] > 0
    assert result["summary"]["peak_mlx_bytes"] is None
    assert "within_budgets" in result and "within_budgets" not in result["summary"]


def test_tts_measures_twenty_fixed_sentences_and_saves_only_first_pass(monkeypatch, tmp_path):
    (tmp_path / "clips").mkdir()
    (tmp_path / "source-clips").mkdir()
    calls = []

    class Voice(bakeoff.StubVoice):
        def chunks(self, text, style):
            calls.append((style, text))
            yield from super().chunks(text, style)

    monkeypatch.setattr(bakeoff, "StubVoice", Voice)
    result = bakeoff.benchmark("kokoro", {}, tmp_path, True)
    assert len(calls) == 80 and len(set(calls)) == 20
    assert len(result["samples"]) == 60
    assert len(result["clips"]) == 20
    assert {style for style, text in calls} == {"sassy", "angry", "sad", "deadpan", "tagged"}
    for clip in result["clips"]:
        assert "kokoro" not in clip["file"]
        with wave.open(str(tmp_path / "source-clips" / clip["file"]), "rb") as wav:
            assert wav.getnframes() > 0
            assert wav.getnchannels() == 1
    bakeoff.listening_files(tmp_path, [result])
    with (tmp_path / "listening.csv").open() as stream:
        rows = list(csv.DictReader(stream))
    assert len(rows) == 20
    assert all(row["expressiveness_1_to_5"] == row["naturalness_1_to_5"] == "" for row in rows)
    assert "kokoro" not in (tmp_path / "listening.csv").read_text()
    assert json.loads((tmp_path / "reveal-key.json").read_text())


@pytest.mark.parametrize(
    "error,status",
    [
        (MemoryError("allocation failed"), "oom"),
        (RuntimeError("Metal out of memory"), "oom"),
        (RuntimeError("[metal::malloc] Unable to allocate 4096 bytes"), "oom"),
        (RuntimeError("Insufficient Memory"), "oom"),
        (RuntimeError("unsupported model"), "error"),
    ],
)
def test_worker_records_failures_instead_of_crashing(monkeypatch, tmp_path, error, status):
    def fail(*args):
        raise error

    monkeypatch.setenv("AMES_STUB", "1")
    monkeypatch.setattr(bakeoff, "benchmark", fail)
    monkeypatch.setattr(sys, "argv", ["bakeoff", "--worker", "whisper", "--out", str(tmp_path)])
    bakeoff.main()
    result = json.loads((tmp_path / "whisper.json").read_text())
    assert result["status"] == status and result["within_budgets"] is False


def test_llm_stays_loaded_through_measurement_and_is_closed_on_failure(monkeypatch, tmp_path):
    events = []

    class LLM:
        def __init__(self, path):
            events.append("load llm")

        async def tokens(self, messages):
            events.append("warm llm")
            yield "hello"

        async def close(self):
            events.append("close llm")

    def fail(*args):
        events.append("load voice")
        raise MemoryError()

    monkeypatch.setattr(bakeoff, "MLXLanguageModel", LLM)
    monkeypatch.setattr(bakeoff, "LocalVoice", fail)
    monkeypatch.setattr(bakeoff, "configure_models", lambda: None)
    with pytest.raises(MemoryError):
        bakeoff.benchmark(
            "whisper",
            {"llm": {"path": str(tmp_path)}, "candidates": {"whisper": {}}},
            tmp_path,
            False,
        )
    assert events == ["load llm", "warm llm", "load voice", "close llm"]


def test_process_kill_is_a_missed_budget_without_claiming_oom(monkeypatch, tmp_path):
    monkeypatch.setattr(subprocess, "run", lambda *a, **kw: SimpleNamespace(returncode=-9))
    result = bakeoff.run_candidate("whisper", tmp_path, None, True)
    assert result == {
        "candidate": "whisper",
        "status": "killed",
        "returncode": -9,
        "log": "whisper.log",
        "within_budgets": False,
    }


def test_parent_continues_after_oom_and_timeout_and_preserves_existing_output(
    monkeypatch, tmp_path
):
    def run(key, *args):
        if key == "whisper":
            raise subprocess.TimeoutExpired("worker", 3600)
        return {"candidate": key, "status": "oom", "within_budgets": False}

    output = tmp_path / "run"
    monkeypatch.setenv("AMES_STUB", "1")
    monkeypatch.setattr(bakeoff, "run_candidate", run)
    monkeypatch.setattr(sys, "argv", ["bakeoff", "--out", str(output)])
    bakeoff.main()
    report = (output / "results.json").read_text()
    results = json.loads(report)["results"]
    assert len(results) == 8 and results[0]["status"] == "timeout"
    assert results[-1]["candidate"] == "kokoro"
    with pytest.raises(SystemExit) as error:
        bakeoff.main()
    assert error.value.code == 1
    assert (output / "results.json").read_text() == report


def test_config_requires_local_paths_pins_and_records_source_quantization(tmp_path):
    entry = {"path": str(tmp_path), "revision": "local-pin", "quantization": "4bit"}
    config = {"llm": dict(entry), "candidates": {key: dict(entry) for key in bakeoff.CANDIDATES}}
    assert bakeoff.validate_config(config) == config
    config["candidates"]["kokoro"]["quantization"] = "bf16"
    assert bakeoff.validate_config(config) == config
    config["candidates"]["kokoro"]["path"] = str(tmp_path / "missing")
    with pytest.raises(ValueError, match="directory missing"):
        bakeoff.validate_config(config)
    config["llm"]["revision"] = ""
    with pytest.raises(ValueError, match="revision"):
        bakeoff.validate_config(config)


def test_cli_stubs_never_import_model_libraries(tmp_path):
    script = """
import importlib.abc
import sys
class BlockModels(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path, target=None):
        if fullname.split('.')[0] in {'mlx', 'mlx_lm', 'mlx_audio', 'mlx_whisper', 'torch'}:
            raise AssertionError(f'Model import attempted: {fullname}')
sys.meta_path.insert(0, BlockModels())
from evals.voice_bakeoff import main
main()
"""
    result = subprocess.run(
        [sys.executable, "-c", script, "--worker", "whisper", "--out", str(tmp_path)],
        env={**os.environ, "AMES_STUB": "1"},
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr
    result = json.loads((tmp_path / "whisper.json").read_text())
    assert result["status"] == "ok" and result["summary"]["samples"] == 90


@pytest.mark.parametrize("role", ["stt", "tts"])
def test_local_adapter_uses_local_loader_materializes_audio_and_passes_options(
    monkeypatch, tmp_path, role
):
    import numpy as np

    events = []

    class Model:
        def parameters(self):
            return []

        def generate(self, *args, **kwargs):
            events.append((args, kwargs))
            if role == "stt":
                assert args[0].dtype == np.float32
                assert args[0].tolist() == [0.5, -0.5]
                return SimpleNamespace(text="hello")
            return iter([SimpleNamespace(audio=np.array([0.5, -0.5]), sample_rate=16000)])

    def load(path, *, model_type):
        assert model_type == "whisper" if role == "stt" else model_type == "kokoro"
        assert path == str(tmp_path)
        return Model()

    mx = SimpleNamespace(
        array=np.array,
        eval=lambda *args: events.append("eval"),
        synchronize=lambda: events.append("sync"),
    )
    monkeypatch.setitem(sys.modules, "mlx", SimpleNamespace(core=mx))
    monkeypatch.setitem(sys.modules, "mlx.core", mx)
    monkeypatch.setitem(sys.modules, f"mlx_audio.{role}.utils", SimpleNamespace(load_model=load))
    voice = bakeoff.LocalVoice(
        role,
        {
            "path": str(tmp_path),
            "model_type": "whisper" if role == "stt" else "kokoro",
            "options": {"language": "en"} if role == "stt" else {"stream": True, "voice": "test"},
            "instructions": {"sad": "Speak quietly."},
        },
    )
    if role == "stt":
        assert voice.transcribe(b"\x00\x40\x00\xc0") == "hello"
        assert events[-2][1] == {"language": "en"}
        assert events[-1] == "sync"
    else:
        chunks = list(voice.chunks("hello", "sad"))
        assert len(chunks[0][0]) == 4 and chunks[0][1] == 16000
        assert events[-1] == "eval"
        assert events[-2][1] == {
            "text": "hello",
            "stream": True,
            "voice": "test",
            "instruct": "Speak quietly.",
        }


@pytest.mark.parametrize(
    "config", [[], {}, {"llm": 1, "candidates": dict.fromkeys(bakeoff.CANDIDATES)}]
)
def test_malformed_config_is_rejected(config):
    with pytest.raises(ValueError):
        bakeoff.validate_config(config)


def test_summary_records_both_memory_peaks_without_claiming_system_fit():
    result = bakeoff.summarize(
        [{"latency_ms": 1, "rtf": 0.1, "underruns": 0}],
        "tts",
        30 * 1024**3,
        31 * 1024**3,
    )
    assert result["within_budgets"]
    assert result["peak_mlx_bytes"] == 31 * 1024**3
    assert "memory" not in result["budgets"]


def test_wer_rejects_empty_reference_corpus():
    with pytest.raises(ValueError, match="reference word"):
        bakeoff.summarize([{"latency_ms": 1, "rtf": 1, "errors": 1, "words": 0}], "stt", 1)


@pytest.mark.parametrize("missing", ["llm", "kokoro"])
def test_missing_config_key_has_actionable_diagnosis(tmp_path, missing):
    entry = {"path": str(tmp_path), "revision": "pin", "quantization": "bf16"}
    config = {"llm": entry, "candidates": dict.fromkeys(bakeoff.CANDIDATES, entry)}
    del (config if missing == "llm" else config["candidates"])[missing]
    with pytest.raises(ValueError, match=missing):
        bakeoff.validate_config(config)


@pytest.mark.parametrize("returncode", [1, -9])
def test_worker_logs_diagnostics_and_resolves_paths_before_changing_cwd(
    monkeypatch, tmp_path, returncode
):
    monkeypatch.chdir(tmp_path)

    def run(command, **kwargs):
        assert kwargs["cwd"] == bakeoff.ROOT
        assert command[-1] == str(tmp_path / "config.json")
        assert kwargs["stderr"] == subprocess.STDOUT
        kwargs["stdout"].write("loader failed: missing processor")
        return SimpleNamespace(returncode=returncode)

    monkeypatch.setattr(subprocess, "run", run)
    result = bakeoff.run_candidate("whisper", tmp_path, bakeoff.Path("config.json"), False)
    assert result["log"] == "whisper.log"
    assert (tmp_path / result["log"]).read_text() == "loader failed: missing processor"


def test_worker_runs_from_an_unrelated_directory(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    result = bakeoff.run_candidate("whisper", tmp_path, None, True)
    assert result["status"] == "ok"


def test_worker_keeps_diagnostics_on_timeout(monkeypatch, tmp_path):
    def run(command, **kwargs):
        assert kwargs["env"]["PYTHONUNBUFFERED"] == "1"
        kwargs["stdout"].write("load reached tokenizer")
        raise subprocess.TimeoutExpired(command, 3600)

    monkeypatch.setattr(subprocess, "run", run)
    with pytest.raises(subprocess.TimeoutExpired):
        bakeoff.run_candidate("whisper", tmp_path, None, True)
    assert (tmp_path / "whisper.log").read_text() == "load reached tokenizer"


def test_config_rejects_invalid_model_type(tmp_path):
    entry = {"path": str(tmp_path), "revision": "pin", "quantization": "bf16", "model_type": 9}
    config = {"llm": entry, "candidates": dict.fromkeys(bakeoff.CANDIDATES, entry)}
    with pytest.raises(ValueError, match="Model type"):
        bakeoff.validate_config(config)


def test_blind_files_hide_rate_and_time_and_follow_shuffled_order(monkeypatch, tmp_path):
    (tmp_path / "source-clips").mkdir()
    (tmp_path / "clips").mkdir()
    clips = []
    for index, rate in enumerate([16000, 48000]):
        name = f"random-{index}.wav"
        with wave.open(str(tmp_path / "source-clips" / name), "wb") as wav:
            wav.setparams((1, 2, rate, 0, "NONE", "not compressed"))
            wav.writeframes(b"12" * rate)
        clips.append({"file": name, "style": "sad", "candidate": str(index)})
    monkeypatch.setattr(bakeoff.random.SystemRandom, "shuffle", lambda self, rows: rows.reverse())
    bakeoff.listening_files(tmp_path, [{"status": "ok", "clips": clips}])
    with (tmp_path / "listening.csv").open() as stream:
        rows = list(csv.DictReader(stream))
    assert [row["file"] for row in rows] == ["random-1.wav", "random-0.wav"]
    assert all(row["style"] == "sad" for row in rows)
    assert len({path.stat().st_mtime_ns for path in (tmp_path / "clips").iterdir()}) == 1
    for row in rows:
        with wave.open(str(tmp_path / "clips" / row["file"]), "rb") as wav:
            assert wav.getframerate() == 48000
            assert abs(wav.getnframes() - 48000) <= 3


def test_real_benchmark_records_mlx_allocator_peak(monkeypatch, tmp_path):
    class LLM:
        def __init__(self, path):
            pass

        async def tokens(self, messages):
            yield "hello"

        async def close(self):
            pass

    voice = bakeoff.StubVoice()
    voice.mx = SimpleNamespace(get_peak_memory=lambda: 123456)
    monkeypatch.setattr(bakeoff, "MLXLanguageModel", LLM)
    monkeypatch.setattr(bakeoff, "LocalVoice", lambda *args: voice)
    monkeypatch.setattr(bakeoff, "configure_models", lambda: None)
    result = bakeoff.benchmark(
        "whisper",
        {"llm": {"path": str(tmp_path)}, "candidates": {"whisper": {}}},
        tmp_path,
        False,
    )
    assert result["summary"]["peak_mlx_bytes"] == 123456
