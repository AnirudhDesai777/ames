import json

import pytest

from evals import check


@pytest.fixture
def memory_rows(monkeypatch):
    file, keys, targets = check.TARGETS["memory"]
    rows = [json.loads(line) for line in (check.ROOT / file).read_text().splitlines()]
    monkeypatch.setattr(check, "TARGETS", {"memory": (file, keys, targets)})
    return rows


def test_memory_split_counts(memory_rows, capsys):
    assert [row["id"] for row in memory_rows if row["split"] == "dev"] == [
        "memory-001",
        "memory-002",
        "memory-003",
    ]
    assert [row["id"] for row in memory_rows if row["split"] == "heldout"] == [
        "memory-004",
        "memory-005",
        "memory-006",
        "memory-007",
        "memory-008",
        "memory-009",
    ]
    heldout = [row for row in memory_rows if row["split"] == "heldout"]
    assert sum(len(row["gold_facts"]) for row in heldout) == 36
    assert sum(len(row["questions"]) for row in heldout) == 48
    check.main()
    assert "memory: ok" in capsys.readouterr().out


@pytest.mark.parametrize("split", [None, "unsplit"])
def test_memory_split_is_required_and_valid(memory_rows, split, tmp_path, monkeypatch):
    if split is None:
        del memory_rows[0]["split"]
    else:
        memory_rows[0]["split"] = split
    path = tmp_path / "memory" / "memory.jsonl"
    path.parent.mkdir()
    path.write_text("\n".join(json.dumps(row) for row in memory_rows))
    monkeypatch.setattr(check, "ROOT", tmp_path)
    with pytest.raises(AssertionError, match="keys" if split is None else "split"):
        check.main()


@pytest.mark.parametrize("split", ["dev", "heldout"])
def test_memory_rejects_over_target_split(memory_rows, split, tmp_path, monkeypatch):
    memory_rows[0 if split == "heldout" else -1]["split"] = split
    path = tmp_path / "memory" / "memory.jsonl"
    path.parent.mkdir()
    path.write_text("\n".join(json.dumps(row) for row in memory_rows))
    monkeypatch.setattr(check, "ROOT", tmp_path)
    with pytest.raises(AssertionError, match=f"over target for \\['{split}'\\]"):
        check.main()
