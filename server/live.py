"""Live cascade with session-only history and transport playback receipts."""

import asyncio
from contextlib import aclosing

from loguru import logger
from pipecat.frames.frames import (
    BotStartedSpeakingFrame,
    BotStoppedSpeakingFrame,
    CancelFrame,
    InterruptionFrame,
    LLMContextFrame,
    LLMFullResponseEndFrame,
    LLMFullResponseStartFrame,
    LLMTextFrame,
    TranscriptionFrame,
    TTSTextFrame,
    UserStartedSpeakingFrame,
    UserStoppedSpeakingFrame,
)
from pipecat.pipeline.pipeline import Pipeline
from pipecat.pipeline.worker import PipelineParams, PipelineWorker
from pipecat.processors.aggregators.llm_context import LLMContext
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor
from pipecat.transports.base_transport import TransportParams
from pipecat.transports.smallwebrtc.transport import SmallWebRTCTransport
from pipecat.workers.runner import WorkerRunner

from server.cascade import LocalLLMService, ReplySanitizer, cascade_stages
from server.control_tokens import ParserState, parse_chunk
from server.tool_calls import FILLER, parse_reply, result_message, speech_route
from server.tools import SearchDisabled, execute, search_enabled

CRISIS_LINE = (
    "I can't help with this one. Please call your local emergency number "
    "or reach someone you trust right now."
)
FALLBACK = "I couldn't complete that request. Please try again."


class TypedTextFrame(TranscriptionFrame):
    """Typed input; SessionPrompt treats it as a finished user turn, not as STT text."""


class Conversation:
    def __init__(self, events, log=None):
        self.events = events
        self.log = log
        self.history = []
        self.heard = ""
        self.turn_id = 0
        self.reply = ""
        self.final = True
        self.user_speaking = False
        self.bot_speaking = False
        self.shown = None
        self.tool_turn = False
        self.interim_ends = set()
        self.crisis = False

    def finish_reply(self, interrupted=True):
        if not self.final:
            self.events.transcript(
                "ames", self.reply, True, self.turn_id, rateable=self.log is not None
            )
            self.final = True
            if self.log:
                self.log.turn(self.turn_id, self.heard, self.reply, interrupted)

    def show_speaking(self):
        """One writer for the indicator: barge-in makes the stop events arrive out of order."""
        who = "user" if self.user_speaking else "ames" if self.bot_speaking else "none"
        if who != self.shown:
            self.shown = who
            self.events.speaking(who)


class SessionPrompt(FrameProcessor):
    """Starts a reply when the user's turn ends.

    The STT transcribes at every short VAD pause, so transcripts are held while
    Smart Turn says the user is still speaking. Without a turn processor (stub mode)
    no user-speaking frames arrive and each transcript starts a reply at once.
    """

    def __init__(self, conversation, model):
        super().__init__()
        self.conversation = conversation
        self.model = model
        self.pending = []

    async def process_frame(self, frame, direction):
        await super().process_frame(frame, direction)
        if direction == FrameDirection.DOWNSTREAM:
            conversation = self.conversation
            if isinstance(frame, InterruptionFrame):
                conversation.finish_reply()
            elif isinstance(frame, UserStartedSpeakingFrame):
                conversation.user_speaking = True
                conversation.show_speaking()
            elif isinstance(frame, UserStoppedSpeakingFrame):
                conversation.user_speaking = False
                conversation.show_speaking()
                await self.push_frame(frame, direction)
                await self.start_turn()
                return
            elif isinstance(frame, TypedTextFrame):
                # Half-spoken STT text is dropped: the typed message replaces it, and
                # user_speaking is cleared so the turn cannot wait for a stop that never comes.
                self.pending = [frame.text]
                conversation.user_speaking = False
                conversation.show_speaking()
                await self.start_turn()
                return
            elif isinstance(frame, TranscriptionFrame) and frame.text.strip():
                self.pending.append(frame.text.strip())
                if conversation.user_speaking:
                    text = " ".join(self.pending)
                    conversation.events.transcript("user", text, False, conversation.turn_id + 1)
                else:
                    await self.start_turn()
                return
        await self.push_frame(frame, direction)

    async def start_turn(self):
        if not self.pending:
            return
        text = " ".join(self.pending)
        self.pending = []
        conversation = self.conversation
        conversation.finish_reply()
        conversation.turn_id += 1
        conversation.reply = ""
        conversation.tool_turn = False
        conversation.crisis = False
        conversation.heard = text
        conversation.final = False
        # Her turns keep their control token in the prompt, or the model
        # copies the bare turns and stops emitting it.
        history = [
            {
                "role": "assistant",
                "content": f"<state crisis=0>\n{message['content']}",
            }
            if message["role"] == "ames"
            else message
            for message in conversation.history
        ]
        messages = await self.model.assemble(history, text)
        conversation.history.append({"role": "user", "content": text})
        conversation.events.transcript("user", text, True, conversation.turn_id)
        await self.push_frame(LLMContextFrame(LLMContext(messages)))


class ToolLoop(LocalLLMService):
    """Stream safe speech, then validate the completed call before any side effect."""

    def __init__(self, model, conversation, executor=execute):
        super().__init__(model)
        self.conversation = conversation
        self.executor = executor

    async def generate_reply(self, messages, *, continuation=False):
        raw = ""
        emitted = ""
        sanitizer = ParserState()
        sanitizer = parse_chunk(sanitizer, "<state crisis=0>").state
        async with aclosing(self.model.tokens(messages)) as tokens:
            while True:
                try:
                    async with asyncio.timeout(10):
                        chunk = await anext(tokens)
                except StopAsyncIteration:
                    break
                raw += chunk
                route = speech_route(raw)
                safe = route.text
                if len(safe) > len(emitted):
                    piece = safe[len(emitted) :]
                    emitted = safe
                    cleaned = parse_chunk(sanitizer, piece)
                    sanitizer = cleaned.state
                    if cleaned.tts_text and not continuation:
                        await self.push_frame(LLMTextFrame(cleaned.tts_text))
        parsed = parse_reply(raw)
        if parsed.error:
            logger.error(parsed.error)
        if parsed.crisis:
            return raw, parsed
        # Withhold any incomplete reserved marker at the end of generation.
        route = speech_route(raw, final=True)
        if len(route.text) > len(emitted):
            cleaned = parse_chunk(sanitizer, route.text[len(emitted) :])
            sanitizer = cleaned.state
            if cleaned.tts_text and not continuation:
                await self.push_frame(LLMTextFrame(cleaned.tts_text))
        cleaned = parse_chunk(sanitizer, "", final=True)
        if cleaned.tts_text and not continuation:
            await self.push_frame(LLMTextFrame(cleaned.tts_text))
        return raw, parsed

    async def speak(self, text):
        await self.push_frame(LLMTextFrame(text))

    async def respond(self, context):
        await self.push_frame(LLMFullResponseStartFrame())
        try:
            messages = context.get_messages()
            raw, parsed = await self.generate_reply(messages)
            if parsed.crisis:
                self.conversation.crisis = True
                await self.speak(CRISIS_LINE)
            elif parsed.call or (parsed.attempted and parsed.error):
                conversation = self.conversation
                if parsed.call and not parsed.text:
                    await self.speak(FILLER[parsed.call["name"]])
                # Flush the filler to TTS before the second generation.
                conversation.tool_turn = True
                interim = LLMFullResponseEndFrame()
                conversation.interim_ends.add(id(interim))
                await self.push_frame(interim)
                if conversation.history and conversation.history[-1]["role"] == "ames":
                    conversation.history.pop()
                receipt = None
                if parsed.call:
                    searching = parsed.call["name"] == "web_search" and search_enabled()
                    if searching:
                        conversation.events.search(True)
                    try:
                        data = await self.executor(parsed.call)
                        receipt = result_message(parsed.call["name"], data=data)
                    except SearchDisabled:
                        logger.error("search_disabled")
                        receipt = result_message(parsed.call["name"], error="search_disabled")
                    except Exception:
                        logger.error("tool_execution_failed")
                        receipt = result_message(parsed.call["name"], error="tool_unavailable")
                    finally:
                        if searching:
                            conversation.events.search(False)
                else:
                    receipt = result_message(None, error=parsed.error)
                call_message = {"role": "assistant", "content": raw}
                conversation.history.extend([call_message, receipt])
                await self.push_frame(LLMFullResponseStartFrame())
                following_raw, following = await self.generate_reply(
                    messages + [call_message, receipt], continuation=True
                )
                if following.crisis:
                    conversation.crisis = True
                    await self.speak(CRISIS_LINE)
                elif following.error or following.attempted or not following.text.strip():
                    logger.error("invalid_tool_continuation")
                    await self.speak(FALLBACK)
                    conversation.history.append(
                        {"role": "assistant", "content": "<state crisis=0>" + FALLBACK}
                    )
                else:
                    cleaned = parse_chunk(
                        ParserState(), "<state crisis=0>" + following.text, final=True
                    )
                    await self.speak(cleaned.tts_text)
                    conversation.history.append({"role": "assistant", "content": following_raw})
            elif parsed.error or not parsed.text.strip():
                await self.speak(FALLBACK)
        except asyncio.CancelledError:
            raise
        except Exception:
            await self.push_error("local_generation_failed")
        else:
            await self.push_frame(LLMFullResponseEndFrame())


class SessionOutput(FrameProcessor):
    """Receipts reach this stage only after the transport sends sentence audio."""

    def __init__(self, conversation):
        super().__init__()
        self.conversation = conversation

    async def process_frame(self, frame, direction):
        await super().process_frame(frame, direction)
        if direction == FrameDirection.DOWNSTREAM:
            conversation = self.conversation
            if isinstance(frame, TTSTextFrame) and frame.append_to_context and frame.text.strip():
                if not conversation.final:
                    text = frame.text.strip()
                    if conversation.reply:
                        if not conversation.tool_turn:
                            conversation.history[-1]["content"] += " " + text
                        conversation.reply += " " + text
                    else:
                        if not conversation.tool_turn:
                            conversation.history.append({"role": "ames", "content": text})
                        conversation.reply = text
                    conversation.events.transcript(
                        "ames", conversation.reply, False, conversation.turn_id
                    )
            elif isinstance(frame, LLMFullResponseEndFrame):
                if id(frame) in conversation.interim_ends:
                    conversation.interim_ends.remove(id(frame))
                else:
                    conversation.finish_reply(interrupted=False)
            elif isinstance(frame, (InterruptionFrame, CancelFrame)):
                conversation.finish_reply()
            elif isinstance(frame, BotStartedSpeakingFrame):
                conversation.bot_speaking = True
                conversation.show_speaking()
            elif isinstance(frame, BotStoppedSpeakingFrame):
                conversation.bot_speaking = False
                conversation.show_speaking()
                if conversation.crisis and conversation.reply == CRISIS_LINE:
                    conversation.crisis = False
                    conversation.events.notice("session_ended", CRISIS_LINE)
                    connection = getattr(conversation.events, "connection", None)
                    if connection and asyncio.iscoroutinefunction(connection.disconnect):
                        asyncio.create_task(connection.disconnect())
        await self.push_frame(frame, direction)


def live_stages(stt, model, tts, conversation, output):
    stages = cascade_stages(stt, model, tts=tts, prompt=SessionPrompt(conversation, model))
    stages[stages.index(next(stage for stage in stages if isinstance(stage, LocalLLMService)))] = (
        ToolLoop(model, conversation)
    )
    stages.remove(next(stage for stage in stages if isinstance(stage, ReplySanitizer)))
    return [*stages, output, SessionOutput(conversation)]


def build_live(connection, stt, model, tts, conversation):
    """Build the pipeline and return its run coroutine function.

    Building is separate so a setup failure can reject the offer instead of
    ending a session the client believes has started.
    """
    transport = SmallWebRTCTransport(
        connection, TransportParams(audio_in_enabled=True, audio_out_enabled=True)
    )
    output = transport.output()
    worker = PipelineWorker(
        Pipeline([transport.input(), *live_stages(stt, model, tts, conversation, output)]),
        params=PipelineParams(audio_in_sample_rate=16000, audio_out_sample_rate=24000),
        observers=[conversation.log.watch(output)] if conversation.log else [],
        enable_rtvi=False,
    )
    runner = WorkerRunner(handle_sigint=False, handle_sigterm=False)

    @transport.event_handler("on_client_disconnected")
    async def on_client_disconnected(transport, client):
        await runner.cancel()

    @worker.event_handler("on_pipeline_error")
    async def on_pipeline_error(worker, frame):
        conversation.events.notice(
            "session_ended", "The voice pipeline stopped. Start a new session."
        )
        await worker.cancel()

    async def run():
        await runner.add_workers(worker)
        await runner.run()

    async def submit(text):
        # Same path as barge-in: the interruption cuts any reply, then the text starts a turn.
        await worker.queue_frames(
            [InterruptionFrame(), TypedTextFrame(text=text, user_id="", timestamp="")]
        )

    run.submit = submit
    return run
