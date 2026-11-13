"""Replay the latency manifest through Pipecat and report stage percentiles."""

import argparse
import asyncio
import json
import math
import os
import wave
from pathlib import Path

from pipecat.frames.frames import (
    AggregatedTextFrame,
    EndFrame,
    ErrorFrame,
    LLMFullResponseEndFrame,
    LLMTextFrame,
    OutputAudioRawFrame,
    StartFrame,
    TranscriptionFrame,
    TTSAudioRawFrame,
    UserStoppedSpeakingFrame,
)
from pipecat.observers.base_observer import BaseObserver, FramePushed
from pipecat.pipeline.pipeline import Pipeline
from pipecat.pipeline.worker import PipelineParams, PipelineWorker
from pipecat.processors.frame_processor import FrameDirection
from pipecat.transports.base_output import BaseOutputTransport
from pipecat.transports.base_transport import TransportParams
from pipecat.workers.runner import WorkerRunner

from server.cascade import (
    PlainPrompt,
    SpokenHistory,
    StubTranscription,
    cascade_stages,
    log_errors,
    model_services,
    tts_service,
)

ROOT = Path(__file__).resolve().parent.parent
MANIFEST = ROOT / "evals/latency/manifest.jsonl"
STAGES = {
    "stt_final": ("turn_end", "stt_final"),
    "llm_first_token": ("stt_final", "llm_first_token"),
    "first_sentence": ("llm_first_token", "first_sentence"),
    "tts_first_chunk": ("first_sentence", "tts_first_chunk"),
    "transport_send": ("tts_first_chunk", "audio_sent"),
    "total": ("turn_end", "audio_sent"),
}


class LatencyObserver(BaseObserver):
    def __init__(self, output):
        super().__init__()
        self.output = output
        self.timestamps = {}
        self.started = asyncio.Event()
        self.finished = asyncio.Event()
        self.failure = None

    async def on_push_frame(self, data: FramePushed):
        frame = data.frame
        if isinstance(frame, ErrorFrame):
            self.failure = RuntimeError("Cascade failed; no latency result for this turn")
            self.finished.set()
        if data.direction != FrameDirection.DOWNSTREAM:
            return
        if isinstance(frame, UserStoppedSpeakingFrame) and data.first_push:
            self.timestamps = {"turn_end": data.timestamp}
            self.started.set()
        elif "turn_end" in self.timestamps:
            name = None
            if isinstance(frame, TranscriptionFrame):
                name = "stt_final"
            elif isinstance(frame, LLMTextFrame):
                name = "llm_first_token"
            elif isinstance(frame, AggregatedTextFrame):
                name = "first_sentence"
            elif isinstance(frame, TTSAudioRawFrame):
                name = "tts_first_chunk"
            if name:
                self.timestamps.setdefault(name, data.timestamp)
            # Output transport pushes audio downstream only after a successful write.
            if data.source is self.output:
                if isinstance(frame, OutputAudioRawFrame) and frame.audio:
                    self.timestamps.setdefault("audio_sent", data.timestamp)
                elif isinstance(frame, LLMFullResponseEndFrame):
                    self.finished.set()

    def durations(self):
        if self.failure:
            raise self.failure
        result = {}
        for name, (start, end) in STAGES.items():
            if start not in self.timestamps or end not in self.timestamps:
                raise RuntimeError(f"Missing latency timestamp: {name}")
            elapsed = (self.timestamps[end] - self.timestamps[start]) / 1_000_000
            if elapsed < 0:
                raise RuntimeError(f"Out-of-order latency timestamps: {name}")
            result[name] = elapsed
        return result


class DiscardAudioOutput(BaseOutputTransport):
    """Exercise Pipecat's real audio queue without a sound device or connection."""

    def __init__(self):
        super().__init__(TransportParams(audio_out_enabled=True, audio_out_end_silence_secs=0))

    async def start(self, frame: StartFrame):
        await super().start(frame)
        await self.set_transport_ready(frame)

    async def write_audio_frame(self, frame):
        return True


def read_manifest(path: Path):
    records = [json.loads(line) for line in path.read_text().splitlines() if line.strip()]
    if not records:
        raise ValueError("Latency manifest is empty")
    seen = set()
    for record in records:
        if not isinstance(record, dict) or any(
            not isinstance(record.get(key), str) or not record[key].strip()
            for key in ("id", "wav", "transcript")
        ):
            raise ValueError("Latency records require nonempty id, wav, and transcript strings")
        if record["id"] in seen:
            raise ValueError("Duplicate latency record id")
        seen.add(record["id"])
    return records


def read_audio(path: Path):
    with wave.open(str(path), "rb") as wav:
        if (wav.getframerate(), wav.getnchannels(), wav.getsampwidth(), wav.getcomptype()) != (
            16000,
            1,
            2,
            "NONE",
        ):
            raise ValueError(f"Expected 16 kHz mono PCM16 WAV: {path}")
        audio = wav.readframes(wav.getnframes())
        if not audio or len(audio) != wav.getnframes() * 2:
            raise ValueError(f"Empty or truncated WAV: {path}")
        return audio


async def measure(manifest: Path = MANIFEST):
    records = read_manifest(manifest)
    stub = os.environ.get("AMES_STUB") == "1"
    # The recorded WAVs are maintainer-provided. CI reuses the generated S1 fixture.
    audio = [
        read_audio(ROOT / "server/tests/fixtures/tone.wav" if stub else ROOT / record["wav"])
        for record in records
    ]
    stt, model = model_services()
    tts = tts_service()
    if hasattr(tts, "prepare"):
        try:
            await tts.prepare()
        except Exception:
            await stt.cleanup()
            await tts.cleanup()
            await model.close()
            raise
    prompt = PlainPrompt()
    output = DiscardAudioOutput()
    observer = LatencyObserver(output)
    worker = PipelineWorker(
        Pipeline(
            [
                *cascade_stages(stt, model, tts=tts, prompt=prompt),
                output,
                SpokenHistory(prompt.history),
            ]
        ),
        params=PipelineParams(audio_in_sample_rate=16000, audio_out_sample_rate=16000),
        observers=[observer],
        enable_rtvi=False,
        cancel_on_idle_timeout=False,
    )
    runner = WorkerRunner(handle_sigint=False, handle_sigterm=False)
    ready = asyncio.Event()
    results = []

    @worker.event_handler("on_pipeline_started")
    async def on_pipeline_started(worker, frame):
        ready.set()

    async def replay():
        try:
            await asyncio.wait_for(ready.wait(), 10)
        except TimeoutError:
            if run.done():
                await run
            raise
        # Warm both STT and the conversation cache once, outside reported samples.
        for index in [0, *range(len(records))]:
            prompt.history.clear()
            observer.started.clear()
            observer.finished.clear()
            observer.failure = None
            if isinstance(stt, StubTranscription):
                stt.transcript = records[index]["transcript"]
            await worker.queue_frame(UserStoppedSpeakingFrame())
            await asyncio.wait_for(observer.started.wait(), 10)
            transcribed = False
            async for frame in stt.run_stt(audio[index]):
                if isinstance(frame, TranscriptionFrame) and frame.text.strip():
                    transcribed = True
                await worker.queue_frame(frame)
            if not transcribed:
                raise RuntimeError(f"No final transcript for {records[index]['id']}")
            await asyncio.wait_for(observer.finished.wait(), 60)
            result = observer.durations()
            results.append(result)
        await worker.queue_frame(EndFrame())

    await runner.add_workers(worker)
    run = asyncio.create_task(runner.run())
    try:
        await replay()
        await run
    finally:
        if not run.done():
            await runner.cancel()
            await run
        await model.close()
    return results[1:]


def percentile(values, fraction):
    ordered = sorted(values)
    return ordered[max(0, math.ceil(len(ordered) * fraction) - 1)]


def report(results):
    mode = (
        "stub (synthetic audio; not a hardware benchmark)"
        if os.environ.get("AMES_STUB") == "1"
        else "local Parakeet, mlx-lm, Pocket TTS"
    )
    print(f"Latency: {len(results)} turns; {mode}; 1 warm-up excluded")
    print("stage                 p50_ms      p90_ms")
    for name in STAGES:
        values = [result[name] for result in results]
        print(f"{name:20s} {percentile(values, 0.5):10.3f} {percentile(values, 0.9):10.3f}")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, default=MANIFEST)
    args = parser.parse_args()
    log_errors()
    try:
        report(asyncio.run(measure(args.manifest)))
    except (OSError, ValueError, RuntimeError, TimeoutError, wave.Error, EOFError) as error:
        parser.exit(1, f"Latency harness: {error}\n")


if __name__ == "__main__":
    main()
