"""Streaming local Qwen speech with a startup speed gate."""

import asyncio
import math
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Event
from time import perf_counter

import numpy as np
from pipecat.frames.frames import (
    CancelFrame,
    ErrorFrame,
    InterruptionFrame,
    TTSAudioRawFrame,
    TTSStoppedFrame,
)
from pipecat.services.settings import TTSSettings
from pipecat.services.tts_service import TTSService

from server.llm import configure_models
from server.mlx_runtime import MLX_LOCK

MODEL_NAME = "Qwen3-TTS-12Hz-1.7B-CustomVoice-8bit"
MINIMUM = (
    "Apple Silicon with 24 GB RAM and a TTS startup real-time factor at most 0.9 "
    "(reference: M4 Pro, 24 GB). See server/README.md."
)
MOOD_INSTRUCTIONS = {
    "neutral": None,
    "amused": "Speak with a teasing, sarcastic tone.",
    "excited": "Speak excitedly, with bright energy.",
    "soft": "Speak softly and gently.",
    "annoyed": "Speak in an irritated tone.",
    "concerned": "Speak with gentle concern.",
}
SELF_TEST_TEXT = "Hey, I'm Ames. Tell me what's on your mind today."


class QwenTTSService(TTSService):
    def __init__(self, model_path: Path):
        if not model_path.is_dir():
            raise ValueError("Qwen requires an existing local model directory")
        super().__init__(
            sample_rate=24000,
            push_start_frame=True,
            push_stop_frames=True,
            settings=TTSSettings(model=str(model_path), voice="Ryan", language="en"),
        )
        self.model_path = model_path
        self.model = None
        self.mood = "neutral"
        self.cancelled = None
        self.ready = False
        self.executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="ames-tts")

    def new_session(self):
        service = QwenTTSService(self.model_path)
        service.model = self.model
        service.mx = self.mx
        service.ready = self.ready
        return service

    def load_model(self):
        with MLX_LOCK:
            if self.model is not None:
                return
            configure_models()
            import mlx.core as mx
            from mlx_audio.tts.utils import load_model

            self.mx = mx
            self.model = load_model(str(self.model_path), model_type="qwen3_tts")
            mx.eval(self.model.parameters())
            mx.synchronize()

    def chunks(self, text, mood, cancelled):
        options = dict(voice="Ryan", lang_code="english", stream=True, streaming_interval=0.1)
        instruction = MOOD_INSTRUCTIONS.get(mood)
        if instruction:
            options["instruct"] = instruction
        with MLX_LOCK:
            stream = self.model.generate(text=text, **options)
        try:
            while not cancelled.is_set():
                with MLX_LOCK:
                    if cancelled.is_set():
                        return
                    result = next(stream, None)
                    if result is None:
                        return
                    self.mx.eval(result.audio)
                    values = np.asarray(result.audio).squeeze()
                    if values.ndim != 1 or not np.isfinite(values).all():
                        raise ValueError("Expected finite mono TTS audio")
                    if result.sample_rate != 24000:
                        raise ValueError("Expected 24 kHz Qwen audio")
                    pcm = (np.clip(values, -1, 1) * 32767).astype("<i2").tobytes()
                    self.mx.synchronize()
                if pcm and not cancelled.is_set():
                    yield pcm, result.sample_rate
        finally:
            with MLX_LOCK:
                stream.close()
                self.mx.synchronize()

    def self_test(self):
        self.load_model()
        # Warm kernels before measuring steady-state synthesis, as in the bake-off.
        for chunk in self.chunks(SELF_TEST_TEXT, "neutral", Event()):
            pass
        started = perf_counter()
        duration = sum(
            len(pcm) / (2 * rate) for pcm, rate in self.chunks(SELF_TEST_TEXT, "neutral", Event())
        )
        elapsed = perf_counter() - started
        factor = elapsed / duration if duration else math.inf
        if not math.isfinite(factor) or factor > 0.9:
            raise RuntimeError(f"This Mac is too slow for her voice. Minimum: {MINIMUM}")
        return factor

    async def prepare(self):
        if not self.ready:
            await asyncio.get_running_loop().run_in_executor(self.executor, self.self_test)
            self.ready = True

    async def start(self, frame):
        await self.prepare()
        await super().start(frame)

    async def process_frame(self, frame, direction):
        if isinstance(frame, (InterruptionFrame, CancelFrame)) and self.cancelled:
            self.cancelled.set()
        await super().process_frame(frame, direction)

    async def run_tts(self, text, context_id):
        loop = asyncio.get_running_loop()
        queue = asyncio.Queue()
        cancelled = Event()
        self.cancelled = cancelled
        mood = self.mood

        def emit(value):
            if not cancelled.is_set():
                loop.call_soon_threadsafe(queue.put_nowait, value)

        def synthesize():
            try:
                for pcm, rate in self.chunks(text, mood, cancelled):
                    emit((pcm, rate))
            except Exception:
                emit(ErrorFrame("local_synthesis_failed"))
            finally:
                emit(None)

        loop.run_in_executor(self.executor, synthesize)
        received_audio = False
        try:
            while (value := await queue.get()) is not None:
                if isinstance(value, ErrorFrame):
                    raise RuntimeError(value.error) from None
                pcm, rate = value
                received_audio = True
                yield TTSAudioRawFrame(pcm, rate, 1, context_id=context_id)
            if not received_audio:
                raise RuntimeError("local_synthesis_failed")
            # Flush the transport's partial audio chunk before its sentence receipt.
            yield TTSStoppedFrame()
        finally:
            cancelled.set()
            if self.cancelled is cancelled:
                self.cancelled = None

    async def cleanup(self):
        if self.cancelled:
            self.cancelled.set()
        try:
            await super().cleanup()
        finally:
            await asyncio.to_thread(self.executor.shutdown, wait=True)
