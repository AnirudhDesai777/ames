"""Model-free audio stages for the S1 transport echo."""

import os

from pipecat.frames.frames import Frame, InputAudioRawFrame, OutputAudioRawFrame
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor


class StubSTT(FrameProcessor):
    # shortcut: preserve PCM for the S1 echo; replace with transcription in S2a.
    async def process_frame(self, frame: Frame, direction: FrameDirection):
        await super().process_frame(frame, direction)
        await self.push_frame(frame, direction)


class StubLLM(StubSTT):
    """Pass frames through without loading a language model."""


class StubTTS(FrameProcessor):
    async def process_frame(self, frame: Frame, direction: FrameDirection):
        await super().process_frame(frame, direction)
        if direction == FrameDirection.DOWNSTREAM and isinstance(frame, InputAudioRawFrame):
            frame = OutputAudioRawFrame(
                audio=frame.audio,
                sample_rate=frame.sample_rate,
                num_channels=frame.num_channels,
            )
        await self.push_frame(frame, direction)


def model_stages() -> list[FrameProcessor]:
    if os.environ.get("AMES_STUB") != "1":
        raise RuntimeError("S1 supports only the echo stubs. Set AMES_STUB=1.")
    return [StubSTT(), StubLLM(), StubTTS()]
