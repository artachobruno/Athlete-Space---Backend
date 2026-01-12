"""Internal AI ops API router."""

from fastapi import APIRouter

from app.internal.ai_ops.cache import get_cached_ai_ops_summary

router = APIRouter(
    prefix="/internal/ai",
    tags=["internal"],
    include_in_schema=False,
)


@router.get("/summary")
async def get_ai_ops_summary():
    """Get AI ops metrics summary (cached).

    Returns:
        AiOpsSummary with all metrics aggregated
    """
    return get_cached_ai_ops_summary()
