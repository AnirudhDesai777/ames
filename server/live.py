"""Live cascade with session-only history and transport playback receipts."""

import os

from pipecat.frames.frames import (
    BotStartedSpeakingFrame,
    BotStoppedSpeakingFrame,
    CancelFrame,
    InterruptionFrame,
    LLMContextFrame,
    LLMFullResponseEndFrame,
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

from server.cascade import cascade_stages


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
    def __init__(self, conversation, model):
        super().__init__()
        self.conversation = conversation
        self.model = model

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
            elif isinstance(frame, TranscriptionFrame) and frame.text.strip():
                conversation.finish_reply()
                conversation.turn_id += 1
                conversation.reply = ""
                conversation.heard = frame.text
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
                messages = await self.model.assemble(history, frame.text)
                conversation.history.append({"role": "user", "content": frame.text})
                conversation.events.transcript("user", frame.text, True, conversation.turn_id)
                await self.push_frame(LLMContextFrame(LLMContext(messages)))
                return
        await self.push_frame(frame, direction)


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
                        conversation.history[-1]["content"] += " " + text
                        conversation.reply += " " + text
                    else:
                        conversation.history.append({"role": "ames", "content": text})
                        conversation.reply = text
                    conversation.events.transcript(
                        "ames", conversation.reply, False, conversation.turn_id
                    )
            elif isinstance(frame, LLMFullResponseEndFrame):
                conversation.finish_reply(interrupted=False)
            elif isinstance(frame, (InterruptionFrame, CancelFrame)):
                conversation.finish_reply()
            elif isinstance(frame, BotStartedSpeakingFrame):
                conversation.bot_speaking = True
                conversation.show_speaking()
            elif isinstance(frame, BotStoppedSpeakingFrame):
                conversation.bot_speaking = False
                conversation.show_speaking()
        await self.push_frame(frame, direction)


def live_stages(stt, model, tts, conversation, output):
    return [
        *cascade_stages(
            stt,
            model,
            tts=tts,
            prompt=SessionPrompt(conversation, model),
        ),
        output,
        SessionOutput(conversation),
    ]


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
        enable_tracing=os.environ.get("AMES_TRACE") == "1",
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

    return run
