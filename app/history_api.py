from __future__ import annotations

import json
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException

from .db import get_db
from .auth import require_api_key

router = APIRouter()


@router.get("/memories/{memory_id}/history", dependencies=[Depends(require_api_key)])
def memory_history(memory_id: int, limit: int = 50) -> dict[str, Any]:
    if limit < 1 or limit > 500:
        raise HTTPException(status_code=400, detail="limit must be 1..500")

    with get_db() as db:
        rows = db.execute(
            """
            SELECT id, created_at, prev_json, new_json, reason
            FROM memories_history
            WHERE memory_id = ?
            ORDER BY id DESC
            LIMIT ?
            """,
            (memory_id, limit),
        ).fetchall()

    hist = []
    for r in rows:
        hist.append(
            {
                "id": r["id"],
                "created_at": r["created_at"],
                "reason": r["reason"],
                "prev": json.loads(r["prev_json"]) if r["prev_json"] else None,
                "new": json.loads(r["new_json"]) if r["new_json"] else None,
            }
        )

    return {"memory_id": memory_id, "count": len(hist), "history": hist}
