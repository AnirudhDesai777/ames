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
        assert tts == transcript == token + "Hello."
        assert errors == ["control_token_malformed"]


@pytest.mark.parametrize("reply", ["Hello.", "", " \n"])
def test_missing_control_token_defaults_once(reply):
    state, tts, transcript, errors = parse_parts([reply, "", ""])
    assert (state.mood, state.crisis) == ("neutral", 0)
    assert tts == transcript == reply.lstrip()
    assert errors == ["control_token_missing"]


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
    ("markup", "tts_markup", "transcript_markup"),
    [
        ("<unknown>", "<unknown>", "<unknown>"),
        ("</laugh>", "</laugh>", "</laugh>"),
        ('<laugh volume="loud">', '<laugh volume="loud">', '<laugh volume="loud">'),
        ("<LAUGH>", "<LAUGH>", "<LAUGH>"),
        ("*laughs*", "laughs", "laughs"),
        ("*laughs <gasp> quietly*", "laughs <gasp> quietly", "laughs  quietly"),
        ("<state mood=annoyed crisis=1>", "", ""),
    ],
)
def test_inline_markup_at_every_boundary(markup, tts_markup, transcript_markup):
    reply = f"<state mood=soft crisis=0>Hello {markup} there."
    for split in range(len(reply) + 1):
        state, tts, transcript, errors = parse_parts([reply[:split], "", reply[split:]])
        assert (state.mood, state.crisis) == ("soft", 0)
        assert tts == f"Hello {tts_markup} there."
        assert transcript == f"Hello {transcript_markup} there."
        assert not errors


def test_sanitizer_handles_multiple_boundaries_in_one_reply():
    reply = "<state mood=excited crisis=0><laugh>Hi.<unknown>*waves*Bye.<sigh>"
    for first in range(len(reply) + 1):
        for second in range(first, len(reply) + 1):
            state, tts, transcript, errors = parse_parts(
                [reply[:first], reply[first:second], reply[second:]]
            )
            assert tts == "<laugh>Hi.<unknown>waves*Bye.<sigh>"
            assert transcript == "Hi.<unknown>waves*Bye."
            assert not state.pending
            assert not errors


@pytest.mark.parametrize("suffix", ["<", "<laugh", "<state mood=soft crisis=1"])
@pytest.mark.parametrize("prefix", ["", "<state mood=neutral crisis=0>Hi."])
def test_held_candidate_is_emitted_at_final_at_every_boundary(prefix, suffix):
    reply = prefix + suffix
    for split in range(len(reply) + 1):
        state, tts, transcript, errors = parse_parts([reply[:split], reply[split:]])
        assert tts == transcript == ("Hi." if prefix else "") + suffix
        assert not state.pending
        assert errors == (
            []
            if prefix
            else [
                "control_token_malformed"
                if suffix.startswith("<state")
                else "control_token_missing"
            ]
        )
    first = parse_chunk(ParserState(), reply)
    assert first.state.pending == suffix
    last = parse_chunk(first.state, "", final=True)
    assert last.tts_text == last.transcript_text == suffix
    assert not last.state.pending


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
    assert tts == tag + "Hello."
    assert transcript == ("" if tag == "<laugh>" else tag) + "Hello."
    assert errors == ["control_token_missing"]


def test_final_call_can_supply_text_and_keeps_unfinished_words():
    result = parse_chunk(
        ParserState(), "<state mood=soft crisis=0><sigh>Hello.*unfinished", final=True
    )
    assert result.tts_text == "<sigh>Hello.unfinished"
    assert result.transcript_text == "Hello.unfinished"
    assert not result.state.pending
    assert result.error_code is None


def test_transcript_has_no_tags_in_any_emitted_chunk():
    state = ParserState()
    reply = "<state mood=soft crisis=0><sigh>Hi.*laughs* Bye.<laugh>"
    for part in [*reply, ""]:
        result = parse_chunk(state, part, final=not part)
        state = result.state
        assert not any(marker in result.transcript_text for marker in "<>*")


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("3 < 5 is true. Next sentence.", "3 < 5 is true. Next sentence."),
        ("2*3 is 6 and 4*5 is 20.", "2*3 is 6 and 4*5 is 20."),
        ("This is *really* good.", "This is really good."),
        ("<3 you", "<3 you"),
        ("*really*", "really"),
        ("*waves*", "waves"),
        ("2*3", "2*3"),
        ("a * b", "a * b"),
        ("* item", "* item"),
        ("*", "*"),
        ("(*really*), *waves*!", "(really), waves!"),
        ("\u201c*really*\u201d", "\u201creally\u201d"),
    ],
)
def test_literal_text_and_asterisk_delimiters_at_every_boundary(text, expected):
    reply = "<state mood=soft crisis=0>" + text
    for parts in [[reply[:split], "", reply[split:]] for split in range(len(reply) + 1)] + [
        list(reply)
    ]:
        state, tts, transcript, errors = parse_parts(parts)
        assert tts == transcript == expected
        assert not state.pending
        assert not errors


def test_leading_literal_angle_bracket_resolves_without_final():
    reply = "<3 you"
    for split in range(len(reply) + 1):
        first = parse_chunk(ParserState(), reply[:split])
        last = parse_chunk(first.state, reply[split:])
        assert first.tts_text + last.tts_text == reply
        assert first.transcript_text + last.transcript_text == reply
        assert [code for code in (first.error_code, last.error_code) if code] == [
            "control_token_missing"
        ]
        assert last.state.control_done
        assert not last.state.pending


@pytest.mark.parametrize("prefix", ["", "<state mood=soft crisis=0>"])
@pytest.mark.parametrize("stray", ["< ", "<3", "<unknown", "<laugh ", "<state\t", "<" + "a" * 64])
def test_long_tail_after_stray_angle_bracket_streams_before_final(prefix, stray):
    tail = " words keep streaming." * 1000
    first = parse_chunk(ParserState(), prefix + stray)
    assert first.tts_text == first.transcript_text == stray
    assert not first.state.pending
    last = parse_chunk(first.state, tail)
    assert last.tts_text == last.transcript_text == tail
    assert not last.state.pending


def test_trailing_asterisk_holds_only_lookahead():
    first = parse_chunk(ParserState(), "<state mood=soft crisis=0>2*")
    assert first.tts_text == first.transcript_text == "2"
    assert first.state.pending == "*"
    empty = parse_chunk(first.state, "")
    assert empty.state == first.state
    last = parse_chunk(empty.state, "3 *really")
    assert last.tts_text == last.transcript_text == "*3 really"
    assert not last.state.pending


def test_stray_angle_bracket_releases_long_tail_without_final():
    first = parse_chunk(ParserState(), "<state mood=soft crisis=0><")
    assert first.state.pending == "<"
    tail = " words keep streaming." * 1000
    last = parse_chunk(first.state, tail)
    assert last.tts_text == last.transcript_text == "<" + tail
    assert not last.state.pending
