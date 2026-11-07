import pytest

from server.control_tokens import MOODS, ParserState, parse_chunk


def parse_parts(parts):
    state = ParserState()
    results = []
    for part in parts:
        result = parse_chunk(state, part)
        results.append(result)
        state = result.state
    results.append(parse_chunk(state, "", final=True))
    return (
        results[-1].state,
        "".join(result.tts_text for result in results),
        "".join(result.transcript_text for result in results),
        [result.error_code for result in results if result.error_code],
    )


@pytest.mark.parametrize("mood", MOODS)
@pytest.mark.parametrize("crisis", [0, 1])
def test_valid_control_token_at_every_boundary(mood, crisis):
    reply = f"<state mood={mood} crisis={crisis}>Hello."
    for split in range(len(reply) + 1):
        state, tts, transcript, errors = parse_parts([reply[:split], reply[split:]])
        assert (state.mood, state.crisis) == (mood, crisis)
        assert tts == transcript == "Hello."
        assert not errors


def test_partial_control_token_emits_nothing():
    result = parse_chunk(ParserState(), "<state mood=soft crisis=")
    assert not result.state.control_done
    assert result.tts_text == result.transcript_text == ""
    assert result.error_code is None
    assert parse_chunk(result.state, "0>Hi.").transcript_text == "Hi."


@pytest.mark.parametrize(
    "token",
    [
        "<state mood=happy crisis=0>",
        "<state mood=soft crisis=2>",
        "<state mood=soft>",
        "<state crisis=0>",
        "<state mood=soft crisis=0 extra=1>",
        '<state mood="soft" crisis=0>',
        "<state mood=SOFT crisis=0>",
    ],
)
def test_malformed_control_token_at_every_boundary(token):
    reply = token + "Hello."
    for split in range(len(reply) + 1):
        state, tts, transcript, errors = parse_parts([reply[:split], reply[split:]])
        assert (state.mood, state.crisis) == ("neutral", 0)
        assert tts == transcript == "Hello."
        assert errors == ["control_token_malformed"]


@pytest.mark.parametrize("reply", ["Hello.", "", " \n"])
def test_missing_control_token_defaults_once(reply):
    state, tts, transcript, errors = parse_parts([reply, "", ""])
    assert (state.mood, state.crisis) == ("neutral", 0)
    assert tts == transcript == reply.lstrip()
    assert errors == ["control_token_missing"]


def test_unfinished_control_token_is_dropped_at_end():
    state, tts, transcript, errors = parse_parts(["<state mood=soft crisis=1"])
    assert (state.mood, state.crisis) == ("neutral", 0)
    assert tts == transcript == ""
    assert errors == ["control_token_malformed"]


def test_leading_whitespace_and_character_chunks():
    state, tts, transcript, errors = parse_parts(list(" \n<state mood=soft crisis=0>Hi."))
    assert state.mood == "soft"
    assert tts == transcript == "Hi."
    assert not errors


def test_parse_is_pure_and_state_is_per_reply(capsys, caplog):
    state = ParserState()
    first = parse_chunk(state, "<state mood=soft crisis=1>Private text")
    assert first == parse_chunk(state, "<state mood=soft crisis=1>Private text")
    assert state == ParserState()
    second = parse_chunk(ParserState(), "Other private text", final=True)
    assert (second.state.mood, second.state.crisis) == ("neutral", 0)
    assert second.error_code == "control_token_missing"
    assert not caplog.records
    assert capsys.readouterr() == ("", "")
