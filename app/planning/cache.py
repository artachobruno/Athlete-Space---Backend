from loguru import logger

from app.planning.schema.session_output import SessionPlan
from app.planning.schema.session_spec import SessionSpec

_spec_cache: dict[int, SessionPlan] = {}


def _cache_key(spec: SessionSpec) -> int:
    """Generate cache key from SessionSpec.

    Args:
        spec: SessionSpec to generate key for

    Returns:
        Hash integer for caching
    """
    key_tuple = (
        spec.sport.value,
        spec.session_type.value,
        spec.intensity.value,
        spec.target_distance_km,
        spec.target_duration_min,
        spec.phase,
    )
    return hash(key_tuple)


def get_cached_session(spec: SessionSpec) -> SessionPlan | None:
    """Get cached session plan for a SessionSpec.

    Args:
        spec: SessionSpec to look up

    Returns:
        Cached SessionPlan or None if not found
    """
    key = _cache_key(spec)
    cached = _spec_cache.get(key)
    if cached:
        logger.debug(
            "planning_cache: Cache hit",
            cache_key=key,
            sport=spec.sport.value,
            session_type=spec.session_type.value,
            intensity=spec.intensity.value,
        )
    return cached


def set_cached_session(spec: SessionSpec, plan: SessionPlan) -> None:
    """Cache a session plan for a SessionSpec.

    Args:
        spec: SessionSpec used as cache key
        plan: SessionPlan to cache
    """
    key = _cache_key(spec)
    _spec_cache[key] = plan
    logger.debug(
        "planning_cache: Cache set",
        cache_key=key,
        sport=spec.sport.value,
        session_type=spec.session_type.value,
        intensity=spec.intensity.value,
    )


def clear_cache() -> None:
    """Clear the session plan cache."""
    _spec_cache.clear()
    logger.debug("planning_cache: Cache cleared")
