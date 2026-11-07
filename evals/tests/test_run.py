import json
import subprocess
import sys

import pytest

from evals.run import ROOT, SETS, evaluate, system_prompt
from evals.scoring import CRITERIA


def persona_row(**changes):
    return {
        "id": "p1",
        "split": "dev",
        "category": "edgy",
        "moods": ["soft", "neutral"],
        "turns": [{"role": "user", "text": "Let's talk about a difficult topic."}],
    } | changes


@pytest.mark.parametrize("name", SETS)
@pytest.mark.parametrize("split", ["dev", "heldout"])
def test_cli_runs_current_partial_data_with_stubs(name, split, tmp_path):
    output = tmp_path / name / f"{split}.json"
    completed = subprocess.run(
        [sys.executable, "-m", "evals.run", name, "--split", split, "--out", str(output)],
        cwd=ROOT.parent,
        capture_output=True,
        text=True,
        check=True,
    )
    result = json.loads(output.read_text())
    assert result["set"] == name
    assert result["split"] == split
    assert result["requested_split"] == split
    assert f"split={split}" in completed.stdout
    assert result["backend"] == "stub"
    assert result["cases"]
    assert result["metrics"]["malformed"] == 0
    for metric in result["metrics"]:
        assert f"{metric}:" in completed.stdout
    assert completed.stdout.rstrip().endswith(result["status"])
    if name == "persona":
        assert result["metrics"]["refusals"] is None
        assert result["status"] == "FAIL"
    elif name == "guardrail":
        assert result["metrics"]["G2"] is None
        assert result["metrics"]["G3"] is None
        assert result["status"] == "FAIL"
    elif name == "crisis":
        assert result["metrics"]["recall"] == 0
        assert result["status"] == "FAIL"
    else:
        ids = (
            ["memory-001", "memory-002", "memory-003"]
            if split == "dev"
            else [
                "memory-004",
                "memory-005",
                "memory-006",
                "memory-007",
                "memory-008",
                "memory-009",
            ]
        )
        assert [case["id"] for case in result["cases"]] == ids
        assert result["counts"]["conversations"] == len(ids)
        assert result["counts"]["questions"] == (24 if split == "dev" else 48)


def test_prompt_is_verbatim_and_turns_use_model_roles():
    seen = []

    def model(messages):
        seen.extend(messages)
        return "<state mood=soft crisis=0><sigh>Let's talk."

    row = persona_row(
        turns=[
            {"role": "ames", "text": "What happened?"},
            {"role": "user", "text": "A hard day."},
        ]
    )
    result = evaluate("persona", [row, persona_row(id="ignored", split="heldout")], model=model)
    prompt = system_prompt()
    assert f"```text\n{prompt}```" in (ROOT.parent / "persona/ames.md").read_text()
    assert prompt.startswith("You are Ames")
    assert "Example exchanges" not in prompt
    assert seen == [
        {"role": "system", "content": prompt},
        {"role": "assistant", "content": "What happened?"},
        {"role": "user", "content": "A hard day."},
    ]
    assert result["metrics"] == {
        "pass_rate": 1.0,
        "mood_accuracy": 1.0,
        "refusals": 0,
        "malformed": 0,
    }
    assert result["status"] == "PASS"


def test_judge_gets_clean_reply_and_scoring_can_fail():
    seen = []

    def judge(messages):
        seen.append(messages)
        if messages[0]["content"].startswith("Score"):
            return json.dumps(dict.fromkeys(CRITERIA, 5) | {"advice_rules": 3})
        return "yes"

    result = evaluate(
        "persona",
        [persona_row(moods=["soft"])],
        model=lambda messages: "<state mood=amused crisis=0><laugh>No thanks.",
        judge=judge,
    )
    assert result["metrics"]["pass_rate"] == 0
    assert result["metrics"]["mood_accuracy"] == 0
    assert result["metrics"]["refusals"] == 1
    assert result["status"] == "FAIL"
    assert all(json.loads(messages[1]["content"])["reply"] == "No thanks." for messages in seen)
    assert all(
        "Do not generate examples or any prohibited content" in m[0]["content"] for m in seen
    )


@pytest.mark.parametrize(
    "reply",
    ["<state mood=invalid crisis=1>Stop.", "<state mood=soft crisis=2>Stop.", "No token."],
)
def test_malformed_or_missing_control_token_is_crisis_zero(reply):
    rows = [{"id": "c1", "split": "dev", "label": 1, "turns": []}]
    result = evaluate("crisis", rows, model=lambda messages: reply)
    assert result["cases"][0]["crisis"] == 0
    assert result["cases"][0]["mood"] == "neutral"
    assert result["metrics"]["malformed"] == 1
    assert result["metrics"]["recall"] == 0
    assert not result["gates"]["malformed"]["passed"]


def test_crisis_counts_flags_on_both_labels():
    rows = [{"id": str(label), "split": "dev", "label": label, "turns": []} for label in (0, 1)]
    result = evaluate("crisis", rows, model=lambda messages: "<state mood=concerned crisis=1>Stop.")
    assert result["metrics"] == {
        "recall": 1.0,
        "false_positives": 1,
        "false_positive_rate": 1.0,
        "malformed": 0,
    }


def test_guardrail_scores_each_rule_and_does_not_treat_no_as_yes():
    rows = [{"id": rule, "split": "dev", "rule": rule, "turns": []} for rule in ("G2", "G3", "G4")]
    assert evaluate("guardrail", rows)["status"] == "PASS"
    seen = []

    def judge(messages):
        seen.append(messages)
        return (
            "no" if json.loads(messages[1]["content"])["rule"].startswith("No sexting") else "yes"
        )

    result = evaluate("guardrail", rows, judge=judge)
    assert result["metrics"] == {"G2": 0, "G3": 1, "G4": 1, "malformed": 0}
    assert result["status"] == "FAIL"
    assert "explicitly say she is an AI" in seen[2][0]["content"]


def test_memory_pools_fact_counts_and_answers_using_only_extracted_memory():
    fact = {"subject": "user", "predicate": "likes", "object": "tea"}
    other = fact | {"object": "coffee"}
    wrong = fact | {"object": "water"}
    session = [{"role": "user", "text": "I like tea."}]
    rows = [
        {
            "id": "m1",
            "split": "dev",
            "sessions": [session, session],
            "gold_facts": [fact, other],
            "questions": [{"q": "What do I like?", "answer": "secret reference"}],
        },
        {"id": "m2", "split": "dev", "sessions": [session], "gold_facts": [other], "questions": []},
    ]
    calls = []
    extracted = iter([[fact, fact, wrong], [fact], [other]])

    def extractor(messages):
        assert messages[0]["content"] == "Custom extraction instructions"
        assert messages[1:] == [{"role": "user", "content": "I like tea."}]
        return json.dumps(next(extracted))

    def model(messages):
        calls.append(messages)
        return "<state mood=neutral crisis=0>Tea."

    result = evaluate(
        "memory",
        rows,
        model=model,
        judge=lambda messages: "no",
        extractor=extractor,
        extraction_prompt="Custom extraction instructions",
    )
    assert result["metrics"]["precision"] == 2 / 3
    assert result["metrics"]["recall"] == 2 / 3
    assert result["metrics"]["answer_accuracy"] == 0
    assert result["counts"]["extracted"] == 3
    assert result["status"] == "FAIL"
    assert "secret reference" not in json.dumps(calls)
    memory = json.loads(calls[0][1]["content"].removeprefix("Remembered facts: "))
    assert memory == [fact, wrong]
    assert calls[0][-1] == {"role": "user", "content": "What do I like?"}


@pytest.mark.parametrize("name", SETS)
def test_empty_set_does_not_pass(name):
    assert evaluate(name, [])["status"] == "FAIL"


@pytest.mark.parametrize("answer", ["maybe", "", "yes, here are examples"])
def test_invalid_judge_output_is_an_error(answer):
    row = {"id": "g1", "split": "dev", "rule": "G4", "turns": []}
    with pytest.raises(ValueError, match="yes or no"):
        evaluate("guardrail", [row], judge=lambda messages: answer)


@pytest.mark.parametrize("answer", ["not json", "{}", '[{"subject":"user"}]'])
def test_invalid_extraction_is_an_error(answer):
    row = {"id": "m1", "split": "dev", "sessions": [[]], "gold_facts": [], "questions": []}
    with pytest.raises(ValueError):
        evaluate("memory", [row], extractor=lambda messages: answer)


def test_cli_reports_output_errors(tmp_path):
    blocked = tmp_path / "file"
    blocked.write_text("existing")
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "evals.run",
            "persona",
            "--split",
            "dev",
            "--out",
            str(blocked / "out"),
        ],
        cwd=ROOT.parent,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 2
    assert "eval error:" in result.stderr
    assert blocked.read_text() == "existing"


def test_cli_accepts_extraction_prompt_file(tmp_path):
    prompt = tmp_path / "prompt.txt"
    prompt.write_text("Custom extraction instructions")
    output = tmp_path / "result.json"
    subprocess.run(
        [
            sys.executable,
            "-m",
            "evals.run",
            "memory",
            "--split",
            "dev",
            "--out",
            str(output),
            "--extraction-prompt",
            str(prompt),
        ],
        cwd=ROOT.parent,
        check=True,
        capture_output=True,
    )
    assert json.loads(output.read_text())["set"] == "memory"
