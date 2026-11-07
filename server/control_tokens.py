"""Pure streaming reply parsing, before sentence splitting or transcript delivery."""

import re
from dataclasses import dataclass, replace

MOODS = ("neutral", "amused", "excited", "soft", "annoyed", "concerned")
CONTROL_TOKEN = re.compile(r"<state mood=(" + "|".join(MOODS) + r") crisis=([01])>")
ALLOWED_TAGS = frozenset(("<laugh>", "<chuckle>", "<sigh>", "<gasp>", "<groan>", "<yawn>"))


@dataclass(frozen=True)
class ParserState:
    pending: str = ""
    control_done: bool = False
    mood: str = "neutral"
    crisis: int = 0


@dataclass(frozen=True)
class ParsedChunk:
    state: ParserState
    tts_text: str = ""
    transcript_text: str = ""
    error_code: str | None = None


def parse_chunk(state: ParserState, text: str, *, final: bool = False) -> ParsedChunk:
    """Start each reply with ParserState(); flush once with final=True at its end.

    The caller logs only error_code when present, never text or parser state.
    """
    text = state.pending + text
    state = replace(state, pending="")
    error_code = None
    if not state.control_done:
        text = text.lstrip()
        if not final and (not text or (text.startswith("<") and ">" not in text)):
            return ParsedChunk(replace(state, pending=text))

        state = replace(state, control_done=True)
        token_end = text.find(">") + 1 if text.startswith("<") else 0
        token = text[:token_end]
        match = CONTROL_TOKEN.fullmatch(token)
        if match:
            state = replace(state, mood=match[1], crisis=int(match[2]))
            text = text[token_end:]
        elif text.startswith("<state"):
            error_code = "control_token_malformed"
            text = text[token_end:] if token_end else ""
        else:
            error_code = "control_token_missing"

    tts = []
    transcript = []
    index = 0
    while index < len(text):
        char = text[index]
        if char in "<*":
            closing = ">" if char == "<" else "*"
            end = text.find(closing, index + 1)
            if end == -1:
                if not final:
                    state = replace(state, pending=text[index:])
                break
            tag = text[index : end + 1]
            if tag in ALLOWED_TAGS:
                tts.append(tag)
            index = end + 1
        else:
            tts.append(char)
            transcript.append(char)
            index += 1

    return ParsedChunk(state, "".join(tts), "".join(transcript), error_code)
