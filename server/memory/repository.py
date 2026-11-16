"""SQLite storage for design sections 6.2 and 6.5; no model or pipeline imports."""

import os
import sqlite3
from datetime import UTC, date, datetime
from pathlib import Path

DATA_DIR = Path.home() / "Library/Application Support/Ames"
FUNCTIONAL = {"lives_in", "scheduled_on", "feels"}
OBJECT_TYPES = {
    "lives_in": {"Place"},
    "scheduled_on": set(),
    "feels": set(),
    "studies": {"Topic"},
    "wants": {"Goal"},
    "knows": {"Person"},
    "likes": {"Topic"},
    "dislikes": {"Topic"},
    "related_to": {"User", "Person", "Goal", "Topic", "Event", "Place"},
}
LITERAL_PREDICATES = {"scheduled_on", "feels", "likes", "dislikes"}

SCHEMA = """
BEGIN;
CREATE TABLE schema_version (version INTEGER NOT NULL);
INSERT INTO schema_version VALUES (2);
CREATE TABLE turn (
    id INTEGER PRIMARY KEY,
    session_id TEXT NOT NULL,
    role TEXT NOT NULL CHECK (role IN ('user', 'ames')),
    text TEXT NOT NULL,
    crisis INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    extracted_at TEXT
);
CREATE TABLE entity (
    id INTEGER PRIMARY KEY,
    type TEXT NOT NULL CHECK (type IN ('User', 'Person', 'Goal', 'Topic', 'Event', 'Place')),
    name TEXT NOT NULL,
    normalized_name TEXT NOT NULL,
    UNIQUE (type, normalized_name)
);
INSERT INTO entity VALUES (1, 'User', 'User', 'user');
CREATE TABLE alias (
    alias TEXT NOT NULL,
    entity_id INTEGER NOT NULL REFERENCES entity(id) ON DELETE CASCADE,
    PRIMARY KEY (alias, entity_id)
);
CREATE TABLE relation (
    id INTEGER PRIMARY KEY,
    subject_id INTEGER NOT NULL REFERENCES entity(id) ON DELETE CASCADE,
    predicate TEXT NOT NULL,
    object_id INTEGER REFERENCES entity(id) ON DELETE CASCADE,
    value TEXT,
    created_at TEXT NOT NULL,
    CHECK ((object_id IS NULL) <> (value IS NULL))
);
CREATE TABLE delete_epoch (n INTEGER NOT NULL);
INSERT INTO delete_epoch VALUES (0);
CREATE TABLE episode (id INTEGER PRIMARY KEY, text TEXT NOT NULL, created_at TEXT NOT NULL);
CREATE VIRTUAL TABLE episode_fts USING fts5(text, content='episode', content_rowid='id');
CREATE TRIGGER episode_insert AFTER INSERT ON episode BEGIN
    INSERT INTO episode_fts(rowid, text) VALUES (new.id, new.text);
END;
CREATE TRIGGER episode_delete AFTER DELETE ON episode BEGIN
    INSERT INTO episode_fts(episode_fts, rowid, text) VALUES ('delete', old.id, old.text);
END;
COMMIT;
"""


def now() -> str:
    return datetime.now(UTC).isoformat()


def nonempty(text: str) -> str:
    if not isinstance(text, str) or not text.strip():
        raise ValueError("Expected nonempty text")
    return text.strip()


class Repository:
    """Methods ending in ``_in_transaction`` leave commit and rollback to the caller."""

    def __init__(self, data_dir: Path = DATA_DIR):
        data_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
        data_dir.chmod(0o700)
        path = data_dir / "ames.db"
        descriptor = os.open(path, os.O_CREAT | os.O_RDWR, 0o600)
        try:
            os.fchmod(descriptor, 0o600)
        finally:
            os.close(descriptor)
        self.connection = sqlite3.connect(path)
        self.connection.row_factory = sqlite3.Row
        try:
            self.connection.execute("PRAGMA foreign_keys = ON")
            self.connection.execute("PRAGMA secure_delete = ON")
            if not self.connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            ).fetchone():
                self.connection.executescript(SCHEMA)
            else:
                versions = self.connection.execute("SELECT version FROM schema_version").fetchall()
                if [row[0] for row in versions] == [1]:
                    self.connection.executescript("""
                        BEGIN;
                        CREATE TABLE episode (
                            id INTEGER PRIMARY KEY, text TEXT NOT NULL, created_at TEXT NOT NULL
                        );
                        CREATE VIRTUAL TABLE episode_fts USING fts5(
                            text, content='episode', content_rowid='id'
                        );
                        CREATE TRIGGER episode_insert AFTER INSERT ON episode BEGIN
                            INSERT INTO episode_fts(rowid, text) VALUES (new.id, new.text);
                        END;
                        CREATE TRIGGER episode_delete AFTER DELETE ON episode BEGIN
                            INSERT INTO episode_fts(episode_fts, rowid, text)
                            VALUES ('delete', old.id, old.text);
                        END;
                        UPDATE schema_version SET version = 2;
                        COMMIT;
                        """)
                elif [row[0] for row in versions] != [2]:
                    raise ValueError("Unsupported memory schema version")
        except Exception:
            self.connection.close()
            raise

    def close(self) -> None:
        self.connection.close()

    def remember(self, fact: str) -> None:
        with self.connection:
            self.connection.execute(
                "INSERT INTO episode(text, created_at) VALUES (?, ?)", (nonempty(fact), now())
            )

    def recall(self, query: str) -> list[str]:
        # Treat query words as FTS literals, never as user-supplied FTS syntax.
        import re

        words = re.findall(r"\w+", nonempty(query), re.UNICODE)
        if not words:
            return []
        match = " OR ".join('"' + word.replace('"', '""') + '"' for word in words)
        return [
            row[0]
            for row in self.connection.execute(
                "SELECT episode.text FROM episode_fts "
                "JOIN episode ON episode.id = episode_fts.rowid "
                "WHERE episode_fts MATCH ? "
                "ORDER BY bm25(episode_fts), episode.created_at DESC LIMIT 3",
                (match,),
            )
        ]

    def add_entity(self, entity_type: str, name: str = "") -> int:
        with self.connection:
            return self.add_entity_in_transaction(entity_type, name)

    def add_entity_in_transaction(self, entity_type: str, name: str = "") -> int:
        if entity_type == "User":
            return 1
        name = nonempty(name)
        self.connection.execute(
            "INSERT INTO entity(type, name, normalized_name) VALUES (?, ?, ?) "
            "ON CONFLICT(type, normalized_name) DO NOTHING",
            (entity_type, name, name.lower()),
        )
        return self.connection.execute(
            "SELECT id FROM entity WHERE type = ? AND normalized_name = ?",
            (entity_type, name.lower()),
        ).fetchone()[0]

    def add_alias(self, alias: str, entity_id: int) -> None:
        with self.connection:
            self.add_alias_in_transaction(alias, entity_id)

    def add_alias_in_transaction(self, alias: str, entity_id: int) -> None:
        self.connection.execute(
            "INSERT INTO alias VALUES (?, ?) ON CONFLICT DO NOTHING",
            (nonempty(alias).lower(), entity_id),
        )

    def add_relation(
        self,
        subject_id: int,
        predicate: str,
        *,
        object_id: int | None = None,
        value: str | None = None,
        created_at: str | None = None,
    ) -> int:
        with self.connection:
            return self.add_relation_in_transaction(
                subject_id, predicate, object_id=object_id, value=value, created_at=created_at
            )

    def add_relation_in_transaction(
        self,
        subject_id: int,
        predicate: str,
        *,
        object_id: int | None = None,
        value: str | None = None,
        created_at: str | None = None,
    ) -> int:
        if predicate not in OBJECT_TYPES:
            raise ValueError("Unknown predicate")
        if (object_id is None) == (value is None):
            raise ValueError("Supply exactly one of object_id and value")
        if value is not None:
            value = nonempty(value)
            if predicate not in LITERAL_PREDICATES:
                raise ValueError("Predicate requires an entity")
            if predicate == "scheduled_on" and date.fromisoformat(value).isoformat() != value:
                raise ValueError("Expected an ISO date: YYYY-MM-DD")
        if object_id is not None:
            target = self.connection.execute(
                "SELECT type FROM entity WHERE id = ?", (object_id,)
            ).fetchone()
            if target is None or target[0] not in OBJECT_TYPES[predicate]:
                raise ValueError("Invalid object entity for predicate")
        replaced = 0
        if predicate in FUNCTIONAL:
            replaced = self.connection.execute(
                "DELETE FROM relation WHERE subject_id = ? AND predicate = ?",
                (subject_id, predicate),
            ).rowcount
        relation_id = self.connection.execute(
            "INSERT INTO relation(subject_id, predicate, object_id, value, created_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (subject_id, predicate, object_id, value, created_at or now()),
        ).lastrowid
        if replaced:
            self.sweep_orphans_in_transaction()
        return relation_id

    def list_relations(self) -> list[sqlite3.Row]:
        return self.connection.execute(
            "SELECT * FROM relation ORDER BY created_at DESC, id DESC"
        ).fetchall()

    def delete_epoch(self) -> int:
        return self.connection.execute("SELECT n FROM delete_epoch").fetchone()[0]

    def delete_relation(self, relation_id: int) -> bool:
        with self.connection:
            return self.delete_relation_in_transaction(relation_id)

    def delete_relation_in_transaction(self, relation_id: int) -> bool:
        deleted = self.connection.execute(
            "DELETE FROM relation WHERE id = ?", (relation_id,)
        ).rowcount
        self.sweep_orphans_in_transaction()
        self.connection.execute("UPDATE delete_epoch SET n = n + 1")
        return bool(deleted)

    def sweep_orphans_in_transaction(self) -> None:
        self.connection.execute(
            "DELETE FROM entity WHERE id <> 1 AND NOT EXISTS "
            "(SELECT 1 FROM relation WHERE subject_id = entity.id OR object_id = entity.id)"
        )

    def delete_everything(self) -> None:
        with self.connection:
            self.delete_everything_in_transaction()

    def delete_everything_in_transaction(self) -> None:
        self.connection.execute("DELETE FROM turn")
        self.connection.execute("DELETE FROM episode")
        self.connection.execute("DELETE FROM relation")
        self.connection.execute("DELETE FROM alias")
        self.connection.execute("DELETE FROM entity WHERE id <> 1")
        self.connection.execute("UPDATE delete_epoch SET n = n + 1")

    def add_turn(
        self,
        session_id: str,
        role: str,
        text: str,
        *,
        crisis: bool = False,
        created_at: str | None = None,
    ) -> int:
        with self.connection:
            return self.add_turn_in_transaction(
                session_id, role, text, crisis=crisis, created_at=created_at
            )

    def add_turn_in_transaction(
        self,
        session_id: str,
        role: str,
        text: str,
        *,
        crisis: bool = False,
        created_at: str | None = None,
    ) -> int:
        nonempty(session_id)
        nonempty(text)
        if crisis not in (False, True):
            raise ValueError("Crisis must be a boolean")
        return self.connection.execute(
            "INSERT INTO turn(session_id, role, text, crisis, created_at) VALUES (?, ?, ?, ?, ?)",
            (session_id, role, text, crisis, created_at or now()),
        ).lastrowid

    def pending_turns(self) -> list[sqlite3.Row]:
        return self.connection.execute(
            "SELECT * FROM turn WHERE role = 'user' AND extracted_at IS NULL "
            "ORDER BY created_at, id"
        ).fetchall()

    def mark_extracted(self, turn_id: int) -> None:
        with self.connection:
            self.mark_extracted_in_transaction(turn_id)

    def mark_extracted_in_transaction(self, turn_id: int) -> None:
        self.connection.execute(
            "UPDATE turn SET extracted_at = ? WHERE id = ? AND extracted_at IS NULL",
            (now(), turn_id),
        )

    def prune_turns(self, active_session_id: str | None) -> int:
        """Drop processed text outside the one active session (None means no session)."""
        with self.connection:
            return self.prune_turns_in_transaction(active_session_id)

    def prune_turns_in_transaction(self, active_session_id: str | None) -> int:
        return self.connection.execute(
            "DELETE FROM turn WHERE session_id IS NOT ? AND ("
            "(role = 'user' AND extracted_at IS NOT NULL) OR "
            "(role = 'ames' AND NOT EXISTS ("
            "SELECT 1 FROM turn AS pending WHERE pending.session_id = turn.session_id "
            "AND pending.role = 'user' AND pending.extracted_at IS NULL)))",
            (active_session_id,),
        ).rowcount
