import json
import subprocess
import sys

import pytest

from evals import run
from evals.run import CLI_SETS, ROOT, evaluate, system_prompt
from evals.scoring import CRITERIA


def persona_row(**changes):
    return {
        "id": "p1",
        "split": "dev",
        "category": "edgy",
        "turns": [{"role": "user", "text": "Let's talk about a difficult topic."}],
    } | changes


@pytest.mark.parametrize("name", CLI_SETS)
@pytest.mark.parametrize("split", ["dev", "heldout"])
@pytest.mark.parametrize("repeats", [1, 3])
def test_cli_runs_current_partial_data_with_stubs(name, split, repeats, tmp_path):
    output = tmp_path / name / f"{split}.json"
    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "evals.run",
            name,
            "--split",
            split,
            "--repeats",
            str(repeats),
            "--out",
            str(output),
        ],
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
    assert result["repeats"] == repeats
    assert len(result["runs"]) == repeats
    assert completed.stdout.count("mean (") == 1
    for index, single in enumerate(result["runs"], start=1):
        assert f"run {index}/{repeats} counts=" in completed.stdout
        assert single["cases"]
        assert single["metrics"] == result["metrics"]
    single = result["runs"][0]
    if repeats == 1:
        assert result["counts"] == single["counts"]
        assert result["cases"] == single["cases"]
    assert result["metrics"]["malformed"] == 0
    for metric in result["metrics"]:
        assert f"{metric}:" in completed.stdout
    assert completed.stdout.rstrip().endswith(result["status"])
    if name == "persona":
        assert result["metrics"]["refusals"] == 0
        assert result["status"] == "PASS"
    elif name == "guardrail":
        # The stub judge finds no prohibited content and confirms she said she is an AI.
        assert result["status"] == "PASS"
    elif name == "crisis":
        assert result["metrics"]["recall"] == 0
        assert result["status"] == "FAIL"
    elif name == "tools":
        assert single["counts"]["conversations"] == (32 if split == "dev" else 16)
        assert result["metrics"]["call_accuracy"] == 0.5
        assert result["metrics"]["tool_accuracy"] == 0
        assert result["metrics"]["malformed_call_rate"] is None
        assert result["status"] == "MEASURED"
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
        assert [case["id"] for case in single["cases"]] == ids
        assert single["counts"]["conversations"] == len(ids)
        assert single["counts"]["questions"] == (24 if split == "dev" else 48)


@pytest.mark.parametrize(
    "extra_facts, expected_status",
    [([0, 0, 1], "PASS"), ([1, 1, 0], "FAIL")],
)
def test_cli_repeats_three_times_and_gates_on_mean(
    extra_facts, expected_status, tmp_path, monkeypatch, capsys
):
    facts = [{"subject": "user", "predicate": "likes", "object": str(i)} for i in range(3)]
    row = {
        "id": "m1",
        "split": "heldout",
        "sessions": [[{"role": "user", "text": "I like three things."}]],
        "gold_facts": facts,
        "questions": [{"q": "What do I like?", "answer": "Three things"}],
    }
    dataset = tmp_path / "memory" / "memory.jsonl"
    dataset.parent.mkdir()
    dataset.write_text(json.dumps(row))
    monkeypatch.setattr(run, "ROOT", tmp_path)
    output = tmp_path / "result.json"
    monkeypatch.setattr(
        sys,
        "argv",
        ["evals.run", "memory", "--split", "heldout", "--repeats", "3", "--out", str(output)],
    )
    calls = []
    extra_counts = iter(extra_facts)

    def evaluate_sample(name, rows, **kwargs):
        calls.append((name, rows, kwargs))
        extras = facts[: next(extra_counts)]
        extracted = facts + [fact | {"object": "wrong"} for fact in extras]
        return evaluate(name, rows, **kwargs, extractor=lambda messages: json.dumps(extracted))

    monkeypatch.setattr(run, "evaluate", evaluate_sample)
    run.main()
    result = json.loads(output.read_text())
    assert len(calls) == 3
    assert all(name == "memory" and rows == [row] for name, rows, kwargs in calls)
    assert all(kwargs["split"] == "heldout" for name, rows, kwargs in calls)
    assert len(result["runs"]) == 3
    precisions = [3 / (3 + extra) for extra in extra_facts]
    assert [single["metrics"]["precision"] for single in result["runs"]] == precisions
    assert result["metrics"]["precision"] == pytest.approx(sum(precisions) / 3)
    assert result["gates"]["precision"]["passed"] == (expected_status == "PASS")
    assert result["status"] == expected_status
    assert result["runs"][-1]["status"] != expected_status
    shown = capsys.readouterr().out
    assert shown.count("precision:") == 4
    for index, precision in enumerate(precisions, start=1):
        assert f"run {index}/3 counts=" in shown
        assert f"precision: {precision}" in shown
    assert f"mean (3 runs)\nprecision: {result['metrics']['precision']}" in shown
    assert shown.rstrip().endswith(expected_status)


@pytest.mark.parametrize("repeats", ["0", "-1", "1.5"])
def test_cli_rejects_invalid_repeats(repeats, tmp_path):
    output = tmp_path / "result.json"
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "evals.run",
            "memory",
            "--split",
            "heldout",
            "--repeats",
            repeats,
            "--out",
            str(output),
        ],
        cwd=ROOT.parent,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 2
    assert "--repeats" in result.stderr
    assert not output.exists()


def test_prompt_is_verbatim_and_turns_use_model_roles():
    seen = []

    def model(messages):
        seen.extend(messages)
        return "<state crisis=0><sigh>Let's talk."

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
        [persona_row()],
        model=lambda messages: "<state crisis=0><laugh>No thanks.",
        judge=judge,
    )
    assert result["metrics"]["pass_rate"] == 0
    assert result["metrics"]["refusals"] == 1
    assert result["status"] == "FAIL"
    assert all(json.loads(messages[1]["content"])["reply"] == "No thanks." for messages in seen)
    assert all(
        "Do not generate examples or any prohibited content" in m[0]["content"] for m in seen
    )


@pytest.mark.parametrize(
    "reply",
    ["<state mood=soft crisis=1>Stop.", "<state crisis=2>Stop.", "No token."],
)
def test_malformed_or_missing_control_token_is_crisis_zero(reply):
    rows = [{"id": "c1", "split": "dev", "label": 1, "turns": []}]
    result = evaluate("crisis", rows, model=lambda messages: reply)
    assert result["cases"][0]["crisis"] == 0
    assert result["metrics"]["malformed"] == 1
    assert result["metrics"]["recall"] == 0
    assert not result["gates"]["malformed"]["passed"]


def test_crisis_counts_flags_on_both_labels():
    rows = [{"id": str(label), "split": "dev", "label": label, "turns": []} for label in (0, 1)]
    result = evaluate("crisis", rows, model=lambda messages: "<state crisis=1>Stop.")
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
        return "<state crisis=0>Tea."

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


@pytest.mark.parametrize("name", CLI_SETS)
def test_empty_set_does_not_pass(name):
    assert evaluate(name, [])["status"] == "FAIL"


@pytest.mark.parametrize("answer", ["maybe", "", "yes, here are examples"])
def test_invalid_judge_output_is_recorded_per_case(answer):
    row = {"id": "g1", "split": "dev", "rule": "G4", "turns": []}
    result = evaluate("guardrail", [row, row | {"id": "g2"}], judge=lambda messages: answer)
    assert len(result["cases"]) == 2
    assert result["metrics"]["G4"] == 0
    assert all(case["judge_errors"][0]["reply"] == answer for case in result["cases"])


@pytest.mark.parametrize("tag", ["", "json"])
def test_memory_normalises_fenced_extractor_reply(tag):
    fact = {"subject": "user", "predicate": "likes", "object": "tea"}
    row = {
        "id": "m1",
        "split": "dev",
        "sessions": [[]],
        "gold_facts": [fact],
        "questions": [],
    }
    result = evaluate(
        "memory", [row], extractor=lambda messages: f"```{tag}\n{json.dumps([fact])}\n```"
    )
    case = result["cases"][0]
    assert case["extracted"] == case["correct_facts"] == 1
    assert case["extraction_errors"] == []


@pytest.mark.parametrize(
    "answer", ["not json", "```json\nnot json\n```", "{}", '[{"subject":"user"}]']
)
def test_invalid_extraction_fails_only_its_case(answer):
    fact = {"subject": "user", "predicate": "likes", "object": "tea"}
    row = {
        "id": "m1",
        "split": "dev",
        "sessions": [[], [], []],
        "gold_facts": [fact],
        "questions": [{"q": "What do I like?", "answer": "Tea"}],
    }
    replies = iter([json.dumps([fact]), answer, json.dumps([fact])])
    seen = []

    def model(messages):
        seen.append(messages[1]["content"])
        return "<state crisis=0>Tea."

    result = evaluate(
        "memory",
        [row, row | {"id": "m2", "sessions": [[]]}],
        model=model,
        judge=lambda messages: "yes",
        extractor=lambda messages: next(replies),
    )
    failed, following = result["cases"]
    assert failed["extracted"] == failed["correct_facts"] == 0
    assert failed["extraction_errors"][0]["session"] == 1
    assert failed["extraction_errors"][0]["reply"] == answer
    assert failed["extraction_errors"][0]["error"]
    assert seen[0] == "Remembered facts: []"
    assert following["extracted"] == following["correct_facts"] == 1
    assert following["extraction_errors"] == []
    assert result["metrics"]["recall"] == 0.5
    assert result["status"] == "FAIL"


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


@pytest.mark.parametrize("reply", ["Yes.", "```json\nyes\n```", " NO! "])
def test_judge_normalises_short_answers(reply):
    assert run.judge_yes(lambda messages: reply, "Check", {}) == ("yes" in reply.casefold())


def test_persona_fenced_scores_and_malformed_token():
    result = evaluate(
        "persona",
        [persona_row(category="thought")],
        model=lambda messages: "Missing control token.",
        judge=lambda messages: "```json\n" + json.dumps(dict.fromkeys(CRITERIA, 4)) + "\n```",
    )
    assert result["metrics"]["pass_rate"] == 1
    assert result["metrics"]["malformed"] == 1


@pytest.mark.parametrize("reply", ["not JSON", "{}", "[]", '{"in_character": 9}'])
def test_bad_persona_judgment_does_not_abort(reply):
    result = evaluate("persona", [persona_row()], judge=lambda messages: reply)
    case = result["cases"][0]
    assert not case["passed"]
    assert case["scores"] is None
    assert case["refusal"]
    assert len(case["judge_errors"]) == 2


def test_bad_memory_judgment_is_attached_to_case():
    row = {
        "id": "m1",
        "split": "dev",
        "sessions": [],
        "gold_facts": [],
        "questions": [{"q": "Name?", "answer": "Maya"}],
    }
    result = evaluate("memory", [row], judge=lambda messages: "unsure")
    assert result["cases"][0]["judge_errors"][0]["reply"] == "unsure"
    assert result["metrics"]["answer_accuracy"] == 0
