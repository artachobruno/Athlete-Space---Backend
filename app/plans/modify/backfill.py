"""Backfill intent from session_type for existing PlannedSession records.

One-time migration logic to populate intent field from session_type.
After this runs, intent becomes the authoritative field.

Usage:
    from app.plans.modify.backfill import backfill_intent_from_session_type
    stats = backfill_intent_from_session_type()
    print(f"Updated {stats['updated']} sessions")
"""

from loguru import logger
from sqlalchemy import select

from app.db.models import PlannedSession
from app.db.session import get_session
from app.plans.week_planner import infer_intent_from_session_type


def backfill_intent_from_session_type() -> dict[str, int]:
    """Backfill intent field from session_type for all PlannedSession records.

    This is a one-time migration. After running, intent becomes authoritative
    and session_type is legacy/auxiliary.

    Returns:
        Dictionary with counts:
            - total: Total sessions processed
            - updated: Sessions that had intent backfilled
            - skipped: Sessions that already had intent
            - errors: Sessions that failed to backfill
    """
    stats = {"total": 0, "updated": 0, "skipped": 0, "errors": 0}

    with get_session() as db:
        # Get all sessions without intent
        sessions = list(
            db.execute(select(PlannedSession).where(PlannedSession.intent.is_(None))).scalars().all()
        )

        stats["total"] = len(sessions)

        for session in sessions:
            try:
                # Skip if already has intent
                if session.intent is not None:
                    stats["skipped"] += 1
                    continue

                # Infer intent from session_type
                if session.session_type:
                    inferred_intent = infer_intent_from_session_type(session.session_type)
                    session.intent = inferred_intent
                    stats["updated"] += 1
                else:
                    # No session_type, default to "easy"
                    session.intent = "easy"
                    stats["updated"] += 1
                    logger.warning(
                        "Backfilling intent: no session_type, defaulting to 'easy'",
                        session_id=session.id,
                    )

            except Exception as e:
                stats["errors"] += 1
                logger.error(
                    "Failed to backfill intent",
                    session_id=session.id,
                    error=str(e),
                )

        db.commit()

    logger.info(
        "Intent backfill complete",
        total=stats["total"],
        updated=stats["updated"],
        skipped=stats["skipped"],
        errors=stats["errors"],
    )

    return stats
