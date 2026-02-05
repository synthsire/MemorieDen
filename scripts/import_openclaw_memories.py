#!/usr/bin/env python3
"""Import OpenClaw memory markdown files into MemorieDen sqlite DB.

Reads:
- <workspace>/MEMORY.md (optional)
- <workspace>/memory/*.md (optional)

Usage:
  python scripts/import_openclaw_memories.py \
    --db ./data/memorieden.sqlite3 \
    --workspace /home/radxa/.openclaw/workspace \
    --user-id default

This script is designed to run either on host or inside the container.
"""

from __future__ import annotations

import argparse
import glob
import os
import sqlite3
from datetime import datetime


def init_db(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
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
        """
    )


def insert_memory(
    conn: sqlite3.Connection,
    *,
    user_id: str | None,
    source: str,
    title: str | None,
    content: str,
    tags: list[str] | None = None,
    metadata: dict | None = None,
) -> int:
    import json

    cur = conn.execute(
        "INSERT INTO memories(user_id, source, title, content, tags_json, metadata_json) VALUES (?,?,?,?,?,?)",
        (
            user_id,
            source,
            title,
            content,
            json.dumps(tags) if tags is not None else None,
            json.dumps(metadata) if metadata is not None else None,
        ),
    )
    return int(cur.lastrowid)


def read_text(path: str) -> str:
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=os.environ.get("MEMORIEDEN_DB", "./data/memorieden.sqlite3"))
    ap.add_argument("--workspace", default="/home/radxa/.openclaw/workspace")
    ap.add_argument("--user-id", default=None)
    args = ap.parse_args()

    os.makedirs(os.path.dirname(os.path.abspath(args.db)), exist_ok=True)

    conn = sqlite3.connect(args.db)
    # WAL is great for concurrency, but can fail on some mounts/permissions.
    # We'll try it and fall back to DELETE journal mode.
    try:
        conn.execute("PRAGMA journal_mode=WAL;")
    except sqlite3.OperationalError:
        conn.execute("PRAGMA journal_mode=DELETE;")
    conn.execute("PRAGMA synchronous=NORMAL;")
    conn.execute("PRAGMA foreign_keys=ON;")

    init_db(conn)

    imported = 0

    # Basic dedupe: avoid importing the same source+title twice.
    conn.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS memories_source_title_uq ON memories(source, title)"
    )

    memory_md = os.path.join(args.workspace, "MEMORY.md")
    if os.path.exists(memory_md):
        content = read_text(memory_md)
        try:
            insert_memory(
                conn,
                user_id=args.user_id,
                source="openclaw:MEMORY.md",
                title="OpenClaw MEMORY.md",
                content=content,
                tags=["openclaw", "memory", "longterm"],
                metadata={"path": "MEMORY.md"},
            )
            imported += 1
        except sqlite3.IntegrityError:
            pass

    daily_glob = os.path.join(args.workspace, "memory", "*.md")
    for path in sorted(glob.glob(daily_glob)):
        base = os.path.basename(path)
        title = f"OpenClaw daily memory {base}"
        content = read_text(path)
        tags = ["openclaw", "memory", "daily"]
        # If filename looks like YYYY-MM-DD.md, add it as a tag.
        if len(base) >= 10 and base[4] == '-' and base[7] == '-':
            tags.append(base[:10])
        try:
            insert_memory(
                conn,
                user_id=args.user_id,
                source=f"openclaw:memory/{base}",
                title=title,
                content=content,
                tags=tags,
                metadata={"path": f"memory/{base}"},
            )
            imported += 1
        except sqlite3.IntegrityError:
            pass

    conn.commit()
    conn.close()

    print(f"Imported {imported} documents at {datetime.utcnow().isoformat()}Z into {args.db}")


if __name__ == "__main__":
    main()
