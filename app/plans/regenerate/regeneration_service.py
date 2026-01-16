"""Service orchestrator for plan regeneration.

This is the public entry point for plan regeneration.
"""

import asyncio
from datetime import date, datetime, timezone

from loguru import logger
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import AthleteProfile, PlanRevision
from app.db.session import get_session
from app.plans.modify.plan_revision_repo import create_plan_revision
from app.plans.regenerate.regeneration_executor import execute_regeneration
from app.plans.regenerate.regeneration_validators import validate_regeneration
from app.plans.regenerate.types import RegenerationRequest


def regenerate_plan(
    *,
    user_id: str,
    athlete_id: int,
    req: RegenerationRequest,
) -> PlanRevision:
    """Regenerate plan from a start date.

    This is the public entry point for plan regeneration.

    Flow:
    1. Load athlete + plan
    2. Run validators
    3. Create PlanRevision(status="pending")
    4. Execute regeneration
    5. Update revision → status="regenerated"
    6. Commit

    Args:
        user_id: User ID
        athlete_id: Athlete ID
        req: Regeneration request

    Returns:
        PlanRevision with status="regenerated"

    Raises:
        ValueError: If validation fails
        RuntimeError: If regeneration fails
    """
    logger.info(
        "Starting plan regeneration",
        user_id=user_id,
        athlete_id=athlete_id,
        start_date=req.start_date.isoformat(),
        end_date=req.end_date.isoformat() if req.end_date else None,
        mode=req.mode,
    )

    with get_session() as session:
        # Step 1: Load athlete profile
        athlete_profile = session.execute(
            select(AthleteProfile).where(AthleteProfile.athlete_id == athlete_id)
        ).scalar_one_or_none()

        if athlete_profile is None:
            raise ValueError(f"Athlete profile not found for athlete_id={athlete_id}")

        # Step 2: Run validators
        today = datetime.now(timezone.utc).date()
        validate_regeneration(
            req=req,
            athlete_profile=athlete_profile,
            today=today,
            session=session,
            user_id=user_id,
            athlete_id=athlete_id,
        )

        # Step 3: Create PlanRevision(status="pending")
        revision = create_plan_revision(
            session=session,
            user_id=user_id,
            athlete_id=athlete_id,
            revision_type="regenerate_plan",
            status="pending",
            reason=req.reason,
            affected_start=req.start_date,
            affected_end=req.end_date,
            deltas={
                "regeneration_mode": req.mode,
                "allow_race_week": req.allow_race_week,
            },
        )
        session.flush()

        logger.info(
            "Created pending revision",
            revision_id=revision.id,
        )

        try:
            # Step 4: Execute regeneration (async)
            # Create a new event loop for this synchronous context
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            try:
                new_sessions = loop.run_until_complete(
                    execute_regeneration(
                        session=session,
                        athlete_profile=athlete_profile,
                        req=req,
                        revision_id=revision.id,
                        user_id=user_id,
                        athlete_id=athlete_id,
                    )
                )
            finally:
                loop.close()

            # Step 5: Update revision → status="regenerated"
            revision.status = "regenerated"
            if revision.deltas is None:
                revision.deltas = {}
            revision.deltas["regenerated_sessions_count"] = len(new_sessions)
            session.flush()

            # Step 6: Commit
            session.commit()

            logger.info(
                "Plan regeneration complete",
                revision_id=revision.id,
                new_sessions_count=len(new_sessions),
            )
        except Exception as e:
            # Mark revision as failed
            revision.status = "blocked"
            revision.blocked_reason = str(e)
            session.commit()

            logger.error(
                "Plan regeneration failed",
                revision_id=revision.id,
                error=str(e),
            )

            raise
        else:
            return revision
