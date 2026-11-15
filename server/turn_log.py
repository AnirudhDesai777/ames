"""Opt-in per-session turn log for reviewing conversations and collecting ratings."""

import hashlib
import json
import os
from datetime import UTC, datetime
from pathlib import Path

from loguru import logger
from pipecat.frames.frames import (
    AggregatedTextFrame,
    BotStartedSpeakingFrame,
    LLMTextFrame,
    TranscriptionFrame,
    TTSAudioRawFrame,
    VADUserStoppedSpeakingFrame,
)
from pipecat.observers.base_observer import BaseObserver
from pipecat.processors.frame_processor import FrameDirection

from server.latency import STAGES
from server.prompt import load_system_prompt

STAMPS = {
    LLMTextFrame: "llm_first_token",
    AggregatedTextFrame: "first_sentence",
    TTSAudioRawFrame: "tts_first_chunk",
}


class TurnTimer(BaseObserver):
    """Each turn starts at its transcript, so noise VAD stops during the reply cannot reset it.

    The output transport also writes silence, so audio counts as sent only when it reports
    that the bot started speaking.
    """

    def __init__(self, output):
        super().__init__()
        self.output = output
        self.last_stop = None
        self.timestamps = {}

    async def on_push_frame(self, data):
        frame, at = data.frame, data.timestamp
        if data.direction != FrameDirection.DOWNSTREAM:
            return
        if isinstance(frame, VADUserStoppedSpeakingFrame):
            self.last_stop = at
        elif isinstance(frame, TranscriptionFrame):
            if data.first_push and frame.text.strip():
                self.timestamps = {
                    "turn_end": at if self.last_stop is None else self.last_stop,
                    "stt_final": at,
                }
        elif self.timestamps:
            if isinstance(frame, BotStartedSpeakingFrame) and data.source is self.output:
                self.timestamps.setdefault("audio_sent", at)
            for kind, name in STAMPS.items():
                if isinstance(frame, kind):
                    self.timestamps.setdefault(name, at)
                    break


class TurnLog:
    def __init__(self, data_dir: Path, session_id: str, voice=lambda: None):
        directory = data_dir / "turns"
        directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        directory.chmod(0o700)
        started = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
        self.path = directory / f"{started}-{session_id}.jsonl"
        self.path.touch(mode=0o600)
        self.session_id = session_id
        self.voice = voice
        self.model = Path(os.environ.get("AMES_LLM_MODEL", "stub")).name
        self.prompt = hashlib.sha256(load_system_prompt().encode()).hexdigest()[:12]
        self.observer = None
        self.turns = set()
        self.ratings = {}

    def append(self, record: dict):
        # A failed log write must never drop the user's turn.
        try:
            with self.path.open("a", encoding="utf-8") as file:
                file.write(json.dumps(record, ensure_ascii=False) + "\n")
        except OSError as error:
            logger.error("turn_log_failed: {}", type(error).__name__)

    def watch(self, output):
        self.observer = TurnTimer(output)
        return self.observer

    def latency(self):
        stamps = self.observer.timestamps if self.observer else {}
        return {
            name: round((stamps[end] - stamps[start]) / 1_000_000)
            for name, (start, end) in STAGES.items()
            if start in stamps and end in stamps and stamps[end] >= stamps[start]
        }

    def turn(self, turn_id: int, user: str, ames: str, interrupted: bool):
        self.turns.add(turn_id)
        self.append(
            dict(
                type="turn",
                session=self.session_id,
                turn_id=turn_id,
                at=datetime.now(UTC).isoformat(timespec="seconds"),
                user=user,
                ames=ames,
                interrupted=interrupted,
                model=self.model,
                prompt=self.prompt,
                voice=self.voice(),
                latency_ms=self.latency(),
            )
        )

    def rate(self, turn_id, rating):
        """The client is untrusted: only ratings for logged turns are kept; the last one wins."""
        valid = type(turn_id) is int and turn_id in self.turns and rating in ("good", "bad")
        if valid and self.ratings.get(turn_id) != rating:
            self.ratings[turn_id] = rating
            self.append(
                dict(
                    type="rating",
                    session=self.session_id,
                    turn_id=turn_id,
                    at=datetime.now(UTC).isoformat(timespec="seconds"),
                    rating=rating,
                )
            )
