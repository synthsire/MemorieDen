import os
import sqlite3
from contextlib import contextmanager
from typing import Iterator

DB_PATH = os.environ.get("MEMORIEDEN_DB", "/data/memorieden.sqlite3")


def connect() -> sqlite3.Connection:
    # check_same_thread=False allows FastAPI (threadpool) to share connections if needed.
    # We still open a new connection per request via get_db().
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA synchronous=NORMAL;")
    conn.execute("PRAGMA foreign_keys=ON;")
    return conn


def init_db() -> None:
    """Initialize DB and run schema migrations.

    Uses PRAGMA user_version for a lightweight, auditable migration system.
    """
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    conn = connect()
    try:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS schema_migrations (
              version INTEGER PRIMARY KEY,
              applied_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
            );
            """
        )

        current = conn.execute("PRAGMA user_version;").fetchone()[0]

        def apply(version: int, sql: str) -> None:
            conn.executescript(sql)
            conn.execute("INSERT OR IGNORE INTO schema_migrations(version) VALUES (?)", (version,))
            conn.execute(f"PRAGMA user_version={version};")

        # v1: base schema
        if current < 1:
            apply(
                1,
                """
                CREATE TABLE IF NOT EXISTS users (
                  user_id TEXT PRIMARY KEY,
                  created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
                  metadata_json TEXT
                );

                CREATE TABLE IF NOT EXISTS memories (
                  id INTEGER PRIMARY KEY AUTOINCREMENT,
                  created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
                  user_id TEXT,
                  source TEXT,
                  title TEXT,
                  content TEXT NOT NULL,
                  tags_json TEXT,
                  metadata_json TEXT
                );

                -- Append-only change history (auditable)
                CREATE TABLE IF NOT EXISTS memories_history (
                  id INTEGER PRIMARY KEY AUTOINCREMENT,
                  memory_id INTEGER NOT NULL,
                  created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
                  prev_json TEXT,
                  new_json TEXT,
                  reason TEXT,
                  FOREIGN KEY(memory_id) REFERENCES memories(id) ON DELETE CASCADE
                );

                CREATE INDEX IF NOT EXISTS idx_memories_history_memory_id ON memories_history(memory_id);

                -- FTS5 index of content + title (external content, kept in sync by triggers)
                CREATE VIRTUAL TABLE IF NOT EXISTS memories_fts USING fts5(
                  title,
                  content,
                  content='memories',
                  content_rowid='id',
                  tokenize='unicode61'
                );

                CREATE TRIGGER IF NOT EXISTS memories_ai AFTER INSERT ON memories BEGIN
                  INSERT INTO memories_fts(rowid, title, content)
                  VALUES (new.id, coalesce(new.title,''), new.content);
                END;

                CREATE TRIGGER IF NOT EXISTS memories_ad AFTER DELETE ON memories BEGIN
                  INSERT INTO memories_fts(memories_fts, rowid, title, content)
                  VALUES('delete', old.id, coalesce(old.title,''), old.content);
                END;

                CREATE TRIGGER IF NOT EXISTS memories_au AFTER UPDATE ON memories BEGIN
                  INSERT INTO memories_fts(memories_fts, rowid, title, content)
                  VALUES('delete', old.id, coalesce(old.title,''), old.content);
                  INSERT INTO memories_fts(rowid, title, content)
                  VALUES (new.id, coalesce(new.title,''), new.content);
                END;
                """,
            )

        # v2: idempotency support
        if current < 2:
            apply(
                2,
                """
                ALTER TABLE memories ADD COLUMN idempotency_key TEXT;
                CREATE UNIQUE INDEX IF NOT EXISTS idx_memories_idempotency_key ON memories(idempotency_key);
                """,
            )

        # v3: memory decay support
        if current < 3:
            apply(
                3,
                """
                ALTER TABLE memories ADD COLUMN last_accessed_at TEXT;
                ALTER TABLE memories ADD COLUMN access_count INTEGER DEFAULT 0;
                ALTER TABLE memories ADD COLUMN cached_strength REAL DEFAULT 1.0;
                ALTER TABLE memories ADD COLUMN strength_updated_at TEXT;
                CREATE INDEX IF NOT EXISTS idx_memories_cached_strength ON memories(cached_strength);
                """,
            )

        # Future migrations go here (v4, v5, ...).

        conn.commit()
    finally:
        conn.close()


@contextmanager
def get_db() -> Iterator[sqlite3.Connection]:
    conn = connect()
    try:
        yield conn
    finally:
        conn.close()
