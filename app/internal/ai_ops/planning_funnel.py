"""Planning funnel collector (read-only).

Tracks planning funnel metrics from progress events.
"""

from loguru import logger
from sqlalchemy import select

from app.db.models import CoachProgressEvent
from app.db.session import get_session
from app.internal.ai_ops.types import PlanningFunnelStats


def collect_planning_funnel() -> PlanningFunnelStats:
    """Collect planning funnel metrics from progress events.

    Returns:
        PlanningFunnelStats with counts for each stage
    """
    try:
        with get_session() as db:
            # Get all progress events (no time limit for funnel analysis)
            events = db.execute(select(CoachProgressEvent)).scalars().all()

            if not events:
                return PlanningFunnelStats(
                    requested=0,
                    validated=0,
                    planned=0,
                    executed=0,
                    failed=0,
                )

            # Count by status
            requested = 0
            validated = 0
            planned = 0
            executed = 0
            failed = 0

            # Track unique conversations/steps to avoid double counting
            seen_steps: set[tuple[str, str]] = set()

            for event in events:
                step_key = (event.conversation_id, event.step_id)

                # Count each unique step only once (use latest status)
                if step_key not in seen_steps:
                    seen_steps.add(step_key)

                    # Categorize by status
                    if event.status == "planned":
                        planned += 1
                    elif event.status == "completed":
                        executed += 1
                    elif event.status == "failed":
                        failed += 1
                    elif event.status == "in_progress":
                        # In progress could be validated or planning
                        validated += 1

            # Requested = total unique conversations with any progress event
            unique_conversations = len({event.conversation_id for event in events})
            requested = unique_conversations

            # Validated = conversations that moved beyond initial request
            # (approximate: any event with status != "planned")
            validated_conversations = len(
                {
                    event.conversation_id
                    for event in events
                    if event.status in {"in_progress", "completed", "failed", "skipped"}
                }
            )
            validated = validated_conversations

            return PlanningFunnelStats(
                requested=requested,
                validated=validated,
                planned=planned,
                executed=executed,
                failed=failed,
            )

    except Exception as e:
        logger.warning(f"Failed to collect planning funnel: {e}")
        return PlanningFunnelStats(
            requested=0,
            validated=0,
            planned=0,
            executed=0,
            failed=0,
        )
