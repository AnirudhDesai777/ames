"""Local, segmented Parakeet transcription using mlx-audio."""

import asyncio
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import numpy as np
from pipecat.frames.frames import ErrorFrame, TranscriptionFrame
from pipecat.services.settings import STTSettings
from pipecat.services.stt_service import SegmentedSTTService
from pipecat.transcriptions.language import Language
from pipecat.utils.time import time_now_iso8601

from server.llm import configure_models


class ParakeetSTTService(SegmentedSTTService):
    def __init__(self, model_path: Path):
        if not model_path.is_dir():
            raise ValueError("Parakeet requires an existing local model directory")
        super().__init__(
            sample_rate=16000,
            settings=STTSettings(model=str(model_path), language=Language.EN),
        )
        self.model_path = model_path
        self.model = None
        # shortcut: STT has its own worker until S3c (#25) unifies MLX execution.
        self.executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="ames-stt")

    @property
    def wants_wav_segments(self):
        return False

    def load_model(self):
        if self.model is not None:
            return
        configure_models()
        import mlx.core as mx
        from mlx_audio.stt.utils import load_model

        self.mx = mx
        self.model = load_model(str(self.model_path), model_type="parakeet")
        mx.eval(self.model.parameters())
        mx.synchronize()

    async def start(self, frame):
        await asyncio.get_running_loop().run_in_executor(self.executor, self.load_model)
        await super().start(frame)

    def transcribe(self, audio):
        signal = np.frombuffer(audio, dtype="<i2").astype(np.float32) / 32768
        result = self.model.generate(self.mx.array(signal))
        self.mx.synchronize()
        return result.text

    async def run_stt(self, audio):
        await self.start_processing_metrics()
        try:
            text = await asyncio.get_running_loop().run_in_executor(
                self.executor, self.transcribe, audio
            )
            if text.strip():
                yield TranscriptionFrame(text, self._user_id, time_now_iso8601(), Language.EN)
        except Exception:
            yield ErrorFrame("local_transcription_failed")
        finally:
            await self.stop_processing_metrics()

    async def cleanup(self):
        try:
            await super().cleanup()
        finally:
            await asyncio.to_thread(self.executor.shutdown, wait=True)
