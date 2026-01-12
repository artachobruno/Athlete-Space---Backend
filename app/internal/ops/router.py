"""Internal ops API router."""

from fastapi import APIRouter

from app.internal.ops.cache import get_cached_ops_summary

router = APIRouter(prefix="/internal/ops", tags=["internal"])


@router.get("/summary")
async def get_ops_summary():
    """Get ops metrics summary (cached).

    Returns:
        OpsSummary with all metrics aggregated
    """
    return get_cached_ops_summary()
