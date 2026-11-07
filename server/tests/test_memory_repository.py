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


def test_composed_writes_roll_back_together(repository):
    with pytest.raises(RuntimeError, match="stop batch"), repository.connection:
        topic = repository.add_entity_in_transaction("Topic", "SQLite")
        repository.add_alias_in_transaction("storage", topic)
        repository.add_relation_in_transaction(1, "studies", object_id=topic)
        turn = repository.add_turn_in_transaction("session", "user", "I study SQLite.")
        repository.mark_extracted_in_transaction(turn)
        raise RuntimeError("stop batch")

    for table in ("turn", "relation", "alias"):
        assert repository.connection.execute(f"SELECT * FROM {table}").fetchall() == []
    assert [row[0] for row in repository.connection.execute("SELECT id FROM entity")] == [1]
    assert repository.delete_epoch() == 0


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


@pytest.mark.parametrize("shared", [False, True])
def test_replacement_sweeps_old_object_and_aliases_unless_still_related(repository, shared):
    old = repository.add_entity("Place", "old home")
    new = repository.add_entity("Place", "new home")
    repository.add_alias("previous home", old)
    repository.add_alias("current home", new)
    repository.add_relation(1, "lives_in", object_id=old)
    if shared:
        repository.add_relation(1, "related_to", object_id=old)

    repository.add_relation(1, "lives_in", object_id=new)

    entities = {row[0] for row in repository.connection.execute("SELECT id FROM entity")}
    aliases = {tuple(row) for row in repository.connection.execute("SELECT * FROM alias")}
    assert entities == ({1, old, new} if shared else {1, new})
    assert aliases == (
        {("previous home", old), ("current home", new)} if shared else {("current home", new)}
    )
    assert repository.delete_epoch() == 0


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


def test_delete_cascades_to_orphan_entities_and_aliases_but_keeps_neighbours(repository):
    friend = repository.add_entity("Person", "friend")
    topic = repository.add_entity("Topic", "SQL")
    repository.add_alias("friend", friend)
    repository.add_alias("databases", topic)
    repository.add_alias("me", 1)
    knows = repository.add_relation(1, "knows", object_id=friend)
    studies = repository.add_relation(friend, "studies", object_id=topic)

    assert repository.delete_relation(knows)
    assert repository.delete_epoch() == 1
    assert [row["id"] for row in repository.list_relations()] == [studies]
    assert repository.connection.execute("SELECT count(*) FROM alias").fetchone()[0] == 3
    assert repository.delete_relation(studies)
    assert repository.delete_epoch() == 2
    assert repository.list_relations() == []
    assert [tuple(row) for row in repository.connection.execute("SELECT id, type FROM entity")] == [
        (1, "User")
    ]
    assert [tuple(row) for row in repository.connection.execute("SELECT * FROM alias")] == [
        ("me", 1)
    ]


def test_delete_removes_orphan_subject_and_preserves_a_shared_object(repository):
    event = repository.add_entity("Event", "exam")
    place = repository.add_entity("Place", "library")
    repository.add_alias("the exam", event)
    relation = repository.add_relation(event, "related_to", object_id=place)
    repository.add_relation(1, "lives_in", object_id=place)
    repository.delete_relation(relation)
    assert (
        repository.connection.execute("SELECT id FROM entity WHERE id = ?", (event,)).fetchone()
        is None
    )
    assert repository.connection.execute("SELECT * FROM alias").fetchall() == []
    assert repository.connection.execute("SELECT id FROM entity WHERE id = ?", (place,)).fetchone()


def test_foreign_keys_cascade_relations_in_both_directions(repository):
    event = repository.add_entity("Event", "exam")
    repository.add_alias("the exam", event)
    repository.add_relation(1, "related_to", object_id=event)
    repository.add_relation(event, "scheduled_on", value="2026-11-10")
    with repository.connection:
        repository.connection.execute("DELETE FROM entity WHERE id = ?", (event,))
    assert repository.list_relations() == []
    assert repository.connection.execute("SELECT * FROM alias").fetchall() == []


def test_delete_everything_keeps_only_user_and_metadata(repository):
    topic = repository.add_entity("Topic", "SQL")
    repository.add_alias("databases", topic)
    repository.add_alias("me", 1)
    repository.add_relation(1, "studies", object_id=topic)
    repository.add_turn("session", "user", "I study SQL.")
    repository.add_turn("session", "ames", "How is that going?")
    repository.delete_everything()
    for table in ("turn", "relation", "alias"):
        assert repository.connection.execute(f"SELECT count(*) FROM {table}").fetchone()[0] == 0
    assert [tuple(row) for row in repository.connection.execute("SELECT * FROM entity")] == [
        (1, "User", "User", "user")
    ]
    assert repository.connection.execute("SELECT version FROM schema_version").fetchone()[0] == 1
    assert repository.delete_epoch() == 1
    assert repository.add_entity("User") == 1
    repository.add_relation(1, "likes", value="tea")
    assert len(repository.list_relations()) == 1


def test_every_delete_advances_epoch_even_if_already_absent(repository, tmp_path):
    assert not repository.delete_relation(999)
    repository.delete_everything()
    repository.delete_everything()
    assert repository.delete_epoch() == 3
    second = Repository(tmp_path / "data")
    try:
        assert second.delete_epoch() == 3
        second.delete_relation(999)
        assert repository.delete_epoch() == 4
    finally:
        second.close()


@pytest.mark.parametrize("delete_all", [False, True])
@pytest.mark.parametrize("failure", ["DELETE ON alias", "UPDATE ON delete_epoch"])
def test_failed_delete_rolls_back_fact_turn_entity_alias_and_epoch(repository, delete_all, failure):
    topic = repository.add_entity("Topic", "SQL")
    repository.add_alias("databases", topic)
    fact = repository.add_relation(1, "studies", object_id=topic)
    repository.add_turn("session", "user", "I study SQL.")
    tables = ("turn", "relation", "entity", "alias", "delete_epoch")
    before = {
        table: repository.connection.execute(f"SELECT * FROM {table} ORDER BY rowid").fetchall()
        for table in tables
    }
    repository.connection.execute(
        f"CREATE TRIGGER reject_delete BEFORE {failure} "
        "BEGIN SELECT RAISE(ABORT, 'failed delete'); END"
    )
    with pytest.raises(sqlite3.IntegrityError, match="failed delete"):
        if delete_all:
            repository.delete_everything()
        else:
            repository.delete_relation(fact)
    after = {
        table: repository.connection.execute(f"SELECT * FROM {table} ORDER BY rowid").fetchall()
        for table in tables
    }
    assert after == before


def test_turns_keep_spoken_text_and_pending_users_are_oldest_first(repository):
    newer = repository.add_turn(
        "new", "user", "  spoken text  ", created_at="2026-11-07T00:00:00+00:00"
    )
    older = repository.add_turn(
        "old", "user", "earlier", crisis=True, created_at="2026-11-06T00:00:00+00:00"
    )
    repository.add_turn("new", "ames", "context only")
    turns = repository.pending_turns()
    assert [row["id"] for row in turns] == [older, newer]
    assert turns[0]["crisis"] == 1
    assert turns[1]["text"] == "  spoken text  "
    repository.mark_extracted(older)
    assert [row["id"] for row in repository.pending_turns()] == [newer]


@pytest.mark.parametrize(
    "kwargs", [{"session_id": ""}, {"text": " "}, {"crisis": "yes"}, {"crisis": 2}]
)
def test_invalid_turns_are_not_stored(repository, kwargs):
    turn = {"session_id": "session", "role": "user", "text": "hello"} | kwargs
    with pytest.raises(ValueError):
        repository.add_turn(**turn)
    assert repository.pending_turns() == []


def test_retention_needs_both_extraction_and_session_end(repository):
    active = repository.add_turn("active", "user", "current session")
    active_reply = repository.add_turn("active", "ames", "current context")
    ended = repository.add_turn("ended", "user", "previous session")
    reply = repository.add_turn("ended", "ames", "context")
    unprocessed = repository.add_turn("ended", "user", "still awaiting extraction")
    repository.mark_extracted(active)
    repository.mark_extracted(ended)
    assert repository.prune_turns("active") == 1
    rows = repository.connection.execute("SELECT id FROM turn ORDER BY id").fetchall()
    assert [row[0] for row in rows] == [active, active_reply, reply, unprocessed]

    assert repository.prune_turns(None) == 2
    rows = repository.connection.execute("SELECT id FROM turn ORDER BY id").fetchall()
    assert [row[0] for row in rows] == [reply, unprocessed]
    assert [row["id"] for row in repository.pending_turns()] == [unprocessed]
    repository.mark_extracted(unprocessed)
    assert repository.prune_turns(None) == 2
    assert repository.connection.execute("SELECT * FROM turn").fetchall() == []
    assert repository.delete_epoch() == 0


def test_retention_after_restart_and_repeated_marking(tmp_path):
    first = Repository(tmp_path)
    processed = first.add_turn("ended", "user", "extracted before shutdown")
    pending = first.add_turn("ended", "user", "not extracted before shutdown")
    first.mark_extracted(processed)
    stamp = first.connection.execute(
        "SELECT extracted_at FROM turn WHERE id = ?", (processed,)
    ).fetchone()[0]
    first.mark_extracted(processed)
    assert (
        first.connection.execute(
            "SELECT extracted_at FROM turn WHERE id = ?", (processed,)
        ).fetchone()[0]
        == stamp
    )
    first.close()
    second = Repository(tmp_path)
    try:
        assert second.prune_turns(None) == 1
        assert [row["id"] for row in second.pending_turns()] == [pending]
        second.mark_extracted(pending)
        assert second.prune_turns(None) == 1
    finally:
        second.close()
