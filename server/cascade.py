"""Local recorded-turn cascade and reusable speech stages."""

import asyncio
import math
import os
import platform
from contextlib import aclosing
from pathlib import Path

from loguru import logger
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
    TTSStoppedFrame,
    TTSTextFrame,
)
from pipecat.processors.aggregators.llm_context import LLMContext
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor
from pipecat.services.llm_service import LLMService
from pipecat.services.settings import LLMSettings, STTSettings, TTSSettings
from pipecat.services.stt_service import STTService
from pipecat.services.tts_service import TTSService
from pipecat.utils.text.simple_text_aggregator import SimpleTextAggregator
from pipecat.utils.time import time_now_iso8601

from server.control_tokens import ParserState, parse_chunk
from server.llm import MODEL_DIR, MLXLanguageModel, StubLanguageModel, configure_models
from server.stt import ParakeetSTTService
from server.tts import MODEL_NAME, QwenTTSService


class StubTranscription(STTService):
    transcript = "hi there"

    def __init__(self):
        super().__init__(settings=STTSettings(model="stub", language="en"))

    async def run_stt(self, audio):
        yield TranscriptionFrame(self.transcript, "", time_now_iso8601())


class PlainPrompt(FrameProcessor):
    def __init__(self):
        super().__init__()
        # shortcut: this recorded prompt is uncapped; W uses PromptAssembler for live sessions.
        self.history = []

    async def process_frame(self, frame: Frame, direction: FrameDirection):
        await super().process_frame(frame, direction)
        await self.push_frame(frame, direction)
        if direction == FrameDirection.DOWNSTREAM and isinstance(frame, TranscriptionFrame):
            self.history.append({"role": "user", "content": frame.text})
            await self.push_frame(LLMContextFrame(LLMContext([dict(m) for m in self.history])))


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
        if isinstance(frame, (InterruptionFrame, CancelFrame)):
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


class ReplySanitizer(FrameProcessor):
    def __init__(self, tts):
        super().__init__()
        self.tts = tts
        self.state = ParserState()

    async def process_frame(self, frame: Frame, direction: FrameDirection):
        await super().process_frame(frame, direction)
        if direction != FrameDirection.DOWNSTREAM:
            await self.push_frame(frame, direction)
            return
        if isinstance(frame, (LLMFullResponseStartFrame, InterruptionFrame, CancelFrame)):
            self.state = ParserState()
            self.tts.mood = "neutral"
        if isinstance(frame, (LLMTextFrame, LLMFullResponseEndFrame)):
            result = parse_chunk(
                self.state,
                frame.text if isinstance(frame, LLMTextFrame) else "",
                final=isinstance(frame, LLMFullResponseEndFrame),
            )
            self.state = result.state
            self.tts.mood = result.state.mood
            if result.error_code:
                logger.error(result.error_code)
            if result.tts_text:
                await self.push_frame(LLMTextFrame(result.tts_text))
            if isinstance(frame, LLMTextFrame):
                return
        await self.push_frame(frame, direction)


class SpokenHistory(FrameProcessor):
    """Place after transport output so receipts follow sent audio, not synthesis."""

    def __init__(self, history):
        super().__init__()
        self.history = history

    async def process_frame(self, frame: Frame, direction: FrameDirection):
        await super().process_frame(frame, direction)
        if direction == FrameDirection.DOWNSTREAM and isinstance(frame, TTSTextFrame):
            if frame.append_to_context and frame.text.strip():
                if self.history and self.history[-1]["role"] == "assistant":
                    self.history[-1]["content"] += " " + frame.text.strip()
                else:
                    self.history.append({"role": "assistant", "content": frame.text.strip()})
        await self.push_frame(frame, direction)


def barge_in_stages(vad_analyzer):
    from pipecat.processors.audio.vad_processor import VADProcessor
    from pipecat.turns.user_start import VADUserTurnStartStrategy
    from pipecat.turns.user_turn_processor import UserTurnProcessor
    from pipecat.turns.user_turn_strategies import UserTurnStrategies

    # Pipecat rounds to the nearest VAD window; round up to require at least 300 ms.
    window = vad_analyzer.num_frames_required() / vad_analyzer.sample_rate
    start_secs = math.ceil(0.3 / window) * window
    vad_analyzer.set_params(vad_analyzer.params.model_copy(update={"start_secs": start_secs}))
    return [
        VADProcessor(vad_analyzer=vad_analyzer),
        UserTurnProcessor(
            user_turn_strategies=UserTurnStrategies(start=[VADUserTurnStartStrategy()])
        ),
    ]


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
            elif isinstance(frame, (InterruptionFrame, CancelFrame, LLMFullResponseStartFrame)):
                await self.aggregator.reset()
            await self.push_frame(frame, direction)


class ToneTTS(TTSService):
    def __init__(self):
        super().__init__(
            sample_rate=16000,
            push_start_frame=True,
            push_stop_frames=True,
            settings=TTSSettings(model="stub", voice=None, language=None),
        )

    async def run_tts(self, text, context_id):
        yield TTSAudioRawFrame(b"\x00\x20\x00\xe0" * 800, 16000, 1, context_id=context_id)
        yield TTSStoppedFrame()


def model_services():
    if os.environ.get("AMES_STUB") == "1":
        return StubTranscription(), StubLanguageModel()
    if platform.system() != "Darwin" or platform.machine() != "arm64":
        raise RuntimeError("Real models require an Apple Silicon Mac; use AMES_STUB=1 for CI")
    configure_models()
    parakeet_path = MODEL_DIR / "parakeet-tdt-0.6b-v2"
    llm_path = os.environ.get("AMES_LLM_MODEL")
    if not parakeet_path.is_dir() or not llm_path:
        raise RuntimeError(
            f"Install local Parakeet files in {parakeet_path} "
            "and set AMES_LLM_MODEL to a local mlx-lm model directory"
        )
    stt = ParakeetSTTService(parakeet_path)
    return stt, MLXLanguageModel(Path(llm_path).expanduser())


def tts_service():
    if os.environ.get("AMES_STUB") == "1":
        return ToneTTS()
    configure_models()
    path = MODEL_DIR / MODEL_NAME
    if not path.is_dir():
        raise RuntimeError(f"Install local Qwen files in {path}")
    return QwenTTSService(path)


def cascade_stages(stt, model, tts=None, prompt=None):
    tts = tts if tts is not None else tts_service()
    prompt = prompt if prompt is not None else PlainPrompt()
    input_stages = []
    if os.environ.get("AMES_STUB") != "1":
        from pipecat.audio.vad.silero import SileroVADAnalyzer

        input_stages = barge_in_stages(SileroVADAnalyzer(sample_rate=16000))
    return [
        *input_stages,
        stt,
        prompt,
        LocalLLMService(model),
        ReplySanitizer(tts),
        SentenceSplitter(),
        tts,
    ]
