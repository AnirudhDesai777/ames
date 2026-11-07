"""Checks eval set schemas and counts against docs/design.md section 10.

Run from the repo root: python evals/check.py
Exits non-zero on a schema error. A set that is short of its target count is
reported as pending, so partly authored sets can be checked before the freeze.
"""
import json
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
    "memory": ("memory/memory.jsonl", {"id", "sessions", "gold_facts", "questions"}, {
        "conversations": 5, "questions": 40,
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
        if name == "memory":
            for s in r["sessions"]:
                check_turns(s, where)
            for f in r["gold_facts"]:
                assert set(f) == {"subject", "predicate", "object"}, f"{where}: fact {f}"
            for q in r["questions"]:
                assert set(q) == {"q", "answer"}, f"{where}: question {q}"
            c["conversations"] += 1
            c["questions"] += len(r["questions"])
            continue
        if name == "latency":
            assert r["wav"].endswith(".wav"), f"{where}: wav path"
            assert 2 <= len(r["transcript"].split()) <= 25, f"{where}: transcript length"
            c["turns"] += 1
            continue
        assert r["split"] in SPLITS, f"{where}: split {r['split']!r}"
        check_turns(r["turns"], where)
        if name == "persona":
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
