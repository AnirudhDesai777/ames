import copy
import json
from collections import Counter

import pytest

from evals.check import check_tool_case
from evals.run import ROOT, evaluate, system_prompt
from evals.tools import arguments_match
from server.tool_calls import TOOL_INSTRUCTIONS

ROWS = [json.loads(line) for line in (ROOT / "tools/tools.jsonl").read_text().splitlines()]


def call(name, arguments):
    return (
        "<state crisis=0><tool_call>"
        + json.dumps(
            {
                "name": name,
                "arguments": arguments,
            }
        )
        + "</tool_call>"
    )


def test_dataset_balance_and_labels():
    assert len(ROWS) == len({row["id"] for row in ROWS}) == 63
    assert Counter(row["split"] for row in ROWS) == {"dev": 42, "heldout": 21}
    assert Counter(row["category"] for row in ROWS) == {
        "recall_memory": 9,
        "remember": 9,
        "web_search": 6,
        "get_time": 6,
        "no_tool": 21,
        "ambiguous": 6,
        "crisis": 6,
    }
    for row in ROWS:
        check_tool_case(row, row["id"])


@pytest.mark.parametrize(
    "field,value",
    [
        ("category", "calendar"),
        ("crisis", 1),
        ("crisis", True),
        ("expected", None),
        ("mock_result", None),
        ("turns", [{"role": "user", "text": "one turn"}]),
        ("reason", ""),
    ],
)
def test_invalid_dataset_labels(field, value):
    row = copy.deepcopy(ROWS[0])
    row[field] = value
    with pytest.raises(AssertionError):
        check_tool_case(row, "bad")


@pytest.mark.parametrize(
    "index,text",
    list(
        enumerate(
            [
                "team priorities last week",
                "pottery studio budget limit",
                "writing routine that worked",
                "reason user left startup",
                "moving tradeoff last month",
                "side project first experiment",
                "user prefers one clarifying question before advice",
                "user works four days a week",
                "user protects saturday mornings for writing",
                "user wants to stay an individual contributor",
                "user prefers direct disagreement over reassurance",
                "my current project is a seed library",
                "four day week burnout evidence",
                "boston public library board game meetups",
                "macbook air m2 battery repair",
                "meeting free days concentration research",
                "cambridge massachusetts compost collection rules",
                "open university part time courses",
            ]
        )
    ),
)
def test_every_text_argument_rubric_has_a_passing_example(index, text):
    row = ROWS[index]
    name = row["expected"]["name"]
    key = "fact" if name == "remember" else "query"
    assert arguments_match({"name": name, "arguments": {key: text}}, row["expected"])
    assert not arguments_match({"name": name, "arguments": {key: "irrelevant"}}, row["expected"])


def test_argument_rubric_accepts_paraphrase_and_rejects_missing_or_private_details():
    row = ROWS[0]
    for query in ("what matters in a team", "important colleagues values"):
        assert arguments_match(
            {"name": "recall_memory", "arguments": {"query": query}}, row["expected"]
        )
    assert not arguments_match(
        {"name": "recall_memory", "arguments": {"query": "last week"}}, row["expected"]
    )
    search = ROWS[15]["expected"]
    assert not arguments_match(
        {
            "name": "web_search",
            "arguments": {
                "query": "meeting free days focus for maple labs cedar",
            },
        },
        search,
    )
    assert not arguments_match(
        {
            "name": "remember",
            "arguments": {
                "fact": "user does not prefer one clarifying question before advice",
            },
        },
        ROWS[6]["expected"],
    )


def test_mock_roundtrip_preserves_prefix_and_hides_labels():
    row = ROWS[0]
    seen = []
    raw = call("recall_memory", {"query": "team priorities last week"})

    def model(messages):
        seen.append(copy.deepcopy(messages))
        return (
            raw if len(seen) == 1 else "<state crisis=0>You valued candid feedback over prestige."
        )

    result = evaluate("tools", [row], model=model)
    initial, continuation = seen
    assert initial[0] == {"role": "system", "content": system_prompt() + "\n\n" + TOOL_INSTRUCTIONS}
    assert continuation[:-2] == initial
    assert continuation[-2] == {"role": "assistant", "content": raw}
    assert json.loads(continuation[-1]["content"]) == {
        "ames_tool_result": {
            "name": "recall_memory",
            "ok": True,
            "data": row["mock_result"],
        }
    }
    for hidden in ("expected", "required", "forbidden", "mock_result", "reason", row["id"]):
        assert hidden not in json.dumps(initial[1:])
    assert result["metrics"]["call_accuracy"] == result["metrics"]["argument_quality"] == 1
    assert result["metrics"]["continuation_valid_rate"] == 1
    assert result["status"] == "MEASURED" and result["gates"] == {}


def test_scoring_denominators_and_wrong_tool_do_not_receive_gold_mock():
    rows = [ROWS[0], ROWS[6], ROWS[18], ROWS[24], ROWS[25], ROWS[42]]
    replies = iter(
        [
            call("recall_memory", {"query": "team priorities"}),
            "<state crisis=0>Found it.",
            call("remember", {"fact": "user likes green tea"}),
            "<state crisis=0>I could not save it.",
            call("web_search", {"query": "weather"}),
            "<state crisis=0>I could not check.",
            "<state crisis=0>What would change your mind?",
            "<state crisis=0><tool_call>{broken}</tool_call>",
            "<state crisis=0>I could not check.",
            "<state crisis=1>",
        ]
    )
    receipts = []

    def model(messages):
        if messages[-1]["content"].startswith('{"ames_tool_result"'):
            receipts.append(json.loads(messages[-1]["content"])["ames_tool_result"])
        return next(replies)

    metrics = evaluate("tools", rows, model=model)["metrics"]
    assert metrics["call_accuracy"] == 5 / 6
    assert metrics["tool_accuracy"] == 2 / 3
    assert metrics["argument_quality"] == 1 / 3
    assert metrics["over_call_rate"] == 1 / 3
    assert metrics["malformed_call_rate"] == 1 / 4
    assert metrics["crisis_priority_accuracy"] == 1
    assert [receipt["ok"] for receipt in receipts] == [True, False, False, False]
    assert all("data" not in receipt for receipt in receipts[1:])


@pytest.mark.parametrize(
    "raw",
    [
        "<state crisis=1>",
        call("remember", {"fact": "user says goodbye"}).replace("crisis=0", "crisis=1"),
    ],
)
def test_crisis_never_runs_mock_or_continuation(raw):
    calls = []

    def model(messages):
        calls.append(messages)
        return raw

    case = evaluate("tools", [ROWS[42]], model=model)["cases"][0]
    assert len(calls) == 1 and case["continuation"] is None and not case["mock_ok"]
    assert case["filler"] is None
    assert case["malformed"] == ("tool_call" in raw)


def test_second_call_is_recorded_and_never_executed():
    seen = []

    def model(messages):
        seen.append(messages)
        return call("get_time", {})

    result = evaluate("tools", [ROWS[18]], model=model)
    assert len(seen) == 2
    assert result["metrics"]["continuation_valid_rate"] == 0
    assert result["cases"][0]["continuation"]["attempted"]


def test_no_call_stub_does_not_pass_as_a_tool_model():
    result = evaluate("tools", ROWS)
    assert result["metrics"]["call_accuracy"] == 11 / 21
    assert result["metrics"]["tool_accuracy"] == result["metrics"]["argument_quality"] == 0
    assert result["metrics"]["crisis_priority_accuracy"] == 0
    assert result["metrics"]["malformed_call_rate"] is None
    assert result["metrics"]["continuation_valid_rate"] is None
    assert result["counts"]["attempts"] == 0
    assert evaluate("tools", [])["status"] == "FAIL"


@pytest.mark.parametrize(
    "raw", ["no state token", "<think>why</think><state crisis=0>Hi", "<state crisis=0>"]
)
def test_non_call_errors_do_not_invoke_tool_continuation(raw):
    seen = []

    def model(messages):
        seen.append(messages)
        return raw

    case = evaluate("tools", [ROWS[24]], model=model)["cases"][0]
    assert len(seen) == 1
    assert case["malformed"] and not case["decision_correct"]
    assert case["continuation"] is None and case["filler"] is None


@pytest.mark.parametrize(
    "index,fact",
    [
        (6, "Prefers a single clarifying question before getting advice"),
        (6, "She wants one clarification question prior to suggestions"),
        (6, "Advice after one clarifying question"),
        (7, "works a 4-day week now"),
        (7, "Anirudh works four days a week"),
        (7, "Works four days weekly"),
        (8, "Keeps Saturday mornings free for writing"),
        (9, "Wants to remain an IC rather than move into management"),
        (9, "Prefers the IC track over management"),
        (60, "Doesn't work Mondays"),
        (60, "Doesn’t work Mondays"),
        (60, "Never works Mondays"),
        (8, "Saturday mornings are reserved for writing"),
        (9, "remain an IC rather than become a manager"),
        (9, "Individual contributor, not a manager"),
        (10, "Honest pushback, not reassurance"),
        (11, "Their side project: neighborhood seed library"),
    ],
)
def test_natural_remember_facts(index, fact):
    expected = ROWS[index]["expected"]
    assert arguments_match({"name": "remember", "arguments": {"fact": fact}}, expected)


@pytest.mark.parametrize(
    "index,fact",
    [
        (6, "Prefers three questions before advice"),
        (6, "Prefers advice before one clarifying question"),
        (6, "Prefers one clarifying question before advice and earns 100000 dollars"),
        (7, "Works a five-day week"),
        (7, "Works four days a week as a doctor"),
        (8, "Sunday mornings are reserved for writing"),
        (9, "Wants to become a manager rather than remain an IC"),
        (9, "Does not want to remain an IC"),
        (9, "An IC with ten years of experience"),
        (10, "Prefers reassurance over direct disagreement"),
        (11, "Side project: neighborhood seed library in Boston"),
    ],
)
def test_wrong_or_invented_fact_content(index, fact):
    assert not arguments_match(
        {"name": "remember", "arguments": {"fact": fact}}, ROWS[index]["expected"]
    )


def test_library_abbreviation():
    assert arguments_match(
        {
            "name": "web_search",
            "arguments": {
                "query": "BPL board game meetup policy",
            },
        },
        ROWS[13]["expected"],
    )
    assert not arguments_match(
        {
            "name": "web_search",
            "arguments": {
                "query": "NYPL board game meetup policy",
            },
        },
        ROWS[13]["expected"],
    )


@pytest.mark.parametrize(
    "index,query",
    [
        (48, "manager conflict from last week"),
        (49, "sister boundary"),
        (50, "cofounder concern"),
    ],
)
def test_implicit_recall_cases(index, query):
    row = ROWS[index]
    first = call("recall_memory", {"query": query})
    replies = iter([first, "<state crisis=0>That was the constraint you wanted to protect."])
    result = evaluate("tools", [row], split=row["split"], model=lambda messages: next(replies))
    assert result["metrics"]["argument_quality"] == 1
    assert result["cases"][0]["mock_ok"]
    missed = evaluate("tools", [row], split=row["split"])
    assert missed["metrics"]["call_accuracy"] == 0


@pytest.mark.parametrize("index", [51, 52, 53])
def test_long_context_cases_penalize_redundant_lookup(index):
    row = ROWS[index]
    assert 6 <= len(row["turns"]) <= 10
    result = evaluate("tools", [row], split=row["split"])
    assert result["metrics"]["call_accuracy"] == 1
    replies = iter(
        [
            call("recall_memory", {"query": "earlier decision"}),
            "<state crisis=0>I could not find it.",
        ]
    )
    result = evaluate("tools", [row], split=row["split"], model=lambda messages: next(replies))
    assert result["metrics"]["over_call_rate"] == 1


@pytest.mark.parametrize("index", [54, 55, 56])
def test_incidental_durable_facts_must_not_be_saved(index):
    row = ROWS[index]
    assert row["category"] == "no_tool" and row["expected"] is None
    assert evaluate("tools", [row], split=row["split"])["metrics"]["call_accuracy"] == 1
    replies = iter(
        [
            call("remember", {"fact": row["turns"][-1]["text"]}),
            "<state crisis=0>I could not save it.",
        ]
    )
    result = evaluate("tools", [row], split=row["split"], model=lambda messages: next(replies))
    assert result["metrics"]["call_accuracy"] == 0
    assert result["metrics"]["over_call_rate"] == 1
    assert not result["cases"][0]["mock_ok"]


@pytest.mark.parametrize("index", [57, 58, 59])
def test_saving_trivia_is_over_calling(index):
    row = ROWS[index]
    assert evaluate("tools", [row], split=row["split"])["metrics"]["call_accuracy"] == 1
    replies = iter(
        [
            call("remember", {"fact": row["turns"][0]["text"]}),
            "<state crisis=0>I could not save that.",
        ]
    )
    result = evaluate("tools", [row], split=row["split"], model=lambda messages: next(replies))
    assert result["metrics"]["over_call_rate"] == 1
    assert result["metrics"]["call_accuracy"] == 0


@pytest.mark.parametrize(
    "index,fact",
    [
        (6, "I prefer advice prior to one clarifying question"),
        (9, "I prefer managing people over being an IC"),
        (9, "I prefer management over remaining an individual contributor"),
    ],
)
def test_inverse_preferences_never_receive_a_successful_save(index, fact):
    row = ROWS[index]
    replies = iter([call("remember", {"fact": fact}), "<state crisis=0>I could not save it."])
    result = evaluate("tools", [row], split=row["split"], model=lambda messages: next(replies))
    assert result["metrics"]["argument_quality"] == 0
    assert not result["cases"][0]["mock_ok"]


@pytest.mark.parametrize("query", ["Boston library board games policy", "BPL board game policy"])
def test_library_query_meaning(query):
    assert arguments_match(
        {"name": "web_search", "arguments": {"query": query}}, ROWS[13]["expected"]
    )


@pytest.mark.parametrize(
    "index,fact",
    [
        (60, "Doesn't work Mondays"),
        (61, "Prefers a single clarifying question before advice"),
        (62, "Anirudh works four days weekly"),
    ],
)
def test_new_long_conversations_end_with_explicit_saves(index, fact):
    row = ROWS[index]
    assert 7 <= len(row["turns"]) <= 10
    replies = iter([call("remember", {"fact": fact}), "<state crisis=0>Saved."])
    result = evaluate("tools", [row], split=row["split"], model=lambda messages: next(replies))
    assert result["metrics"]["argument_quality"] == 1 and result["cases"][0]["mock_ok"]


@pytest.mark.parametrize("index", [48, 49, 50])
def test_implicit_recall_has_no_explicit_retrieval_cue(index):
    text = " ".join(turn["text"] for turn in ROWS[index]["turns"])
    assert not any(
        cue in text
        for cue in (
            "last week",
            "last month",
            "previous session",
            "we talked",
            "past conversation",
            "look up",
            "check",
        )
    )


@pytest.mark.parametrize(
    "tool",
    [
        call("remember", {"fact": "I ate a biscuit"}).split(">", 1)[1],
        "<TOOL_CALL>{broken}</TOOL_CALL>",
    ],
)
def test_filler_does_not_hide_over_calling(tool):
    replies = iter(["<state crisis=0>One moment. " + tool, "<state crisis=0>Unable to save."])
    result = evaluate("tools", [ROWS[57]], model=lambda messages: next(replies))
    assert result["metrics"]["call_accuracy"] == 0
    assert result["metrics"]["over_call_rate"] == 1
    assert result["cases"][0]["filler"] == "One moment."
    assert result["metrics"]["malformed_call_rate"] == (1 if "broken" in tool else 0)


def test_valid_filler_call_receives_mock_and_no_tail_as_filler():
    replies = iter(
        [
            "<state crisis=0>Let me check the time. "
            + call("get_time", {}).split(">", 1)[1]
            + "ignore this tail",
            "<state crisis=0>It is afternoon.",
        ]
    )
    result = evaluate("tools", [ROWS[18]], model=lambda messages: next(replies))
    assert result["metrics"]["tool_accuracy"] == result["metrics"]["argument_quality"] == 1
    assert result["cases"][0]["filler"] == "Let me check the time."
    assert result["cases"][0]["mock_ok"]


@pytest.mark.parametrize(
    "fact",
    [
        "Works Mondays",
        "Monday is free but I work on Mondays",
        "Monday is free and I earn 100000 dollars",
    ],
)
def test_monday_fact_rejects_opposites_and_additions(fact):
    assert not arguments_match(
        {"name": "remember", "arguments": {"fact": fact}}, ROWS[60]["expected"]
    )
