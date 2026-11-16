import asyncio
import sys
import wave
from pathlib import Path
from unittest.mock import Mock

from pipecat.frames.frames import (
    InputAudioRawFrame,
    InterruptionFrame,
    LLMFullResponseEndFrame,
    TranscriptionFrame,
    TTSAudioRawFrame,
    TTSTextFrame,
)
from pipecat.pipeline.pipeline import Pipeline
from pipecat.tests.utils import SleepFrame, run_test

from server.cascade import StubTranscription, ToneTTS
from server.latency import DiscardAudioOutput
from server.live import Conversation, SessionOutput, SessionPrompt, live_stages
from server.llm import StubLanguageModel


async def test_stub_mic_frames_produce_one_turn_per_utterance(monkeypatch):
    monkeypatch.setenv("AMES_STUB", "1")
    monkeypatch.setitem(sys.modules, "pipecat.audio.vad.silero", None)
    events = Mock()
    conversation = Conversation(events)
    received, upstream = await run_test(
        Pipeline(
            live_stages(
                StubTranscription(),
                StubLanguageModel(),
                ToneTTS(),
                conversation,
                DiscardAudioOutput(),
            )
        ),
        frames_to_send=[
            *[InputAudioRawFrame(bytes(640), 16000, 1) for i in range(50)],
            *[InputAudioRawFrame(b"\x00\x20\x00\xe0" * 160, 16000, 1) for i in range(50)],
            *[InputAudioRawFrame(bytes(640), 16000, 1) for i in range(50)],
            SleepFrame(0.35),
        ],
    )
    assert conversation.turn_id == 1
    assert [message["role"] for message in conversation.history] == ["user", "ames"]
    assert conversation.history[0]["content"] == "hi there"
    finals = [call.args for call in events.transcript.call_args_list if call.args[2]]
    assert finals == [
        ("user", "hi there", True, 1),
        ("ames", conversation.history[1]["content"], True, 1),
    ]
    assert sum(isinstance(frame, LLMFullResponseEndFrame) for frame in received) == 1
    assert any(isinstance(frame, TTSAudioRawFrame) for frame in received)


async def test_wav_to_spoken_audio_and_next_prompt(monkeypatch):
    monkeypatch.setenv("AMES_STUB", "1")
    prompts = []

    class Model(StubLanguageModel):
        async def tokens(self, messages):
            prompts.append(messages)
            for text in ("<state crisis=0>Hello. ", "<sigh>How are you?"):
                yield text

    with wave.open(str(Path(__file__).parent / "fixtures/tone.wav"), "rb") as wav:
        audio = wav.readframes(wav.getnframes()) * 5
    events = Mock()
    conversation = Conversation(events)
    received, upstream = await run_test(
        Pipeline(
            live_stages(StubTranscription(), Model(), ToneTTS(), conversation, DiscardAudioOutput())
        ),
        frames_to_send=[
            *[InputAudioRawFrame(audio[i : i + 640], 16000, 1) for i in range(0, len(audio), 640)],
            *[InputAudioRawFrame(bytes(640), 16000, 1) for i in range(50)],
            SleepFrame(0.35),
            *[InputAudioRawFrame(audio[i : i + 640], 16000, 1) for i in range(0, len(audio), 640)],
            *[InputAudioRawFrame(bytes(640), 16000, 1) for i in range(50)],
            SleepFrame(0.35),
        ],
    )
    assert any(
        isinstance(frame, TTSAudioRawFrame) and frame.audio.strip(b"\x00") for frame in received
    )
    assert prompts[0][0]["role"] == "system"
    assert prompts[0][-1] == {"role": "user", "content": "hi there"}
    assert prompts[1][1:] == [
        {"role": "user", "content": "hi there"},
        {"role": "assistant", "content": "<state crisis=0>\nHello. How are you?"},
        {"role": "user", "content": "hi there"},
    ]
    assert conversation.history == [
        {"role": "user", "content": "hi there"},
        {"role": "ames", "content": "Hello. How are you?"},
        {"role": "user", "content": "hi there"},
        {"role": "ames", "content": "Hello. How are you?"},
    ]
    finals = [call.args for call in events.transcript.call_args_list if call.args[2]]
    assert finals == [
        ("user", "hi there", True, 1),
        ("ames", "Hello. How are you?", True, 1),
        ("user", "hi there", True, 2),
        ("ames", "Hello. How are you?", True, 2),
    ]
    events.speaking.assert_any_call("ames")


async def test_speaking_indicator_survives_barge_in_ordering():
    events = Mock()
    conversation = Conversation(events)
    conversation.bot_speaking = True
    conversation.show_speaking()
    conversation.user_speaking = True  # user interrupts while Ames is audible
    conversation.show_speaking()
    conversation.bot_speaking = False  # the bot stop arrives late
    conversation.show_speaking()
    conversation.show_speaking()
    conversation.user_speaking = False
    conversation.show_speaking()
    assert [call.args for call in events.speaking.call_args_list] == [
        ("ames",),
        ("user",),
        ("none",),
    ]


async def test_crisis_reply_never_reaches_tts_or_history(monkeypatch):
    monkeypatch.setenv("AMES_STUB", "1")

    class CrisisModel(StubLanguageModel):
        async def tokens(self, messages):
            yield "<state cri"
            yield "sis=1>Do not speak this."
            yield " Or this."

    conversation = Conversation(Mock())
    received, upstream = await run_test(
        Pipeline(
            live_stages(
                StubTranscription(), CrisisModel(), ToneTTS(), conversation, DiscardAudioOutput()
            )
        ),
        frames_to_send=[TranscriptionFrame("hi there", "", "now")],
    )
    spoken = [frame.text for frame in received if isinstance(frame, TTSTextFrame)]
    assert " ".join(spoken) == (
        "I can't help with this one. Please call your local emergency number "
        "or reach someone you trust right now."
    )
    assert conversation.history[0] == {"role": "user", "content": "hi there"}


async def test_interruption_finalizes_only_completed_sentence_once():
    events = Mock()
    conversation = Conversation(events)
    conversation.turn_id = 1
    conversation.final = False
    conversation.history.append({"role": "user", "content": "hello"})
    await run_test(
        SessionOutput(conversation),
        frames_to_send=[
            TTSTextFrame("Finished sentence.", "sentence"),
            SleepFrame(0.05),
            InterruptionFrame(),
            SleepFrame(0.05),
            LLMFullResponseEndFrame(),
        ],
    )
    assert conversation.history[-1] == {"role": "ames", "content": "Finished sentence."}
    assert [call.args for call in events.transcript.call_args_list] == [
        ("ames", "Finished sentence.", False, 1),
        ("ames", "Finished sentence.", True, 1),
    ]


async def test_sessions_do_not_share_history_and_blank_transcripts_do_not_start_turns():
    first = Conversation(Mock())
    first.history.append({"role": "user", "content": "private"})
    second = Conversation(Mock())
    await run_test(
        SessionPrompt(second, StubLanguageModel()),
        frames_to_send=[
            TranscriptionFrame(" ", "", "now"),
        ],
    )
    assert second.history == []
    assert second.turn_id == 0


async def test_pipeline_error_ends_live_session_without_hanging(monkeypatch):
    from pipecat.frames.frames import StartFrame
    from pipecat.processors.frame_processor import FrameProcessor

    from server import live

    class BrokenSTT(StubTranscription):
        async def process_frame(self, frame, direction):
            await super().process_frame(frame, direction)
            if isinstance(frame, StartFrame):
                await self.push_error("local_transcription_failed")

    class Input(FrameProcessor):
        async def process_frame(self, frame, direction):
            await super().process_frame(frame, direction)
            await self.push_frame(frame, direction)

    transport = Mock()
    transport.input.return_value = Input()
    transport.output.return_value = DiscardAudioOutput()
    monkeypatch.setenv("AMES_STUB", "1")
    monkeypatch.setattr(live, "SmallWebRTCTransport", Mock(return_value=transport))
    events = Mock()
    await asyncio.wait_for(
        live.build_live(
            Mock(),
            BrokenSTT(),
            StubLanguageModel(),
            ToneTTS(),
            Conversation(events),
        )(),
        5,
    )
    events.notice.assert_called_once_with(
        "session_ended",
        "The voice pipeline stopped. Start a new session.",
    )


async def test_transcripts_wait_for_the_end_of_the_user_turn():
    from pipecat.frames.frames import (
        LLMContextFrame,
        UserStartedSpeakingFrame,
        UserStoppedSpeakingFrame,
    )

    class Model:
        async def assemble(self, history, text):
            return [*history, {"role": "user", "content": text}]

    events = Mock()
    conversation = Conversation(events)
    received, upstream = await run_test(
        SessionPrompt(conversation, Model()),
        frames_to_send=[
            UserStartedSpeakingFrame(),
            SleepFrame(0.05),
            TranscriptionFrame("I was thinking", "", "now"),
            TranscriptionFrame("about cats", "", "now"),
            SleepFrame(0.05),
            UserStoppedSpeakingFrame(),
            SleepFrame(0.05),
            # Smart Turn can decide before the STT finishes; the late transcript still starts.
            UserStartedSpeakingFrame(),
            UserStoppedSpeakingFrame(),
            SleepFrame(0.05),
            TranscriptionFrame("and dogs", "", "now"),
        ],
    )
    contexts = [frame for frame in received if isinstance(frame, LLMContextFrame)]
    assert [c.context.get_messages()[-1]["content"] for c in contexts] == [
        "I was thinking about cats",
        "and dogs",
    ]
    assert conversation.turn_id == 2
    user = [call.args for call in events.transcript.call_args_list if call.args[0] == "user"]
    assert user == [
        ("user", "I was thinking", False, 1),
        ("user", "I was thinking about cats", False, 1),
        ("user", "I was thinking about cats", True, 1),
        ("user", "and dogs", True, 2),
    ]
