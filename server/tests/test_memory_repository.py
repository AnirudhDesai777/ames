import sqlite3
import stat

import pytest

from server.memory.repository import Repository


@pytest.fixture
def repository(tmp_path):
    store = Repository(tmp_path / "data")
    yield store
    store.close()


def test_schema_is_private_persistent_and_reopenable(tmp_path):
    data_dir = tmp_path / "data"
    first = Repository(data_dir)
    topic = first.add_entity("Topic", "SQLite")
    first.add_alias("storage", topic)
    fact = first.add_relation(1, "studies", object_id=topic)
    first.close()
    (data_dir / "ames.db").chmod(0o644)
    data_dir.chmod(0o755)

    second = Repository(data_dir)
    try:
        versions = second.connection.execute("SELECT version FROM schema_version").fetchall()
        assert [row[0] for row in versions] == [1]
        assert second.connection.execute("PRAGMA foreign_keys").fetchone()[0] == 1
        assert second.connection.execute("PRAGMA secure_delete").fetchone()[0] == 1
        assert second.delete_epoch() == 0
        assert second.list_relations()[0]["id"] == fact
        assert second.add_entity("User") == 1
        users = second.connection.execute("SELECT * FROM entity WHERE type = 'User'").fetchall()
        assert len(users) == 1
        assert stat.S_IMODE(data_dir.stat().st_mode) == 0o700
        assert stat.S_IMODE((data_dir / "ames.db").stat().st_mode) == 0o600
    finally:
        second.close()


def test_unsupported_schema_is_not_overwritten(tmp_path):
    store = Repository(tmp_path)
    store.add_relation(1, "likes", value="tea")
    with store.connection:
        store.connection.execute("UPDATE schema_version SET version = 2")
    store.close()
    with pytest.raises(ValueError, match="schema version"):
        Repository(tmp_path)
    with sqlite3.connect(tmp_path / "ames.db") as connection:
        assert connection.execute("SELECT value FROM relation").fetchone()[0] == "tea"
        assert connection.execute("SELECT version FROM schema_version").fetchone()[0] == 2


def test_entities_and_aliases_are_normalized_without_merging_types(repository):
    topic = repository.add_entity("Topic", "  SQL  ")
    assert repository.add_entity("Topic", "sql") == topic
    assert repository.add_entity("Goal", "SQL") != topic
    repository.add_alias("  DATABASES ", topic)
    repository.add_alias("databases", topic)
    other = repository.add_entity("Topic", "SQLite")
    repository.add_alias("databases", other)
    aliases = repository.connection.execute("SELECT * FROM alias ORDER BY entity_id").fetchall()
    assert [tuple(row) for row in aliases] == [("databases", topic), ("databases", other)]


@pytest.mark.parametrize("text", ["", "  ", None, 42])
def test_empty_names_aliases_and_values_are_rejected(repository, text):
    with pytest.raises(ValueError):
        repository.add_entity("Topic", text)
    with pytest.raises(ValueError):
        repository.add_alias(text, 1)
    with pytest.raises(ValueError):
        repository.add_relation(1, "likes", value=text)


@pytest.mark.parametrize(
    "statement,parameters",
    [
        ("INSERT INTO entity(type, name, normalized_name) VALUES (?, ?, ?)", ("Other", "x", "x")),
        ("INSERT INTO alias VALUES (?, ?)", ("missing", 999)),
        (
            "INSERT INTO relation(subject_id, predicate, value, created_at) VALUES (?, ?, ?, ?)",
            (999, "likes", "tea", "2026-11-07T00:00:00+00:00"),
        ),
        (
            "INSERT INTO relation(subject_id, predicate, object_id, value, created_at) "
            "VALUES (1, 'likes', ?, ?, '2026-11-07T00:00:00+00:00')",
            (1, "tea"),
        ),
        (
            "INSERT INTO relation(subject_id, predicate, created_at) "
            "VALUES (1, 'likes', '2026-11-07T00:00:00+00:00')",
            (),
        ),
        (
            "INSERT INTO turn(session_id, role, text, created_at) "
            "VALUES ('a', 'other', 'x', 'now')",
            (),
        ),
    ],
)
def test_schema_constraints(repository, statement, parameters):
    with pytest.raises(sqlite3.IntegrityError), repository.connection:
        repository.connection.execute(statement, parameters)


@pytest.mark.parametrize(
    "predicate,entity_type,value",
    [
        ("lives_in", "Place", None),
        ("scheduled_on", None, "2026-11-10"),
        ("feels", None, "hopeful"),
        ("studies", "Topic", None),
        ("wants", "Goal", None),
        ("knows", "Person", None),
        ("likes", "Topic", None),
        ("likes", None, "coffee"),
        ("dislikes", "Topic", None),
        ("dislikes", None, "noise"),
        ("related_to", "Event", None),
    ],
)
def test_predicate_targets(repository, predicate, entity_type, value):
    object_id = repository.add_entity(entity_type, "example") if entity_type else None
    fact = repository.add_relation(1, predicate, object_id=object_id, value=value)
    row = repository.list_relations()[0]
    assert (row["id"], row["predicate"], row["object_id"], row["value"]) == (
        fact,
        predicate,
        object_id,
        value,
    )


@pytest.mark.parametrize(
    "predicate,target",
    [
        ("unknown", {"value": "tea"}),
        ("likes", {}),
        ("likes", {"object_id": 1, "value": "tea"}),
        ("likes", {"object_id": 999}),
        ("lives_in", {"value": "home"}),
        ("lives_in", {"object_id": 1}),
        ("scheduled_on", {"object_id": 1}),
        ("scheduled_on", {"value": "tomorrow"}),
        ("scheduled_on", {"value": "2026-02-30"}),
        ("scheduled_on", {"value": "20261110"}),
        ("feels", {"object_id": 1}),
        ("related_to", {"value": "tea"}),
    ],
)
def test_invalid_relation_is_not_stored(repository, predicate, target):
    with pytest.raises(ValueError):
        repository.add_relation(1, predicate, **target)
    assert repository.list_relations() == []


@pytest.mark.parametrize("predicate", ["feels", "scheduled_on", "lives_in"])
def test_functional_relations_replace_only_the_same_subject_and_predicate(repository, predicate):
    subject = repository.add_entity("Person", "friend")
    if predicate == "lives_in":
        old = {"object_id": repository.add_entity("Place", "old home")}
        new = {"object_id": repository.add_entity("Place", "new home")}
    else:
        old = {"value": "calm" if predicate == "feels" else "2026-11-10"}
        new = {"value": "happy" if predicate == "feels" else "2026-11-11"}
    repository.add_relation(1, predicate, **old)
    repository.add_relation(subject, predicate, **old)
    repository.add_relation(1, "likes", value="coffee")
    repository.add_relation(1, predicate, **new)
    rows = repository.list_relations()
    assert len(rows) == 3
    fact = next(row for row in rows if row["subject_id"] == 1 and row["predicate"] == predicate)
    assert all(fact[key] == value for key, value in new.items())


def test_failed_replacement_rolls_back_the_old_fact(repository):
    repository.add_relation(1, "feels", value="calm")
    repository.connection.execute(
        "CREATE TRIGGER reject_relation BEFORE INSERT ON relation "
        "BEGIN SELECT RAISE(ABORT, 'failed write'); END"
    )
    with pytest.raises(sqlite3.IntegrityError, match="failed write"):
        repository.add_relation(1, "feels", value="happy")
    assert [row["value"] for row in repository.list_relations()] == ["calm"]


def test_multi_relations_coexist_and_list_newest_first(repository):
    repository.add_relation(1, "likes", value="tea", created_at="2026-11-06T00:00:00+00:00")
    repository.add_relation(1, "likes", value="coffee", created_at="2026-11-07T00:00:00+00:00")
    repository.add_relation(1, "likes", value="water", created_at="2026-11-07T00:00:00+00:00")
    assert [row["value"] for row in repository.list_relations()] == ["water", "coffee", "tea"]


def test_sql_text_is_bound_as_data(repository):
    text = "tea'); DELETE FROM entity; --"
    entity = repository.add_entity("Topic", text)
    repository.add_alias(text, entity)
    repository.add_relation(1, "likes", value=text)
    assert repository.list_relations()[0]["value"] == text
    assert repository.add_entity("Topic", text) == entity
