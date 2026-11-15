"""Checks eval set schemas and counts against docs/design.md section 10.

Run from the repo root: python evals/check.py
Exits non-zero on a schema error. A set that is short of its target count is
reported as pending, so partly authored sets can be checked before the freeze.
"""
import json
import re
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).parent
SPLITS = {"dev", "heldout"}
ROLES = {"user", "ames"}

# set -> (file, required keys, {count key: target})
TARGETS = {
    "persona": ("persona/persona.jsonl", {"id", "split", "category", "turns"}, {
        "dev": 60, "heldout": 30,
        "edgy/dev": 10, "edgy/heldout": 10,
    }),
    "crisis": ("crisis/crisis.jsonl", {"id", "split", "label", "turns"}, {
        "1/dev": 10, "1/heldout": 10, "0/dev": 20, "0/heldout": 20,
    }),
    "guardrail": ("guardrail/guardrail.jsonl", {"id", "split", "rule", "turns"}, {
        f"{r}/{s}": 3 for r in ("G2", "G3", "G4") for s in ("dev", "heldout")
    }),
    "memory": ("memory/memory.jsonl", {"id", "split", "sessions", "gold_facts", "questions"}, {
        "conversations": 9, "questions": 72, "dev": 3, "heldout": 6,
    }),
    "tools": ("tools/tools.jsonl", {
        "id", "split", "category", "turns", "expected", "crisis", "reason", "mock_result",
    }, {
        f"{category}/{split}": (8 if category == "no_tool" else 4) // divisor
        for split, divisor in (("dev", 1), ("heldout", 2))
        for category in ("recall_memory", "remember", "web_search", "get_time",
                         "no_tool", "ambiguous", "crisis")
    }),
    "latency": ("latency/manifest.jsonl", {"id", "wav", "transcript"}, {"turns": 30}),
}


def check_turns(turns, where):
    assert isinstance(turns, list) and turns, f"{where}: turns must be a non-empty list"
    for t in turns:
        assert set(t) == {"role", "text"}, f"{where}: turn keys {sorted(t)}"
        assert t["role"] in ROLES, f"{where}: role {t['role']!r}"
        assert isinstance(t["text"], str) and t["text"].strip(), f"{where}: empty text"
    assert turns[-1]["role"] == "user", f"{where}: last turn must be the user's"


def count(name, rows):
    c = Counter()
    for r in rows:
        where = f"{name}:{r.get('id')}"
        if name != "latency":
            assert r["split"] in SPLITS, f"{where}: split {r['split']!r}"
        if name == "memory":
            for s in r["sessions"]:
                check_turns(s, where)
            for f in r["gold_facts"]:
                assert set(f) == {"subject", "predicate", "object"}, f"{where}: fact {f}"
            for q in r["questions"]:
                assert set(q) == {"q", "answer"}, f"{where}: question {q}"
            c["conversations"] += 1
            c["questions"] += len(r["questions"])
            c[r["split"]] += 1
            continue
        if name == "latency":
            assert r["wav"].endswith(".wav"), f"{where}: wav path"
            assert 2 <= len(r["transcript"].split()) <= 25, f"{where}: transcript length"
            c["turns"] += 1
            continue
        check_turns(r["turns"], where)
        if name == "tools":
            check_tool_case(r, where)
            c[f"{r['category']}/{r['split']}"] += 1
        elif name == "persona":
            assert r["category"] in {"thought", "companion", "edgy"}, f"{where}: category"
            c[r["split"]] += 1
            if r["category"] == "edgy":
                c[f"edgy/{r['split']}"] += 1
        elif name == "crisis":
            assert r["label"] in (0, 1), f"{where}: label"
            c[f"{r['label']}/{r['split']}"] += 1
        elif name == "guardrail":
            assert r["rule"] in {"G2", "G3", "G4"}, f"{where}: rule"
            c[f"{r['rule']}/{r['split']}"] += 1
    return c


def check_tool_case(row, where):
    tools = {"recall_memory", "remember", "web_search", "get_time"}
    category = row["category"]
    assert category in tools | {"no_tool", "ambiguous", "crisis"}, f"{where}: category"
    assert type(row["crisis"]) is bool, f"{where}: crisis boolean"
    assert row["crisis"] == (category == "crisis"), f"{where}: crisis label"
    assert isinstance(row["reason"], str) and row["reason"].strip(), f"{where}: reason"
    assert len(row["turns"]) >= 3, f"{where}: multi-turn required"
    assert all(t["role"] == ("user" if i % 2 == 0 else "ames")
               for i, t in enumerate(row["turns"])), f"{where}: alternating turns"
    expected = row["expected"]
    if category not in tools:
        assert expected is None and row["mock_result"] is None, f"{where}: no-call labels"
        return
    assert isinstance(expected, dict), f"{where}: expected call"
    assert set(expected) == {"name", "arguments"}, f"{where}: expected keys"
    assert expected["name"] == category, f"{where}: expected tool"
    rules = expected["arguments"]
    assert isinstance(rules, dict) and set(rules) == {"required", "forbidden"}, f"{where}: rules"
    for patterns in rules.values():
        assert isinstance(patterns, list), f"{where}: pattern list"
        for pattern in patterns:
            assert isinstance(pattern, str) and pattern.strip(), f"{where}: empty pattern"
            re.compile(pattern)
    assert bool(rules["required"]) == (category != "get_time"), f"{where}: required concepts"
    assert category != "get_time" or not rules["forbidden"], f"{where}: no time arguments"
    assert isinstance(row["mock_result"], dict) and row["mock_result"], f"{where}: mock result"


def main():
    pending = 0
    for name, (file, keys, targets) in TARGETS.items():
        path = ROOT / file
        rows = [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []
        ids = [r.get("id") for r in rows]
        assert len(ids) == len(set(ids)), f"{name}: duplicate ids"
        for r in rows:
            assert set(r) == keys, f"{name}:{r.get('id')}: keys {sorted(r)}"
        c = count(name, rows)
        short = {k: f"{c[k]}/{v}" for k, v in targets.items() if c[k] != v}
        over = [k for k, v in targets.items() if c[k] > v]
        assert not over, f"{name}: over target for {over}"
        pending += bool(short)
        print(f"{name}: {'ok' if not short else 'pending ' + ', '.join(f'{k} {v}' for k, v in short.items())}")
    print(f"schemas ok, {pending} set(s) pending")


if __name__ == "__main__":
    main()
