"""Local recorded-turn cascade and reusable speech stages."""

import asyncio
import math
import os
import platform
import sys
from contextlib import aclosing
from pathlib import Path

from loguru import logger
from pipecat.audio.vad.vad_analyzer import VADAnalyzer
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
from pipecat.processors.audio.vad_processor import VADProcessor
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor
from pipecat.services.llm_service import LLMService
from pipecat.services.settings import LLMSettings, STTSettings, TTSSettings
from pipecat.services.stt_service import SegmentedSTTService
from pipecat.services.tts_service import TTSService
from pipecat.utils.text.simple_text_aggregator import SimpleTextAggregator
from pipecat.utils.time import time_now_iso8601

from server.control_tokens import ParserState, parse_chunk
from server.llm import MODEL_DIR, MLXLanguageModel, StubLanguageModel, configure_models
from server.stt import ParakeetSTTService
from server.tts import MODEL_NAME, PocketTTSService


def log_errors(sink=sys.stderr):
    # Library debug logs can include SDP or data-channel messages, and loguru's
    # default tracebacks print variable values, which can hold user speech.
    logger.remove()
    logger.add(sink, level="ERROR", format="{message}", diagnose=False, backtrace=False)


class StubVAD(VADAnalyzer):
    def num_frames_required(self):
        return 320

    def voice_confidence(self, audio):
        # shortcut: stub speech uses only Pipecat's volume gate; real mode uses Silero.
        return 1.0


class StubTranscription(SegmentedSTTService):
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
            self.generation = asyncio.create_task(self.respond(frame.context))
            try:
                await self.generation
            except asyncio.CancelledError:
                if asyncio.current_task().cancelling():
                    raise
            finally:
                self.generation = None
        else:
            await self.push_frame(frame, direction)

    async def respond(self, context):
        await self.push_frame(LLMFullResponseStartFrame())
        try:
            async with aclosing(self.model.tokens(context.get_messages())) as tokens:
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
    def __init__(self):
        super().__init__()
        self.state = ParserState()

    async def process_frame(self, frame: Frame, direction: FrameDirection):
        await super().process_frame(frame, direction)
        if direction != FrameDirection.DOWNSTREAM:
            await self.push_frame(frame, direction)
            return
        if isinstance(frame, (LLMFullResponseStartFrame, InterruptionFrame, CancelFrame)):
            self.state = ParserState()
        if isinstance(frame, (LLMTextFrame, LLMFullResponseEndFrame)):
            result = parse_chunk(
                self.state,
                frame.text if isinstance(frame, LLMTextFrame) else "",
                final=isinstance(frame, LLMFullResponseEndFrame),
            )
            self.state = result.state
            if result.error_code:
                logger.error(result.error_code)
            # shortcut: crisis replies stay silent until S6 adds the full crisis exit.
            if result.tts_text and not result.state.crisis:
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
    from pipecat.turns.user_start import VADUserTurnStartStrategy
    from pipecat.turns.user_turn_processor import UserTurnProcessor
    from pipecat.turns.user_turn_strategies import UserTurnStrategies

    # An analyzer reports rate 0 until a pipeline starts it; every pipeline here takes 16 kHz.
    vad_analyzer.set_sample_rate(16000)
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
        raise RuntimeError(f"Install local Pocket TTS files in {path}")
    return PocketTTSService(path)


def cascade_stages(stt, model, tts=None, prompt=None):
    tts = tts if tts is not None else tts_service()
    prompt = prompt if prompt is not None else PlainPrompt()
    if os.environ.get("AMES_STUB") == "1":
        input_stages = [VADProcessor(vad_analyzer=StubVAD(sample_rate=16000))]
    else:
        from pipecat.audio.vad.silero import SileroVADAnalyzer

        input_stages = barge_in_stages(SileroVADAnalyzer(sample_rate=16000))
    return [
        *input_stages,
        stt,
        prompt,
        LocalLLMService(model),
        ReplySanitizer(),
        SentenceSplitter(),
        tts,
    ]
