"""Pure streaming reply parsing, before sentence splitting or transcript delivery."""

import re
import unicodedata
from dataclasses import dataclass, replace
from string import punctuation

CONTROL_TOKEN = re.compile(r"<state crisis=([01])>")
ALLOWED_TAGS = frozenset()
TAG_SHAPE = re.compile(r"<(?:[A-Za-z/][A-Za-z0-9_=-]*|state[A-Za-z0-9_= -]*)")
MARKUP = ALLOWED_TAGS | {f"<state crisis={crisis}>" for crisis in (0, 1)}


@dataclass(frozen=True)
class ParserState:
    pending: str = ""
    control_done: bool = False
    crisis: int = 0
    previous_char: str = ""


@dataclass(frozen=True)
class ParsedChunk:
    state: ParserState
    tts_text: str = ""
    transcript_text: str = ""
    error_code: str | None = None


def markup_at(text: str, index: int) -> str | None:
    candidate = text[index : index + 32]
    for markup in MARKUP:
        if candidate.startswith(markup):
            return markup
    if any(markup.startswith(candidate) for markup in MARKUP):
        return None
    return ""


def tag_at(text: str, index: int) -> str | None:
    candidate = text[index : index + 33]
    end = candidate.find(">")
    if end != -1:
        candidate = candidate[: end + 1]
    if len(candidate) > 32:
        return ""
    if candidate.endswith(">"):
        return candidate if TAG_SHAPE.fullmatch(candidate[:-1]) else ""
    if candidate == "<" or TAG_SHAPE.fullmatch(candidate):
        return None
    return ""


def span_boundary(char: str) -> bool:
    return (
        not char
        or char.isspace()
        or char in punctuation
        or unicodedata.category(char).startswith("P")
    )


def parse_chunk(state: ParserState, text: str, *, final: bool = False) -> ParsedChunk:
    """Start each reply with ParserState(); flush once with final=True at its end.

    The caller logs only error_code when present, never text or parser state.
    """
    text = state.pending + text
    state = replace(state, pending="")
    error_code = None
    if not state.control_done:
        text = text.lstrip()
        token = markup_at(text, 0) if text.startswith("<") else ""
        if not final and (not text or token is None):
            return ParsedChunk(replace(state, pending=text))

        state = replace(state, control_done=True)
        match = CONTROL_TOKEN.fullmatch(token or "")
        if match:
            state = replace(state, crisis=int(match[1]))
            text = text[len(match[0]) :]
        elif text.startswith("<state"):
            error_code = "control_token_malformed"
        else:
            error_code = "control_token_missing"

    tts = []
    transcript = []
    index = 0
    previous = state.previous_char
    while index < len(text):
        char = text[index]
        if char == "<":
            tag = tag_at(text, index)
            if tag is None and not final:
                state = replace(state, pending=text[index:])
                break
            if tag:
                if tag in ALLOWED_TAGS:
                    tts.append(tag)
                previous = ">"
                index += len(tag)
                continue
        if char == "*":
            following = text[index + 1 : index + 2]
            if not following and not final:
                state = replace(state, pending="*")
                break
            opens = span_boundary(previous) and following and not following.isspace()
            closes = previous and not previous.isspace() and span_boundary(following)
            if opens or closes:
                previous = char
                index += 1
                continue
        tts.append(char)
        transcript.append(char)
        previous = char
        index += 1

    state = replace(state, previous_char=previous)
    return ParsedChunk(state, "".join(tts), "".join(transcript), error_code)
