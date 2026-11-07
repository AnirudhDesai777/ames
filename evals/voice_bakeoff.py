"""Offline voice bake-off; run with AMES_STUB=1 to exercise it without models."""

import argparse
import asyncio
import csv
import importlib.metadata
import json
import os
import platform
import random
import re
import resource
import subprocess
import sys
import time
import uuid
import wave
from pathlib import Path

from server.latency import MANIFEST, ROOT, percentile, read_audio, read_manifest
from server.llm import MLXLanguageModel, configure_models

CANDIDATES = {
    "whisper": ("stt", "mlx-community/whisper-large-v3-turbo"),
    "parakeet": ("stt", "mlx-community/parakeet-tdt-0.6b-v2"),
    "nemotron": ("stt", "nvidia/nemotron-speech-streaming-en-0.6b"),
    "orpheus": ("tts", "mlx-community/orpheus-3b-0.1-ft-4bit"),
    "chatterbox": ("tts", "mlx-community/chatterbox-turbo-4bit"),
    "ming": ("tts", "mlx-community/Ming-3.0-tts-0.5B-bf16"),
    "qwen": ("tts", "mlx-community/Qwen3-TTS-12Hz-1.7B-CustomVoice-8bit"),
    "kokoro": ("tts", "mlx-community/Kokoro-82M-bf16"),
}
# These are probes, not claims that any candidate supports a tag or instruction.
SENTENCES = (
    ("sassy", "Oh, brilliant, another meeting that could have been a sandwich."),
    ("sassy", "You call that a plan? I call it a very confident guess."),
    ("sassy", "Fine, you win this round. Try not to make it your personality."),
    ("sassy", "Sure, because ignoring the deadline has worked so well before."),
    ("angry", "I asked you to listen, and you talked right over me."),
    ("angry", "No, that was unfair, and I am not letting it slide."),
    ("angry", "Stop moving the goalposts every time I get close."),
    ("angry", "I need a minute before I say something I regret."),
    ("sad", "I thought we would have more time together."),
    ("sad", "The house feels very quiet without her here."),
    ("sad", "I know it is over. I just wish it did not hurt so much."),
    ("sad", "Could you stay with me for a little while?"),
    ("deadpan", "The printer has chosen violence again."),
    ("deadpan", "Yes, the weather outside is indeed weather."),
    ("tagged", "<laugh> That was terrible. Tell me another one."),
    ("tagged", "<chuckle> You almost had me there."),
    ("tagged", "<sigh> All right, let us start again."),
    ("tagged", "<gasp> You actually remembered!"),
    ("tagged", "<groan> Not that joke again."),
    ("tagged", "<yawn> One more story, then I am going to bed."),
)
REPEATS = 3


def word_errors(reference, hypothesis):
    """Word edit distance after case folding and removing punctuation."""
    expected = re.findall(r"\w+", reference.casefold())
    actual = re.findall(r"\w+", hypothesis.casefold())
    previous = list(range(len(actual) + 1))
    for i, word in enumerate(expected, 1):
        current = [i]
        for j, other in enumerate(actual, 1):
            current.append(min(current[-1] + 1, previous[j] + 1, previous[j - 1] + (word != other)))
        previous = current
    return previous[-1], len(expected)


def tts_sample(chunks, clock=time.perf_counter):
    start = clock()
    first = None
    ready_until = 0
    duration = 0
    underruns = 0
    audio = bytearray()
    rate = None
    for pcm, sample_rate in chunks:
        arrival = clock() - start
        if not pcm:
            continue
        if sample_rate <= 0 or len(pcm) % 2 or (rate is not None and rate != sample_rate):
            raise ValueError("TTS requires consistent mono PCM16 chunks")
        rate = sample_rate
        seconds = len(pcm) / (2 * rate)
        if first is None:
            first = arrival
            ready_until = arrival
        elif arrival > ready_until:
            underruns += 1
        ready_until = max(ready_until, arrival) + seconds
        duration += seconds
        audio.extend(pcm)
    elapsed = clock() - start
    if first is None:
        raise ValueError("TTS produced no audio")
    return (
        {
            "latency_ms": first * 1000,
            "rtf": elapsed / duration,
            "underruns": underruns,
        },
        bytes(audio),
        rate,
    )


def summarize(samples, role, peak_rss_bytes):
    latencies = [sample["latency_ms"] for sample in samples]
    rtfs = [sample["rtf"] for sample in samples]
    summary = {
        "samples": len(samples),
        "latency_p50_ms": percentile(latencies, 0.5),
        "latency_p90_ms": percentile(latencies, 0.9),
        "rtf_p50": percentile(rtfs, 0.5),
        "rtf_p90": percentile(rtfs, 0.9),
        "peak_rss_bytes": peak_rss_bytes,
    }
    summary["budgets"] = {
        "latency": summary["latency_p90_ms"] <= (200 if role == "stt" else 400),
        "memory": peak_rss_bytes <= 24 * 1024**3,
    }
    if role == "stt":
        summary["wer"] = sum(s["errors"] for s in samples) / sum(s["words"] for s in samples)
    else:
        summary["underruns"] = sum(s["underruns"] for s in samples)
        summary["budgets"]["rtf"] = summary["rtf_p90"] <= 0.9
    summary["within_budgets"] = all(summary["budgets"].values())
    return summary


class StubVoice:
    def transcribe(self, audio):
        return "hi there"

    def chunks(self, text, style):
        yield b"\x00\x20\x00\xe0" * 800, 16000


class LocalVoice:
    def __init__(self, role, entry):
        import mlx.core as mx

        if role == "stt":
            from mlx_audio.stt.utils import load_model
        else:
            from mlx_audio.tts.utils import load_model

        self.mx = mx
        self.entry = entry
        self.model = load_model(str(Path(entry["path"]).expanduser()))
        mx.eval(self.model.parameters())
        mx.synchronize()

    def transcribe(self, audio):
        import numpy as np

        signal = np.frombuffer(audio, dtype="<i2").astype(np.float32) / 32768
        result = self.model.generate(self.mx.array(signal), **self.entry.get("options", {}))
        self.mx.synchronize()
        return result.text

    def chunks(self, text, style):
        import numpy as np

        options = dict(self.entry.get("options", {}))
        instruction = self.entry.get("instructions", {}).get(style)
        if instruction:
            options["instruct"] = instruction
        for result in self.model.generate(text=text, **options):
            self.mx.eval(result.audio)
            values = np.asarray(result.audio).squeeze()
            if values.ndim != 1:
                raise ValueError("Expected mono TTS audio")
            pcm = (np.clip(values, -1, 1) * 32767).astype("<i2").tobytes()
            yield pcm, result.sample_rate


def validate_config(config):
    if not isinstance(config, dict) or not isinstance(config.get("candidates"), dict):
        raise ValueError("Config requires llm and candidates objects")
    entries = [config["llm"], *(config["candidates"][key] for key in CANDIDATES)]
    for entry in entries:
        if not isinstance(entry, dict):
            raise ValueError("Each model entry must be an object")
        if any(
            not isinstance(entry.get(k), str) or not entry[k].strip()
            for k in ("path", "revision", "quantization")
        ):
            raise ValueError("Each model needs path, revision and quantization strings")
        if not Path(entry["path"]).expanduser().is_dir():
            raise ValueError(f"Local model directory missing: {entry['path']}")
        if any(not isinstance(entry.get(key, {}), dict) for key in ("options", "instructions")):
            raise ValueError("Model options and instructions must be objects")
    if len({entry["quantization"] for entry in entries[1:]}) != 1:
        raise ValueError("Voice candidates must use the same quantization tier")
    return config


async def warm_llm(model):
    async for text in model.tokens([{"role": "user", "content": "Say hello."}]):
        pass


def benchmark(key, config, output, stub):
    role, repo = CANDIDATES[key]
    records = read_manifest(MANIFEST)
    if len(records) != 30:
        raise ValueError("Voice bake-off requires the 30 latency recordings")
    audios = [read_audio(ROOT / row["wav"]) for row in records] if role == "stt" else []
    llm = None
    try:
        if not stub:
            configure_models()
            llm = MLXLanguageModel(Path(config["llm"]["path"]).expanduser())
            asyncio.run(warm_llm(llm))
        started = time.perf_counter()
        voice = StubVoice() if stub else LocalVoice(role, config["candidates"][key])
        cold_load = time.perf_counter() - started
        samples = []
        clips = []
        # One complete untimed pass triggers lazy loads before three measured passes.
        for repeat in range(-1, REPEATS):
            if role == "stt":
                for row, audio in zip(records, audios, strict=True):
                    started = time.perf_counter()
                    transcript = voice.transcribe(audio)
                    elapsed = time.perf_counter() - started
                    if not isinstance(transcript, str):
                        raise ValueError("STT must return a final transcript string")
                    errors, words = word_errors(row["transcript"], transcript)
                    if repeat >= 0:
                        samples.append(
                            {
                                "id": row["id"],
                                "repeat": repeat + 1,
                                "latency_ms": elapsed * 1000,
                                "rtf": elapsed / (len(audio) / 32000),
                                "errors": errors,
                                "words": words,
                            }
                        )
            else:
                for index, (style, text) in enumerate(SENTENCES, 1):
                    sample, pcm, rate = tts_sample(voice.chunks(text, style))
                    if repeat >= 0:
                        samples.append(sample | {"id": index, "repeat": repeat + 1})
                    if repeat == 0:
                        name = uuid.uuid4().hex + ".wav"
                        with wave.open(str(output / "clips" / name), "wb") as wav:
                            wav.setparams((1, 2, rate, 0, "NONE", "not compressed"))
                            wav.writeframes(pcm)
                        clips.append(
                            {
                                "file": name,
                                "candidate": key,
                                "sentence": index,
                                "style": style,
                                "tag": " ".join(re.findall(r"<[^>]+>", text)),
                                "instruction": ""
                                if stub
                                else config["candidates"][key]
                                .get("instructions", {})
                                .get(style, ""),
                            }
                        )
        peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        peak *= 1 if sys.platform == "darwin" else 1024
        return {
            "candidate": key,
            "repo": repo,
            "role": role,
            "status": "ok",
            "cold_load_s": cold_load,
            "summary": summarize(samples, role, peak),
            "samples": samples,
            "clips": clips,
        }
    finally:
        if llm:
            asyncio.run(llm.close())


def write_json(path, value):
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(value, indent=2, allow_nan=False) + "\n")
    temporary.replace(path)


def run_candidate(key, output, config_path, stub):
    command = [sys.executable, "-m", "evals.voice_bakeoff", "--out", str(output), "--worker", key]
    if config_path:
        command.extend(["--config", str(config_path)])
    result = subprocess.run(
        command,
        env={**os.environ, "AMES_STUB": "1" if stub else "0"},
        capture_output=True,
        text=True,
        timeout=3600,
    )
    if result.returncode:
        # A SIGKILL may be the OS memory killer; do not claim its cause is known.
        return {
            "candidate": key,
            "status": "killed" if result.returncode < 0 else "error",
            "returncode": result.returncode,
            "within_budgets": False,
        }
    return json.loads((output / f"{key}.json").read_text())


def listening_files(output, results):
    clips = [clip for result in results if result["status"] == "ok" for clip in result["clips"]]
    random.SystemRandom().shuffle(clips)
    write_json(output / "reveal-key.json", clips)
    with (output / "listening.csv").open("w", newline="") as stream:
        writer = csv.writer(stream)
        writer.writerow(
            [
                "file",
                "expressiveness_1_to_5",
                "naturalness_1_to_5",
                "tag_or_instruction_rendered",
                "notes",
            ]
        )
        writer.writerows([clip["file"], "", "", "", ""] for clip in clips)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, required=True, help="new run directory")
    parser.add_argument("--config", type=Path, help="local model paths and provenance JSON")
    parser.add_argument("--worker", choices=CANDIDATES, help=argparse.SUPPRESS)
    args = parser.parse_args()
    stub = os.environ.get("AMES_STUB") == "1"
    try:
        config = {}
        if not stub:
            if platform.system() != "Darwin" or platform.machine() != "arm64":
                raise ValueError("Real models require Apple Silicon; use AMES_STUB=1 for CI")
            if not args.config:
                raise ValueError("Real runs require --config with local model directories")
            config = validate_config(json.loads(args.config.read_text()))
        if args.worker:
            try:
                result = benchmark(args.worker, config, args.out, stub)
            except Exception as error:
                oom = isinstance(error, MemoryError) or "out of memory" in str(error).casefold()
                result = {
                    "candidate": args.worker,
                    "status": "oom" if oom else "error",
                    "error": str(error),
                    "within_budgets": False,
                }
            write_json(args.out / f"{args.worker}.json", result)
            return
        args.out.mkdir(parents=True, exist_ok=False)
        (args.out / "clips").mkdir()
        versions = {}
        for package in ("mlx", "mlx-lm", "mlx-audio"):
            try:
                versions[package] = importlib.metadata.version(package)
            except importlib.metadata.PackageNotFoundError:
                versions[package] = None
        report = {
            "mode": "stub" if stub else "local",
            "platform": platform.platform(),
            "python": platform.python_version(),
            "versions": versions,
            "config": config,
            "warm_repeats": REPEATS,
            "results": [],
        }
        for key in CANDIDATES:
            try:
                result = run_candidate(key, args.out.resolve(), args.config, stub)
            except subprocess.TimeoutExpired:
                result = {"candidate": key, "status": "timeout", "within_budgets": False}
            report["results"].append(result)
            write_json(args.out / "results.json", report)
            print(f"{key}: {result['status']}", flush=True)
        listening_files(args.out, report["results"])
        print(
            "Stub results are not hardware evidence." if stub else "Listening scores are pending."
        )
    except (OSError, ValueError, KeyError, TypeError) as error:
        parser.exit(1, f"Voice bake-off: {error}\n")


if __name__ == "__main__":
    main()
