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
    assert len(ROWS) == len({row["id"] for row in ROWS}) == 48
    assert Counter(row["split"] for row in ROWS) == {"dev": 32, "heldout": 16}
    assert Counter(row["category"] for row in ROWS) == {
        "recall_memory": 6,
        "remember": 6,
        "web_search": 6,
        "get_time": 6,
        "no_tool": 12,
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
    assert result["metrics"]["call_accuracy"] == 0.5
    assert result["metrics"]["tool_accuracy"] == result["metrics"]["argument_quality"] == 0
    assert result["metrics"]["crisis_priority_accuracy"] == 0
    assert result["metrics"]["malformed_call_rate"] is None
    assert result["metrics"]["continuation_valid_rate"] is None
    assert result["counts"]["attempts"] == 0
    assert evaluate("tools", [])["status"] == "FAIL"
