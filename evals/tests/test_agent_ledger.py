import re
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2] / ".agent"
DECISIONS = yaml.safe_load((ROOT / "learning/decisions.yml").read_text())
CANDIDATES = yaml.safe_load((ROOT / "learning/candidates.yml").read_text())
RULES = yaml.safe_load((ROOT / "rules.yml").read_text())

DECISION_FIELDS = {
    "id",
    "date",
    "status",
    "topic",
    "decision",
    "evidence",
    "source",
    "supersedes",
    "superseded_by",
    "originating_task",
}
CANDIDATE_FIELDS = {
    "id",
    "status",
    "proposal",
    "evidence",
    "reason",
    "affected_files",
    "originating_task",
    "confidence",
    "conflicts",
    "explicit_user_feedback",
}
RULE_FIELDS = {
    "id",
    "title",
    "strength",
    "rule",
    "rationale",
    "source",
    "enforcement",
    "documentation",
}


def by_id(items, pattern):
    ids = [item["id"] for item in items]
    assert len(ids) == len(set(ids))
    assert all(re.fullmatch(pattern, i) for i in ids), ids
    return {item["id"]: item for item in items}


decisions = by_id(DECISIONS["decisions"], r"DECISION-\d{3}")
candidates = by_id(CANDIDATES["candidates"], r"CANDIDATE-\d{3}")
rules = by_id(RULES["rules"], r"(PRODUCT|MODEL|EVAL|PROCESS|SAFETY)-\d{3}")


def test_schema_versions():
    assert DECISIONS["schema_version"] == CANDIDATES["schema_version"] == 1
    assert RULES["schema_version"] == 1


def test_decisions_are_well_formed():
    for d in decisions.values():
        assert DECISION_FIELDS <= d.keys(), d["id"]
        assert re.fullmatch(r"\d{4}-\d{2}-\d{2}", d["date"]), d["id"]
        assert d["status"] in {"APPROVED", "SUPERSEDED"}
        assert d["source"] == "USER_DECISION"
        assert d["evidence"], d["id"]
        assert (d["status"] == "SUPERSEDED") == (d["superseded_by"] is not None), d["id"]


def test_decisions_numbered_in_date_order():
    dates = [decisions[i]["date"] for i in sorted(decisions)]
    assert dates == sorted(dates)


def test_supersession_links_are_symmetric():
    for d in decisions.values():
        for old in d["supersedes"]:
            assert decisions[old]["superseded_by"] == d["id"], (d["id"], old)
        if d["superseded_by"]:
            assert d["id"] in decisions[d["superseded_by"]]["supersedes"], d["id"]


def test_candidates_are_well_formed():
    for c in candidates.values():
        assert CANDIDATE_FIELDS <= c.keys(), c["id"]
        assert c["status"] in {
            "NEEDS_DECISION",
            "ADOPT",
            "ADAPT",
            "REJECT",
            "NOT_APPLICABLE",
        }
        assert c["confidence"] in {"HIGH", "MEDIUM", "LOW"}
        assert c["evidence"], c["id"]
        assert isinstance(c["explicit_user_feedback"], bool)
        assert all(ref in decisions for ref in c["conflicts"]), c["id"]
        if c.get("decision_id") is not None:
            assert c["decision_id"] in decisions, c["id"]


def test_rules_reference_approved_decisions():
    for r in rules.values():
        assert RULE_FIELDS <= r.keys(), r["id"]
        assert r["strength"] in {"MUST", "SHOULD"}
        assert r["enforcement"]["status"] in {"automated", "partial", "review-only"}
        assert r["source"]["type"] == "USER_DECISION"
        assert r["source"]["references"], r["id"]
        for ref in r["source"]["references"]:
            path, _, decision_id = ref.partition("#")
            assert path == ".agent/learning/decisions.yml", ref
            assert decisions[decision_id]["status"] == "APPROVED", ref
