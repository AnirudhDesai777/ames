import pytest

from server.control_tokens import ParserState, parse_chunk


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


@pytest.mark.parametrize("crisis", [0, 1])
def test_valid_control_token_at_every_boundary(crisis):
    reply = f"<state crisis={crisis}>Hello."
    for split in range(len(reply) + 1):
        state, tts, transcript, errors = parse_parts([reply[:split], reply[split:]])
        assert state.crisis == crisis
        assert tts == transcript == "Hello."
        assert not errors


def test_partial_control_token_emits_nothing():
    result = parse_chunk(ParserState(), "<state crisis=")
    assert not result.state.control_done
    assert result.tts_text == result.transcript_text == ""
    assert result.error_code is None
    assert parse_chunk(result.state, "0>Hi.").transcript_text == "Hi."


@pytest.mark.parametrize(
    ("token", "expected"),
    [
        ("<state mood=soft crisis=0>", ""),
        ("<state mood=amused crisis=1>", ""),
        ("<state crisis=2>", ""),
        ("<state crisis=>", ""),
        ("<state>", ""),
        ("<state crisis=0 extra=1>", ""),
        ('<state crisis="0">', '<state crisis="0">'),
        ("<state CRISIS=0>", ""),
    ],
)
def test_malformed_control_token_at_every_boundary(token, expected):
    reply = token + "Hello."
    for split in range(len(reply) + 1):
        state, tts, transcript, errors = parse_parts([reply[:split], reply[split:]])
        assert state.crisis == 0
        assert tts == transcript == expected + "Hello."
        assert errors == ["control_token_malformed"]


@pytest.mark.parametrize("reply", ["Hello.", "", " \n"])
def test_missing_control_token_defaults_once(reply):
    state, tts, transcript, errors = parse_parts([reply, "", ""])
    assert state.crisis == 0
    assert tts == transcript == reply.lstrip()
    assert errors == ["control_token_missing"]


def test_leading_whitespace_and_character_chunks():
    state, tts, transcript, errors = parse_parts(list(" \n<state crisis=0>Hi."))
    assert state.crisis == 0
    assert tts == transcript == "Hi."
    assert not errors


def test_parse_is_pure_and_state_is_per_reply(capsys, caplog):
    state = ParserState()
    first = parse_chunk(state, "<state crisis=1>Private text")
    assert first == parse_chunk(state, "<state crisis=1>Private text")
    assert state == ParserState()
    second = parse_chunk(ParserState(), "Other private text", final=True)
    assert second.state.crisis == 0
    assert second.error_code == "control_token_missing"
    assert not caplog.records
    assert capsys.readouterr() == ("", "")


@pytest.mark.parametrize("tag", ["<laugh>", "<chuckle>", "<sigh>", "<gasp>", "<groan>", "<yawn>"])
def test_formerly_allowed_tags_are_stripped_at_every_boundary(tag):
    reply = f"<state crisis=0>Hello {tag} there."
    for parts in [[reply[:split], reply[split:]] for split in range(len(reply) + 1)] + [
        list(reply)
    ]:
        state, tts, transcript, errors = parse_parts(parts)
        assert state.crisis == 0
        assert tts == "Hello  there."
        assert transcript == "Hello  there."
        assert not errors


@pytest.mark.parametrize(
    ("markup", "tts_markup", "transcript_markup"),
    [
        ("<unknown>", "", ""),
        ("<bogus>", "", ""),
        ("<smile>", "", ""),
        ("</laugh>", "", ""),
        ('<laugh volume="loud">', '<laugh volume="loud">', '<laugh volume="loud">'),
        ("<LAUGH>", "", ""),
        ("*laughs*", "laughs", "laughs"),
        ("*laughs <gasp> quietly*", "laughs  quietly", "laughs  quietly"),
        ("<state crisis=1>", "", ""),
    ],
)
def test_inline_markup_at_every_boundary(markup, tts_markup, transcript_markup):
    reply = f"<state crisis=0>Hello {markup} there."
    for split in range(len(reply) + 1):
        state, tts, transcript, errors = parse_parts([reply[:split], "", reply[split:]])
        assert state.crisis == 0
        assert tts == f"Hello {tts_markup} there."
        assert transcript == f"Hello {transcript_markup} there."
        assert not errors


@pytest.mark.parametrize("tag", ["<bogus>", "<smile>", "</laugh>"])
def test_adjacent_known_and_unknown_tags_at_every_boundary(tag):
    reply = f"<state crisis=0>ha <laugh> ok {tag} done"
    for parts in [[reply[:split], reply[split:]] for split in range(len(reply) + 1)] + [
        list(reply)
    ]:
        state, tts, transcript, errors = parse_parts(parts)
        assert tts == "ha  ok  done"
        assert transcript == "ha  ok  done"
        assert not state.pending
        assert not errors


@pytest.mark.parametrize(
    ("markup", "expected"),
    [
        ("<a1_b-c=d>", ""),
        ("<state anything=1 extra>", ""),
        ("<name space>", "<name space>"),
        ("<state\tbad>", "<state\tbad>"),
        ("<a/b>", "<a/b>"),
        ("<>", "<>"),
        ("<" + "a" * 30 + ">", ""),
        ("<" + "a" * 31 + ">", "<" + "a" * 31 + ">"),
        ("<bad!<smile>>", "<bad!>"),
        ("<" + "a" * 32 + "<smile>", "<" + "a" * 32),
    ],
)
def test_tag_shape_and_limit_at_every_boundary(markup, expected):
    reply = "<state crisis=0>" + markup
    for parts in [[reply[:split], reply[split:]] for split in range(len(reply) + 1)] + [
        list(reply)
    ]:
        state, tts, transcript, errors = parse_parts(parts)
        assert tts == transcript == expected
        assert not state.pending
        assert not errors


def test_sanitizer_handles_multiple_boundaries_in_one_reply():
    reply = "<state crisis=0><laugh>Hi.<unknown>*waves*Bye.<sigh>"
    for first in range(len(reply) + 1):
        for second in range(first, len(reply) + 1):
            state, tts, transcript, errors = parse_parts(
                [reply[:first], reply[first:second], reply[second:]]
            )
            assert tts == "Hi.waves*Bye."
            assert transcript == "Hi.waves*Bye."
            assert not state.pending
            assert not errors


@pytest.mark.parametrize(
    "suffix", ["<", "<laugh", "<bogus", "<smile", "</laugh", "<state crisis=1"]
)
@pytest.mark.parametrize("prefix", ["", "<state crisis=0>Hi."])
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
    first = parse_chunk(ParserState(), "<state crisis=0>Hi.<lau")
    assert first.tts_text == first.transcript_text == "Hi."
    last = parse_chunk(first.state, "gh>Bye.", final=True)
    assert last.tts_text == "Bye."
    assert last.transcript_text == "Bye."


@pytest.mark.parametrize("tag", ["<laugh>", "<unknown>"])
def test_missing_control_token_still_sanitizes_first_tag(tag):
    state, tts, transcript, errors = parse_parts(list(tag + "Hello."))
    assert state.crisis == 0
    assert tts == "Hello."
    assert transcript == "Hello."
    assert errors == ["control_token_missing"]


def test_final_call_can_supply_text_and_keeps_unfinished_words():
    result = parse_chunk(ParserState(), "<state crisis=0><sigh>Hello.*unfinished", final=True)
    assert result.tts_text == "Hello.unfinished"
    assert result.transcript_text == "Hello.unfinished"
    assert not result.state.pending
    assert result.error_code is None


def test_transcript_has_no_tags_in_any_emitted_chunk():
    state = ParserState()
    reply = "<state crisis=0><sigh>Hi.<bogus>*laughs* Bye.<smile></laugh><laugh>"
    for part in [*reply, ""]:
        result = parse_chunk(state, part, final=not part)
        state = result.state
        assert not any(marker in result.transcript_text for marker in "<>*")


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("3 < 5 is true. Next sentence.", "3 < 5 is true. Next sentence."),
        ("x < y and y > z", "x < y and y > z"),
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
    reply = "<state crisis=0>" + text
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


@pytest.mark.parametrize("prefix", ["", "<state crisis=0>"])
@pytest.mark.parametrize("stray", ["< ", "<3", "<laugh ", "<state\t", "<" + "a" * 64])
def test_long_tail_after_stray_angle_bracket_streams_before_final(prefix, stray):
    tail = " words keep streaming." * 1000
    first = parse_chunk(ParserState(), prefix + stray)
    assert first.tts_text == first.transcript_text == stray
    assert not first.state.pending
    last = parse_chunk(first.state, tail)
    assert last.tts_text == last.transcript_text == tail
    assert not last.state.pending


def test_trailing_asterisk_holds_only_lookahead():
    first = parse_chunk(ParserState(), "<state crisis=0>2*")
    assert first.tts_text == first.transcript_text == "2"
    assert first.state.pending == "*"
    empty = parse_chunk(first.state, "")
    assert empty.state == first.state
    last = parse_chunk(empty.state, "3 *really")
    assert last.tts_text == last.transcript_text == "*3 really"
    assert not last.state.pending


def test_stray_angle_bracket_releases_long_tail_without_final():
    first = parse_chunk(ParserState(), "<state crisis=0><")
    assert first.state.pending == "<"
    tail = " words keep streaming." * 1000
    last = parse_chunk(first.state, tail)
    assert last.tts_text == last.transcript_text == "<" + tail
    assert not last.state.pending
