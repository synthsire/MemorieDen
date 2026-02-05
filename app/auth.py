from __future__ import annotations

from fastapi import Header, HTTPException


def require_api_key(x_api_key: str | None = Header(default=None)) -> None:
    """Optional single-tenant auth.

    If MEMORIEDEN_API_KEY is set, require header: X-API-Key: <value>
    If not set, auth is disabled.
    """
    import os

    expected = os.environ.get("MEMORIEDEN_API_KEY")
    if not expected:
        return
    if not x_api_key or x_api_key != expected:
        raise HTTPException(status_code=401, detail="Authentication required")
