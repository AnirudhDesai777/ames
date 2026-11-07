import pytest

from server.control_tokens import ALLOWED_TAGS, MOODS, ParserState, parse_chunk


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


@pytest.mark.parametrize("tag", sorted(ALLOWED_TAGS))
def test_allowed_tags_are_only_in_tts_at_every_boundary(tag):
    reply = f"<state mood=amused crisis=0>Hello {tag} there."
    for parts in [[reply[:split], reply[split:]] for split in range(len(reply) + 1)] + [
        list(reply)
    ]:
        state, tts, transcript, errors = parse_parts(parts)
        assert state.mood == "amused"
        assert tts == f"Hello {tag} there."
        assert transcript == "Hello  there."
        assert not errors


@pytest.mark.parametrize(
    "markup",
    [
        "<unknown>",
        "</laugh>",
        '<laugh volume="loud">',
        "<LAUGH>",
        "*laughs*",
        "*laughs <gasp> quietly*",
        "<state mood=annoyed crisis=1>",
    ],
)
def test_unknown_tags_and_actions_are_removed_at_every_boundary(markup):
    reply = f"<state mood=soft crisis=0>Hello {markup} there."
    for split in range(len(reply) + 1):
        state, tts, transcript, errors = parse_parts([reply[:split], "", reply[split:]])
        assert (state.mood, state.crisis) == ("soft", 0)
        assert tts == transcript == "Hello  there."
        assert not errors


def test_sanitizer_handles_multiple_boundaries_in_one_reply():
    reply = "<state mood=excited crisis=0><laugh>Hi.<unknown>*waves*Bye.<sigh>"
    for first in range(len(reply) + 1):
        for second in range(first, len(reply) + 1):
            state, tts, transcript, errors = parse_parts(
                [reply[:first], reply[first:second], reply[second:]]
            )
            assert tts == "<laugh>Hi.Bye.<sigh>"
            assert transcript == "Hi.Bye."
            assert not state.pending
            assert not errors


@pytest.mark.parametrize("suffix", ["<", "<laugh", "<unknown", "*", "*laughs"])
def test_unfinished_markup_is_never_emitted(suffix):
    first = parse_chunk(ParserState(), "<state mood=neutral crisis=0>Hi." + suffix)
    assert first.tts_text == first.transcript_text == "Hi."
    assert first.state.pending == suffix
    last = parse_chunk(first.state, "", final=True)
    assert last.tts_text == last.transcript_text == ""
    assert not last.state.pending
    assert last.error_code is None


def test_partial_inline_tag_waits_for_closing_chunk():
    first = parse_chunk(ParserState(), "<state mood=amused crisis=0>Hi.<lau")
    assert first.tts_text == first.transcript_text == "Hi."
    last = parse_chunk(first.state, "gh>Bye.", final=True)
    assert last.tts_text == "<laugh>Bye."
    assert last.transcript_text == "Bye."


@pytest.mark.parametrize("tag", ["<laugh>", "<unknown>"])
def test_missing_control_token_still_sanitizes_first_tag(tag):
    state, tts, transcript, errors = parse_parts(list(tag + "Hello."))
    assert (state.mood, state.crisis) == ("neutral", 0)
    assert tts == ("<laugh>" if tag == "<laugh>" else "") + "Hello."
    assert transcript == "Hello."
    assert errors == ["control_token_missing"]


def test_final_call_can_supply_text_and_drops_unfinished_markup():
    result = parse_chunk(
        ParserState(), "<state mood=soft crisis=0><sigh>Hello.*unfinished", final=True
    )
    assert result.tts_text == "<sigh>Hello."
    assert result.transcript_text == "Hello."
    assert not result.state.pending
    assert result.error_code is None


def test_transcript_has_no_tags_in_any_emitted_chunk():
    state = ParserState()
    reply = "<state mood=soft crisis=0><sigh>Hi.<unknown>*laughs*Bye.<laugh>"
    for part in [*reply, ""]:
        result = parse_chunk(state, part, final=not part)
        state = result.state
        assert not any(marker in result.transcript_text for marker in "<>*")
