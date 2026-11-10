"""Live cascade with session-only history and transport playback receipts."""

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
    def __init__(self, events):
        self.events = events
        self.history = []
        self.turn_id = 0
        self.reply = ""
        self.mood = "neutral"
        self.final = True

    def finish_reply(self):
        if not self.final:
            self.events.transcript("ames", self.reply, True, self.turn_id)
            self.final = True

    def state(self, mood):
        self.mood = mood
        self.events.state(mood, self.turn_id)


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
                conversation.events.speaking("user")
            elif isinstance(frame, UserStoppedSpeakingFrame):
                conversation.events.speaking("none")
            elif isinstance(frame, TranscriptionFrame) and frame.text.strip():
                conversation.finish_reply()
                conversation.turn_id += 1
                conversation.reply = ""
                conversation.final = False
                # Her turns keep their control token in the prompt, or the model
                # copies the bare turns and stops emitting it.
                history = [
                    {
                        "role": "assistant",
                        "content": f"<state mood={message['mood']} crisis=0>\n{message['content']}",
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
                        conversation.history.append(
                            {"role": "ames", "content": text, "mood": conversation.mood}
                        )
                        conversation.reply = text
                    conversation.events.transcript(
                        "ames", conversation.reply, False, conversation.turn_id
                    )
            elif isinstance(frame, (LLMFullResponseEndFrame, InterruptionFrame, CancelFrame)):
                conversation.finish_reply()
            elif isinstance(frame, BotStartedSpeakingFrame):
                conversation.events.speaking("ames")
            elif isinstance(frame, BotStoppedSpeakingFrame):
                conversation.events.speaking("none")
        await self.push_frame(frame, direction)


def live_stages(stt, model, tts, conversation, output):
    return [
        *cascade_stages(
            stt,
            model,
            tts=tts,
            prompt=SessionPrompt(conversation, model),
            on_state=conversation.state,
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
    worker = PipelineWorker(
        Pipeline(
            [transport.input(), *live_stages(stt, model, tts, conversation, transport.output())]
        ),
        params=PipelineParams(audio_in_sample_rate=16000, audio_out_sample_rate=24000),
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

    return run
