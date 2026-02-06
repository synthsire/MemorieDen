from __future__ import annotations

import json
from typing import Any, Optional

from fastapi import Depends, FastAPI, HTTPException, Header
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from .db import get_db, init_db
from .history_api import router as history_router
from .export_import import router as export_import_router
from . import strength as strength_module

app = FastAPI(title="MemorieDen (minimal)", version="0.6.0")
app.include_router(history_router)
app.include_router(export_import_router)

# Mount static files for web UI
import os
static_path = os.path.join(os.path.dirname(__file__), "..", "static")
if os.path.exists(static_path):
    app.mount("/static", StaticFiles(directory=static_path), name="static")


from .auth import require_api_key


@app.on_event("startup")
def _startup() -> None:
    init_db()


@app.get("/")
def root():
    """Serve the web UI."""
    static_path = os.path.join(os.path.dirname(__file__), "..", "static", "index.html")
    if os.path.exists(static_path):
        return FileResponse(static_path)
    return {"message": "MemorieDen API", "docs": "/docs", "health": "/health"}


class MemoryAdd(BaseModel):
    content: str = Field(..., min_length=1)
    title: Optional[str] = None
    user_id: Optional[str] = None
    source: Optional[str] = None
    tags: Optional[list[str]] = None
    metadata: Optional[dict[str, Any]] = None
    idempotency_key: Optional[str] = None  # can also be provided via header


class MemoryBulkDoc(BaseModel):
    # Used for importing documents (e.g., MEMORY.md).
    content: Optional[str] = None
    title: Optional[str] = None
    user_id: Optional[str] = None
    source: Optional[str] = None
    tags: Optional[list[str]] = None
    metadata: Optional[dict[str, Any]] = None


@app.get("/health")
def health() -> dict[str, Any]:
    with get_db() as db:
        v = db.execute("select sqlite_version() as v").fetchone()["v"]
        mem_count = db.execute("select count(*) as c from memories").fetchone()["c"]
        user_count = db.execute("select count(*) as c from users").fetchone()["c"]
    return {"ok": True, "sqlite_version": v, "memories": mem_count, "users": user_count}


class UserAdd(BaseModel):
    user_id: str = Field(..., min_length=1, max_length=200)
    metadata: Optional[dict[str, Any]] = None


@app.post("/users/add", dependencies=[Depends(require_api_key)])
def add_user(payload: UserAdd) -> dict[str, Any]:
    import json

    meta_json = json.dumps(payload.metadata) if payload.metadata is not None else None
    with get_db() as db:
        # idempotent insert
        db.execute(
            "INSERT OR IGNORE INTO users(user_id, metadata_json) VALUES (?, ?)",
            (payload.user_id, meta_json),
        )
        db.commit()
        row = db.execute(
            "SELECT user_id, created_at, metadata_json FROM users WHERE user_id=?",
            (payload.user_id,),
        ).fetchone()
    return {
        "user": {
            "user_id": row["user_id"],
            "created_at": row["created_at"],
            "metadata": json.loads(row["metadata_json"]) if row["metadata_json"] else None,
        }
    }


@app.get("/users/all", dependencies=[Depends(require_api_key)])
def users_all(limit: int = 200) -> dict[str, Any]:
    import json

    if limit < 1 or limit > 2000:
        raise HTTPException(status_code=400, detail="limit must be 1..2000")

    with get_db() as db:
        rows = db.execute(
            "SELECT user_id, created_at, metadata_json FROM users ORDER BY created_at DESC LIMIT ?",
            (limit,),
        ).fetchall()

    users = [
        {
            "user_id": r["user_id"],
            "created_at": r["created_at"],
            "metadata": json.loads(r["metadata_json"]) if r["metadata_json"] else None,
        }
        for r in rows
    ]

    return {"count": len(users), "users": users}


@app.get("/users/search", dependencies=[Depends(require_api_key)])
def users_search(q: str, limit: int = 50) -> dict[str, Any]:
    import json

    if not q:
        raise HTTPException(status_code=400, detail="q is required")
    if limit < 1 or limit > 200:
        raise HTTPException(status_code=400, detail="limit must be 1..200")

    with get_db() as db:
        rows = db.execute(
            "SELECT user_id, created_at, metadata_json FROM users WHERE user_id LIKE ? ORDER BY created_at DESC LIMIT ?",
            (f"%{q}%", limit),
        ).fetchall()

    users = [
        {
            "user_id": r["user_id"],
            "created_at": r["created_at"],
            "metadata": json.loads(r["metadata_json"]) if r["metadata_json"] else None,
        }
        for r in rows
    ]

    return {"count": len(users), "users": users}


@app.post("/memories/add", dependencies=[Depends(require_api_key)])
def add_memory(payload: MemoryAdd, idempotency_key: str | None = Header(default=None, alias="Idempotency-Key")) -> dict[str, Any]:
    """Add a memory.

    Idempotency:
    - If Idempotency-Key header (or payload.idempotency_key) is provided and already exists,
      return the existing memory instead of inserting a duplicate.
    """
    key = payload.idempotency_key or idempotency_key

    tags_json = json.dumps(payload.tags) if payload.tags is not None else None
    metadata_json = json.dumps(payload.metadata) if payload.metadata is not None else None

    with get_db() as db:
        if payload.user_id:
            db.execute("INSERT OR IGNORE INTO users(user_id, metadata_json) VALUES (?, NULL)", (payload.user_id,))

        if key:
            existing = db.execute(
                "SELECT id, created_at, user_id, source, title, content, tags_json, metadata_json FROM memories WHERE idempotency_key=?",
                (key,),
            ).fetchone()
            if existing:
                return {"memory": _row_to_dict(existing), "idempotent": True}

        try:
            cur = db.execute(
                """
                INSERT INTO memories(user_id, source, title, content, tags_json, metadata_json, idempotency_key)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    payload.user_id,
                    payload.source,
                    payload.title,
                    payload.content,
                    tags_json,
                    metadata_json,
                    key,
                ),
            )
            db.commit()
            memory_id = cur.lastrowid
        except Exception as e:
            import sqlite3

            if isinstance(e, sqlite3.IntegrityError):
                # If the idempotency key collided, return the existing record.
                if key and "idempotency" in str(e).lower():
                    existing = db.execute(
                        "SELECT id, created_at, user_id, source, title, content, tags_json, metadata_json FROM memories WHERE idempotency_key=?",
                        (key,),
                    ).fetchone()
                    if existing:
                        return {"memory": _row_to_dict(existing), "idempotent": True}
                raise HTTPException(status_code=409, detail=str(e))
            raise

        row = db.execute(
            "SELECT id, created_at, user_id, source, title, content, tags_json, metadata_json FROM memories WHERE id=?",
            (memory_id,),
        ).fetchone()

    return {"memory": _row_to_dict(row), "idempotent": False}


@app.post("/memories/add_document", dependencies=[Depends(require_api_key)])
def add_document(payload: MemoryBulkDoc) -> dict[str, Any]:
    if not payload.content or not payload.content.strip():
        raise HTTPException(status_code=400, detail="content is required")

    add_payload = MemoryAdd(
        content=payload.content,
        title=payload.title,
        user_id=payload.user_id,
        source=payload.source,
        tags=payload.tags,
        metadata=payload.metadata,
    )
    return add_memory(add_payload)


class SearchRequest(BaseModel):
    query: str = Field(..., min_length=1)
    limit: int = Field(20, ge=1, le=200)
    user_id: Optional[str] = None
    track_access: bool = Field(False, description="If true, increment access count for returned memories")


class MemoryUpdate(BaseModel):
    content: Optional[str] = None
    title: Optional[str] = None
    user_id: Optional[str] = None
    source: Optional[str] = None
    tags: Optional[list[str]] = None
    metadata: Optional[dict[str, Any]] = None
    reason: Optional[str] = None  # optional audit note


class BulkImportRequest(BaseModel):
    documents: list[MemoryAdd] = Field(..., min_length=1)


@app.post("/memories/search", dependencies=[Depends(require_api_key)])
def search_memories(payload: SearchRequest) -> dict[str, Any]:
    # Import config here to avoid circular dependency
    from . import config
    
    # FTS5 query syntax: https://sqlite.org/fts5.html
    # We accept the query as-is (user can use quotes/NEAR/etc).
    q = payload.query.strip()

    with get_db() as db:
        sql = (
            """
            SELECT m.id, m.created_at, m.user_id, m.source, m.title, m.content, m.tags_json, m.metadata_json,
                   m.last_accessed_at, m.access_count, m.cached_strength, m.strength_updated_at,
                   bm25(memories_fts) AS bm25_score,
                   snippet(memories_fts, 1, '[', ']', '…', 12) AS snippet
            FROM memories_fts
            JOIN memories m ON m.id = memories_fts.rowid
            WHERE memories_fts MATCH ?
            """
        )
        params: list[Any] = [q]
        if payload.user_id:
            sql += " AND (m.user_id = ?)"
            params.append(payload.user_id)
        
        # Fetch results (no limit yet - we need all for normalization)
        rows = db.execute(sql, params).fetchall()
        
        if not rows:
            return {"query": payload.query, "count": 0, "memories": []}
        
        # Normalize BM25 scores and combine with strength
        # BM25 scores are negative, where more negative = better match
        bm25_scores = [r["bm25_score"] for r in rows]
        min_bm25 = min(bm25_scores)
        max_bm25 = max(bm25_scores)
        bm25_range = max_bm25 - min_bm25 if max_bm25 != min_bm25 else 1.0
        
        # Calculate combined scores
        alpha = config.MEMORY_STRENGTH_BM25_WEIGHT
        beta = config.MEMORY_STRENGTH_DECAY_WEIGHT
        
        results = []
        for r in rows:
            # Normalize BM25 to 0-1 range, then invert so higher = better
            normalized_bm25 = (r["bm25_score"] - min_bm25) / bm25_range
            bm25_component = 1.0 - normalized_bm25  # Invert: higher = better match
            
            # Get cached strength, refresh if stale
            cached_strength = r["cached_strength"] or 1.0
            if strength_module.should_refresh_cached_strength(r["strength_updated_at"]):
                cached_strength = strength_module.calculate_strength(
                    created_at=r["created_at"],
                    last_accessed_at=r["last_accessed_at"],
                    access_count=r["access_count"] or 0,
                )
                # Update cache
                db.execute(
                    """
                    UPDATE memories
                    SET cached_strength = ?,
                        strength_updated_at = strftime('%Y-%m-%dT%H:%M:%fZ','now')
                    WHERE id = ?
                    """,
                    (cached_strength, r["id"]),
                )
            
            # Combined score: higher = better
            final_score = (alpha * bm25_component) + (beta * cached_strength)
            
            results.append({
                "row": r,
                "bm25_score": r["bm25_score"],
                "strength": cached_strength,
                "final_score": final_score,
            })
        
        # Commit any cache updates
        db.commit()
        
        # Sort by final score (descending - higher is better)
        results.sort(key=lambda x: x["final_score"], reverse=True)
        
        # Apply limit
        results = results[:payload.limit]
        
        # Track access if requested
        if payload.track_access:
            for result in results:
                r = result["row"]
                new_access_count = (r["access_count"] or 0) + 1
                new_strength = strength_module.calculate_strength(
                    created_at=r["created_at"],
                    last_accessed_at=None,  # Will use current time
                    access_count=new_access_count,
                )
                db.execute(
                    """
                    UPDATE memories
                    SET last_accessed_at = strftime('%Y-%m-%dT%H:%M:%fZ','now'),
                        access_count = ?,
                        cached_strength = ?,
                        strength_updated_at = strftime('%Y-%m-%dT%H:%M:%fZ','now')
                    WHERE id = ?
                    """,
                    (new_access_count, new_strength, r["id"]),
                )
            db.commit()
    
    # Format response
    memories = []
    for result in results:
        r = result["row"]
        mem_dict = _row_to_dict(r)
        mem_dict["score"] = result["bm25_score"]
        mem_dict["snippet"] = r["snippet"]
        mem_dict["final_score"] = result["final_score"]
        mem_dict["strength"] = result["strength"]
        memories.append(mem_dict)

    return {
        "query": payload.query,
        "count": len(memories),
        "memories": memories,
    }


@app.post("/memories/bulk", dependencies=[Depends(require_api_key)])
def bulk_import(payload: BulkImportRequest) -> dict[str, Any]:
    # True bulk ingestion: one request, many docs. Keeps it simple (single transaction).
    docs = payload.documents
    if len(docs) > 500:
        raise HTTPException(status_code=400, detail="too many documents (max 500)")

    inserted: list[dict[str, Any]] = []
    with get_db() as db:
        for d in docs:
            tags_json = json.dumps(d.tags) if d.tags is not None else None
            metadata_json = json.dumps(d.metadata) if d.metadata is not None else None
            cur = db.execute(
                """
                INSERT INTO memories(user_id, source, title, content, tags_json, metadata_json)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (d.user_id, d.source, d.title, d.content, tags_json, metadata_json),
            )
            inserted.append({"id": cur.lastrowid})
        db.commit()

    return {"inserted": len(inserted), "ids": [x["id"] for x in inserted]}


@app.get("/memories/all", dependencies=[Depends(require_api_key)])
def all_memories(limit: int = 200, user_id: Optional[str] = None) -> dict[str, Any]:
    if limit < 1 or limit > 2000:
        raise HTTPException(status_code=400, detail="limit must be 1..2000")

    with get_db() as db:
        sql = "SELECT id, created_at, user_id, source, title, content, tags_json, metadata_json, " \
              "last_accessed_at, access_count, cached_strength, strength_updated_at FROM memories"
        params: list[Any] = []
        if user_id:
            sql += " WHERE user_id = ?"
            params.append(user_id)
        sql += " ORDER BY id DESC LIMIT ?"
        params.append(limit)
        rows = db.execute(sql, params).fetchall()

    return {"count": len(rows), "memories": [_row_to_dict(r) for r in rows]}


@app.get("/memories/{memory_id}", dependencies=[Depends(require_api_key)])
def get_memory(memory_id: int) -> dict[str, Any]:
    with get_db() as db:
        row = db.execute(
            "SELECT id, created_at, user_id, source, title, content, tags_json, metadata_json, "
            "last_accessed_at, access_count, cached_strength, strength_updated_at FROM memories WHERE id=?",
            (memory_id,),
        ).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="not found")
        
        # Track access: increment count, update timestamps, refresh strength
        new_access_count = (row["access_count"] or 0) + 1
        new_strength = strength_module.calculate_strength(
            created_at=row["created_at"],
            last_accessed_at=None,  # Will use current time (just accessed now)
            access_count=new_access_count,
        )
        
        db.execute(
            """
            UPDATE memories
            SET last_accessed_at = strftime('%Y-%m-%dT%H:%M:%fZ','now'),
                access_count = ?,
                cached_strength = ?,
                strength_updated_at = strftime('%Y-%m-%dT%H:%M:%fZ','now')
            WHERE id = ?
            """,
            (new_access_count, new_strength, memory_id),
        )
        db.commit()
        
        # Fetch updated row
        updated_row = db.execute(
            "SELECT id, created_at, user_id, source, title, content, tags_json, metadata_json, "
            "last_accessed_at, access_count, cached_strength, strength_updated_at FROM memories WHERE id=?",
            (memory_id,),
        ).fetchone()
    
    return {"memory": _row_to_dict(updated_row)}


@app.put("/memories/{memory_id}", dependencies=[Depends(require_api_key)])
def update_memory(memory_id: int, payload: MemoryUpdate) -> dict[str, Any]:
    if all(
        v is None
        for v in [
            payload.content,
            payload.title,
            payload.user_id,
            payload.source,
            payload.tags,
            payload.metadata,
        ]
    ):
        raise HTTPException(status_code=400, detail="no fields to update")

    with get_db() as db:
        row = db.execute(
            "SELECT id, created_at, user_id, source, title, content, tags_json, metadata_json FROM memories WHERE id=?",
            (memory_id,),
        ).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="not found")

        current = _row_to_dict(row)
        new_tags = payload.tags if payload.tags is not None else current.get("tags")
        new_meta = payload.metadata if payload.metadata is not None else current.get("metadata")

        updated_payload = {
            "user_id": payload.user_id if payload.user_id is not None else current.get("user_id"),
            "source": payload.source if payload.source is not None else current.get("source"),
            "title": payload.title if payload.title is not None else current.get("title"),
            "content": payload.content if payload.content is not None else current.get("content"),
            "tags": new_tags,
            "metadata": new_meta,
        }

        # Write history entry (append-only)
        db.execute(
            """
            INSERT INTO memories_history(memory_id, prev_json, new_json, reason)
            VALUES (?, ?, ?, ?)
            """,
            (
                memory_id,
                json.dumps(current),
                json.dumps({**current, **updated_payload}),
                payload.reason,
            ),
        )

        db.execute(
            """
            UPDATE memories
            SET user_id = ?, source = ?, title = ?, content = ?, tags_json = ?, metadata_json = ?
            WHERE id = ?
            """,
            (
                updated_payload["user_id"],
                updated_payload["source"],
                updated_payload["title"],
                updated_payload["content"],
                json.dumps(updated_payload["tags"]) if updated_payload["tags"] is not None else None,
                json.dumps(updated_payload["metadata"]) if updated_payload["metadata"] is not None else None,
                memory_id,
            ),
        )
        db.commit()
        updated = db.execute(
            "SELECT id, created_at, user_id, source, title, content, tags_json, metadata_json FROM memories WHERE id=?",
            (memory_id,),
        ).fetchone()

    return {"memory": _row_to_dict(updated)}


@app.post("/memories/{memory_id}/access", dependencies=[Depends(require_api_key)])
def record_access(memory_id: int) -> dict[str, Any]:
    """Explicitly record an access to a memory (for manual control)."""
    with get_db() as db:
        row = db.execute(
            "SELECT id, created_at, user_id, source, title, content, tags_json, metadata_json, "
            "last_accessed_at, access_count, cached_strength, strength_updated_at FROM memories WHERE id=?",
            (memory_id,),
        ).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="not found")
        
        # Increment access count and refresh strength
        new_access_count = (row["access_count"] or 0) + 1
        new_strength = strength_module.calculate_strength(
            created_at=row["created_at"],
            last_accessed_at=None,  # Current time
            access_count=new_access_count,
        )
        
        db.execute(
            """
            UPDATE memories
            SET last_accessed_at = strftime('%Y-%m-%dT%H:%M:%fZ','now'),
                access_count = ?,
                cached_strength = ?,
                strength_updated_at = strftime('%Y-%m-%dT%H:%M:%fZ','now')
            WHERE id = ?
            """,
            (new_access_count, new_strength, memory_id),
        )
        db.commit()
    
    return {
        "success": True,
        "memory_id": memory_id,
        "access_count": new_access_count,
        "strength": new_strength,
    }


@app.get("/memories/{memory_id}/strength", dependencies=[Depends(require_api_key)])
def get_memory_strength(memory_id: int) -> dict[str, Any]:
    """Debug endpoint to view strength calculation details."""
    with get_db() as db:
        row = db.execute(
            "SELECT id, created_at, last_accessed_at, access_count, cached_strength, strength_updated_at "
            "FROM memories WHERE id=?",
            (memory_id,),
        ).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="not found")
    
    details = strength_module.get_strength_details(
        created_at=row["created_at"],
        last_accessed_at=row["last_accessed_at"],
        access_count=row["access_count"] or 0,
    )
    
    # Add cached values for comparison
    details["cached"] = {
        "cached_strength": row["cached_strength"],
        "strength_updated_at": row["strength_updated_at"],
        "is_stale": strength_module.should_refresh_cached_strength(row["strength_updated_at"]),
    }
    
    return details


@app.delete("/memories/{memory_id}", dependencies=[Depends(require_api_key)])
def delete_memory(memory_id: int) -> dict[str, Any]:
    with get_db() as db:
        cur = db.execute("DELETE FROM memories WHERE id=?", (memory_id,))
        db.commit()
    if cur.rowcount == 0:
        raise HTTPException(status_code=404, detail="not found")
    return {"deleted": True, "id": memory_id}


@app.post("/admin/refresh-strengths", dependencies=[Depends(require_api_key)])
def refresh_all_strengths(limit: int = 1000) -> dict[str, Any]:
    """Batch recalculate cached_strength for all memories (maintenance endpoint)."""
    if limit < 1 or limit > 10000:
        raise HTTPException(status_code=400, detail="limit must be 1..10000")
    
    with get_db() as db:
        # Fetch all memories that need refresh
        rows = db.execute(
            """
            SELECT id, created_at, last_accessed_at, access_count
            FROM memories
            ORDER BY id DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
        
        updated_count = 0
        for row in rows:
            new_strength = strength_module.calculate_strength(
                created_at=row["created_at"],
                last_accessed_at=row["last_accessed_at"],
                access_count=row["access_count"] or 0,
            )
            
            db.execute(
                """
                UPDATE memories
                SET cached_strength = ?,
                    strength_updated_at = strftime('%Y-%m-%dT%H:%M:%fZ','now')
                WHERE id = ?
                """,
                (new_strength, row["id"]),
            )
            updated_count += 1
        
        db.commit()
    
    return {
        "success": True,
        "updated": updated_count,
        "message": f"Refreshed strength for {updated_count} memories",
    }


def _row_to_dict(row) -> dict[str, Any]:
    if row is None:
        return {}
    tags = json.loads(row["tags_json"]) if row["tags_json"] else None
    metadata = json.loads(row["metadata_json"]) if row["metadata_json"] else None
    
    result = {
        "id": row["id"],
        "created_at": row["created_at"],
        "user_id": row["user_id"],
        "source": row["source"],
        "title": row["title"],
        "content": row["content"],
        "tags": tags,
        "metadata": metadata,
    }
    
    # Include strength fields if available in row
    if "last_accessed_at" in row.keys():
        result["last_accessed_at"] = row["last_accessed_at"]
    if "access_count" in row.keys():
        result["access_count"] = row["access_count"]
    if "cached_strength" in row.keys():
        result["cached_strength"] = row["cached_strength"]
    if "strength_updated_at" in row.keys():
        result["strength_updated_at"] = row["strength_updated_at"]
    
    return result
