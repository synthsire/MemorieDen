"""Memory strength calculation based on ACT-R cognitive model.

Implements exponential time decay with logarithmic access frequency boost.
"""
from __future__ import annotations

import math
import sqlite3
from datetime import datetime, timezone
from typing import Any

from . import config


def parse_iso_timestamp(ts: str | None) -> datetime | None:
    """Parse ISO 8601 timestamp to datetime."""
    if ts is None:
        return None
    try:
        # SQLite stores as 'YYYY-MM-DDTHH:MM:SS.fffZ'
        return datetime.fromisoformat(ts.replace('Z', '+00:00'))
    except (ValueError, AttributeError):
        return None


def calculate_strength(
    created_at: str,
    last_accessed_at: str | None,
    access_count: int,
    half_life_days: float | None = None,
    access_boost_factor: float | None = None,
) -> float:
    """
    Calculate memory strength using ACT-R inspired formula.
    
    Formula:
        strength = time_decay × access_boost
        
    Where:
        time_decay = exp(-λ × days_elapsed)
        λ = ln(2) / half_life_days
        access_boost = 1 + boost_factor × ln(access_count + 1)
    
    Args:
        created_at: ISO 8601 timestamp of memory creation
        last_accessed_at: ISO 8601 timestamp of last access (None = never accessed)
        access_count: Number of times memory has been accessed
        half_life_days: Decay half-life (default from config)
        access_boost_factor: Access frequency scaling (default from config)
    
    Returns:
        Strength value in range [0.0, ~2.0+] where:
        - 1.0 = newly created, never accessed
        - >1.0 = recently accessed or frequently accessed
        - <1.0 = old and rarely accessed
    """
    if half_life_days is None:
        half_life_days = config.MEMORY_DECAY_HALF_LIFE_DAYS
    if access_boost_factor is None:
        access_boost_factor = config.MEMORY_ACCESS_BOOST_FACTOR
    
    # Guard against invalid parameters
    if half_life_days <= 0:
        half_life_days = 30.0  # Fallback to default
    if access_count < 0:
        access_count = 0  # Floor at 0
    
    # Parse timestamps
    created_dt = parse_iso_timestamp(created_at)
    if created_dt is None:
        # Fallback: treat as very old if unparseable
        return 0.01
    
    # Use last access time if available, otherwise creation time
    last_accessed_dt = parse_iso_timestamp(last_accessed_at)
    reference_dt = last_accessed_dt if last_accessed_dt is not None else created_dt
    
    # Calculate days elapsed since last interaction
    now = datetime.now(timezone.utc)
    days_elapsed = (now - reference_dt).total_seconds() / 86400.0
    
    # Exponential time decay: strength = exp(-λt) where λ = ln(2)/half_life
    decay_rate = math.log(2) / half_life_days
    time_decay = math.exp(-decay_rate * days_elapsed)
    
    # Logarithmic access frequency boost
    # +1 to avoid log(0), scaling prevents runaway growth
    access_boost = 1.0 + access_boost_factor * math.log(access_count + 1)
    
    strength = time_decay * access_boost
    
    return strength


def calculate_strength_from_row(row: sqlite3.Row) -> float:
    """
    Calculate strength from a database row.
    
    Expects row to have: created_at, last_accessed_at, access_count
    """
    # sqlite3.Row doesn't have .get() method - use direct access with fallback
    last_accessed = row["last_accessed_at"] if "last_accessed_at" in row.keys() else None
    access_count = row["access_count"] if "access_count" in row.keys() else 0
    
    return calculate_strength(
        created_at=row["created_at"],
        last_accessed_at=last_accessed,
        access_count=access_count or 0,
    )


def should_refresh_cached_strength(strength_updated_at: str | None) -> bool:
    """
    Check if cached strength is stale and should be recomputed.
    
    Args:
        strength_updated_at: ISO timestamp of last strength update
    
    Returns:
        True if cache is stale or missing
    """
    staleness_hours = config.MEMORY_STRENGTH_CACHE_STALENESS_HOURS
    
    # Always refresh if staleness is 0 (on-the-fly mode)
    if staleness_hours <= 0:
        return True
    
    # Refresh if never computed
    if strength_updated_at is None:
        return True
    
    # Check age
    updated_dt = parse_iso_timestamp(strength_updated_at)
    if updated_dt is None:
        return True
    
    now = datetime.now(timezone.utc)
    hours_elapsed = (now - updated_dt).total_seconds() / 3600.0
    
    return hours_elapsed >= staleness_hours


def get_strength_details(
    created_at: str,
    last_accessed_at: str | None,
    access_count: int,
) -> dict[str, Any]:
    """
    Get detailed breakdown of strength calculation (for debugging).
    
    Returns:
        Dictionary with strength components and metadata
    """
    # Guard against invalid parameters
    if access_count < 0:
        access_count = 0
    half_life_days = config.MEMORY_DECAY_HALF_LIFE_DAYS
    if half_life_days <= 0:
        half_life_days = 30.0
    
    created_dt = parse_iso_timestamp(created_at)
    last_accessed_dt = parse_iso_timestamp(last_accessed_at)
    now = datetime.now(timezone.utc)
    
    reference_dt = last_accessed_dt if last_accessed_dt else created_dt
    
    if reference_dt:
        days_elapsed = (now - reference_dt).total_seconds() / 86400.0
        decay_rate = math.log(2) / half_life_days
        time_decay = math.exp(-decay_rate * days_elapsed)
    else:
        days_elapsed = 0.0
        time_decay = 0.01
    
    access_boost = 1.0 + config.MEMORY_ACCESS_BOOST_FACTOR * math.log(access_count + 1)
    final_strength = time_decay * access_boost
    
    return {
        "strength": final_strength,
        "components": {
            "time_decay": time_decay,
            "access_boost": access_boost,
        },
        "parameters": {
            "half_life_days": half_life_days,  # Use guarded value, not raw config
            "access_boost_factor": config.MEMORY_ACCESS_BOOST_FACTOR,
            "days_elapsed": days_elapsed,
            "access_count": access_count,
        },
        "timestamps": {
            "created_at": created_at,
            "last_accessed_at": last_accessed_at,
            "reference_time": reference_dt.isoformat() if reference_dt else None,
            "now": now.isoformat(),
        },
    }
