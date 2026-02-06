"""Configuration for memory decay system."""

import os


def get_float_env(key: str, default: float) -> float:
    """Get a float from environment variable, with fallback to default."""
    val = os.environ.get(key)
    if val is None:
        return default
    try:
        return float(val)
    except ValueError:
        return default


# Memory decay half-life in days (default: 30 days, ACT-R inspired)
# Memories decay to 50% strength after this many days without access
MEMORY_DECAY_HALF_LIFE_DAYS = get_float_env("MEMORY_DECAY_HALF_LIFE_DAYS", 30.0)

# Weighted combination of BM25 and strength in search scoring
# final_score = (BM25_WEIGHT * bm25_component) + (DECAY_WEIGHT * strength_component)
# These should sum to 1.0 for balanced scoring
MEMORY_STRENGTH_BM25_WEIGHT = get_float_env("MEMORY_STRENGTH_BM25_WEIGHT", 0.7)
MEMORY_STRENGTH_DECAY_WEIGHT = get_float_env("MEMORY_STRENGTH_DECAY_WEIGHT", 0.3)

# Access frequency boost scaling factor
# Controls how much impact access_count has on strength
# strength = time_decay * (1 + BOOST_FACTOR * ln(access_count + 1))
MEMORY_ACCESS_BOOST_FACTOR = get_float_env("MEMORY_ACCESS_BOOST_FACTOR", 0.2)

# Staleness threshold in hours - if cached_strength is older than this, recompute
# Set to 0 to always compute on-the-fly (slower but always fresh)
MEMORY_STRENGTH_CACHE_STALENESS_HOURS = get_float_env("MEMORY_STRENGTH_CACHE_STALENESS_HOURS", 24.0)
