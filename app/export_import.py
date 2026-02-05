from __future__ import annotations

import json
from typing import Any, Iterable, Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse

from .auth import require_api_key
from .db import get_db

router = APIRouter()


def _row_to_obj(row) -> dict[str, Any]:
    return {
        "id": row["id"],
        "created_at": row["created_at"],
        "user_id": row["user_id"],
        "source": row["source"],
        "title": row["title"],
        "content": row["content"],
        "tags": json.loads(row["tags_json"]) if row["tags_json"] else None,
        "metadata": json.loads(row["metadata_json"]) if row["metadata_json"] else None,
    }


@router.get("/export.ndjson", dependencies=[Depends(require_api_key)])
def export_ndjson(user_id: Optional[str] = None, limit: int = 100000) -> StreamingResponse:
    """Export memories as NDJSON (one JSON object per line).

    Lightweight + auditable. Intended for backups, diffing, and migration.
    """
    if limit < 1 or limit > 500000:
        raise HTTPException(status_code=400, detail="limit must be 1..500000")

    with get_db() as db:
        sql = "SELECT id, created_at, user_id, source, title, content, tags_json, metadata_json FROM memories"
        params: list[Any] = []
        if user_id:
            sql += " WHERE user_id = ?"
            params.append(user_id)
        sql += " ORDER BY id ASC LIMIT ?"
        params.append(limit)
        rows = db.execute(sql, params).fetchall()

    def gen() -> Iterable[bytes]:
        for r in rows:
            yield (json.dumps(_row_to_obj(r), ensure_ascii=False) + "\n").encode("utf-8")

    return StreamingResponse(gen(), media_type="application/x-ndjson")


@router.post("/import.ndjson", dependencies=[Depends(require_api_key)])
async def import_ndjson(request: Request, user_id: Optional[str] = None, source: str = "import") -> dict[str, Any]:
    """Import NDJSON memories.

    Each line should be a JSON object with at least `content`. Other fields are optional.
    - `user_id` query param overrides per-line user_id if provided.
    - `source` query param sets source if per-line source is missing.

    Note: does not preserve IDs; inserts new rows.
    """

    inserted = 0
    bad = 0

    body = await request.body()
    text = body.decode("utf-8", errors="replace")
    lines = [ln for ln in text.splitlines() if ln.strip()]

    with get_db() as db:
        for ln in lines:
            try:
                obj = json.loads(ln)
                content = (obj.get("content") or "").strip()
                if not content:
                    bad += 1
                    continue

                u = user_id if user_id is not None else obj.get("user_id")
                s = obj.get("source") or source
                title = obj.get("title")
                tags = obj.get("tags")
                meta = obj.get("metadata")
                # stamp import provenance
                if isinstance(meta, dict):
                    meta = {**meta, "imported": True}
                else:
                    meta = {"imported": True}

                if u:
                    db.execute("INSERT OR IGNORE INTO users(user_id, metadata_json) VALUES (?, NULL)", (u,))

                db.execute(
                    """
                    INSERT INTO memories(user_id, source, title, content, tags_json, metadata_json)
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (
                        u,
                        s,
                        title,
                        content,
                        json.dumps(tags) if tags is not None else None,
                        json.dumps(meta) if meta is not None else None,
                    ),
                )
                inserted += 1
            except Exception:
                bad += 1

        db.commit()

    return {"inserted": inserted, "bad_lines": bad}
