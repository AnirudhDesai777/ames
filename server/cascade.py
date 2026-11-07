"""S2a recorded-turn cascade: local STT, a plain prompt, and tone-only TTS."""

import asyncio
import os
import platform
from contextlib import aclosing
from pathlib import Path

from pipecat.frames.frames import (
    AggregatedTextFrame,
    CancelFrame,
    Frame,
    InterruptionFrame,
    LLMContextFrame,
    LLMFullResponseEndFrame,
    LLMFullResponseStartFrame,
    LLMTextFrame,
    TranscriptionFrame,
    TTSAudioRawFrame,
    UserStartedSpeakingFrame,
)
from pipecat.processors.aggregators.llm_context import LLMContext
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor
from pipecat.services.llm_service import LLMService
from pipecat.services.settings import LLMSettings, STTSettings, TTSSettings
from pipecat.services.stt_service import STTService
from pipecat.services.tts_service import TTSService
from pipecat.utils.text.simple_text_aggregator import SimpleTextAggregator
from pipecat.utils.time import time_now_iso8601

from server.llm import MODEL_DIR, MLXLanguageModel, StubLanguageModel, configure_models


class StubTranscription(STTService):
    transcript = "hi there"

    def __init__(self):
        super().__init__(settings=STTSettings(model="stub", language="en"))

    async def run_stt(self, audio):
        yield TranscriptionFrame(self.transcript, "", time_now_iso8601())


class PlainPrompt(FrameProcessor):
    async def process_frame(self, frame: Frame, direction: FrameDirection):
        await super().process_frame(frame, direction)
        await self.push_frame(frame, direction)
        if direction == FrameDirection.DOWNSTREAM and isinstance(frame, TranscriptionFrame):
            await self.push_frame(
                LLMContextFrame(LLMContext([{"role": "user", "content": frame.text}]))
            )


class LocalLLMService(LLMService):
    def __init__(self, model):
        super().__init__(
            settings=LLMSettings(
                model="local",
                system_instruction=None,
                temperature=None,
                max_tokens=256,
                top_p=None,
                top_k=None,
                frequency_penalty=None,
                presence_penalty=None,
                seed=None,
                filter_incomplete_user_turns=False,
                user_turn_completion_config=None,
            )
        )
        self.model = model
        self.generation = None

    async def process_frame(self, frame: Frame, direction: FrameDirection):
        await super().process_frame(frame, direction)
        if isinstance(frame, (InterruptionFrame, UserStartedSpeakingFrame, CancelFrame)):
            # System frames run while a context is streaming on the data-frame task.
            if self.generation:
                self.generation.cancel()
        if direction == FrameDirection.DOWNSTREAM and isinstance(frame, LLMContextFrame):
            self.generation = asyncio.create_task(self.respond(frame.context.get_messages()))
            try:
                await self.generation
            except asyncio.CancelledError:
                if asyncio.current_task().cancelling():
                    raise
            finally:
                self.generation = None
        else:
            await self.push_frame(frame, direction)

    async def respond(self, messages):
        await self.push_frame(LLMFullResponseStartFrame())
        try:
            async with aclosing(self.model.tokens(messages)) as tokens:
                while True:
                    try:
                        async with asyncio.timeout(10):
                            text = await anext(tokens)
                    except StopAsyncIteration:
                        break
                    if text:
                        await self.push_frame(LLMTextFrame(text))
        except asyncio.CancelledError:
            raise
        except Exception:
            await self.push_error("local_generation_failed")
        else:
            await self.push_frame(LLMFullResponseEndFrame())


class SentenceSplitter(FrameProcessor):
    def __init__(self):
        super().__init__()
        self.aggregator = SimpleTextAggregator()

    async def process_frame(self, frame: Frame, direction: FrameDirection):
        await super().process_frame(frame, direction)
        if direction == FrameDirection.DOWNSTREAM and isinstance(frame, LLMTextFrame):
            async for sentence in self.aggregator.aggregate(frame.text):
                await self.push_frame(AggregatedTextFrame(sentence.text, sentence.type))
        else:
            if isinstance(frame, LLMFullResponseEndFrame):
                sentence = await self.aggregator.flush()
                if sentence and sentence.text.strip():
                    await self.push_frame(AggregatedTextFrame(sentence.text, sentence.type))
            elif isinstance(frame, (InterruptionFrame, LLMFullResponseStartFrame)):
                await self.aggregator.reset()
            await self.push_frame(frame, direction)


class ToneTTS(TTSService):
    def __init__(self):
        super().__init__(
            sample_rate=16000, settings=TTSSettings(model="stub", voice=None, language=None)
        )

    async def run_tts(self, text, context_id):
        # shortcut: a tone stands in for speech until the chosen TTS lands in S3c.
        yield TTSAudioRawFrame(b"\x00\x20\x00\xe0" * 800, 16000, 1, context_id=context_id)


def model_services():
    if os.environ.get("AMES_STUB") == "1":
        return StubTranscription(), StubLanguageModel()
    if platform.system() != "Darwin" or platform.machine() != "arm64":
        raise RuntimeError("Real models require an Apple Silicon Mac; use AMES_STUB=1 for CI")
    configure_models()
    whisper_path = MODEL_DIR / "whisper-large-v3-turbo"
    llm_path = os.environ.get("AMES_LLM_MODEL")
    if not whisper_path.is_dir() or not llm_path:
        raise RuntimeError(
            "Install local Whisper large-v3-turbo files in the models directory "
            "and set AMES_LLM_MODEL to a local mlx-lm model directory"
        )
    from pipecat.services.whisper.stt import WhisperSTTServiceMLX

    stt = WhisperSTTServiceMLX(
        settings=WhisperSTTServiceMLX.Settings(model=str(whisper_path)),
        sample_rate=16000,
    )
    return stt, MLXLanguageModel(Path(llm_path).expanduser())


def cascade_stages(stt, model):
    return [stt, PlainPrompt(), LocalLLMService(model), SentenceSplitter(), ToneTTS()]
