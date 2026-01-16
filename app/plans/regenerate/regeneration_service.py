"""Service orchestrator for plan regeneration.

This is the public entry point for plan regeneration.
"""

import asyncio
from datetime import date, datetime, timezone

from loguru import logger
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.coach.explainability import explain_plan_revision
from app.db.models import AthleteProfile, PlanRevision
from app.db.session import get_session
from app.plans.modify.plan_revision_repo import create_plan_revision
from app.plans.regenerate.regeneration_executor import execute_regeneration
from app.plans.regenerate.regeneration_validators import validate_regeneration
from app.plans.regenerate.types import RegenerationRequest
from app.plans.revision.types import PlanRevision as PydanticPlanRevision
from app.plans.revision.types import RevisionDelta, RevisionOutcome, RevisionRule, RevisionScope


def _db_revision_to_pydantic(db_revision: PlanRevision, user_request: str) -> PydanticPlanRevision:
    """Convert DB PlanRevision to Pydantic PlanRevision.

    Args:
        db_revision: Database PlanRevision model
        user_request: User request text

    Returns:
        Pydantic PlanRevision
    """
    # Map status to outcome
    outcome: RevisionOutcome
    if db_revision.status == "blocked":
        outcome = "blocked"
    elif db_revision.status in {"applied", "regenerated"}:
        outcome = "applied"
    else:
        outcome = "partially_applied"

    # Map revision_type to scope
    scope: RevisionScope
    if "day" in db_revision.revision_type:
        scope = "day"
    elif "week" in db_revision.revision_type:
        scope = "week"
    elif "season" in db_revision.revision_type:
        scope = "season"
    elif "race" in db_revision.revision_type or "regenerate" in db_revision.revision_type:
        scope = "race"
    else:
        scope = "week"  # Default

    # Extract deltas from DB revision
    deltas_list: list[RevisionDelta] = []
    if db_revision.deltas and isinstance(db_revision.deltas, dict):
        revision_data = db_revision.deltas.get("revision")
        if revision_data and isinstance(revision_data, dict) and "deltas" in revision_data:
            deltas_list.extend(
                RevisionDelta(**delta_dict)
                for delta_dict in revision_data["deltas"]
                if isinstance(delta_dict, dict)
            )

    # Extract rules from DB revision
    rules_list: list[RevisionRule] = []
    if db_revision.deltas and isinstance(db_revision.deltas, dict):
        revision_data = db_revision.deltas.get("revision")
        if revision_data and isinstance(revision_data, dict) and "rules" in revision_data:
            rules_list.extend(
                RevisionRule(**rule_dict)
                for rule_dict in revision_data["rules"]
                if isinstance(rule_dict, dict)
            )

    # Build affected range
    affected_range: dict[str, str] | None = None
    if db_revision.affected_start and db_revision.affected_end:
        affected_range = {
            "start": db_revision.affected_start.isoformat(),
            "end": db_revision.affected_end.isoformat(),
        }
    elif db_revision.affected_start:
        affected_range = {
            "start": db_revision.affected_start.isoformat(),
            "end": db_revision.affected_start.isoformat(),
        }

    return PydanticPlanRevision(
        revision_id=db_revision.id,
        created_at=db_revision.created_at,
        scope=scope,
        outcome=outcome,
        user_request=user_request,
        reason=db_revision.reason,
        deltas=deltas_list,
        rules=rules_list,
        affected_range=affected_range,
    )


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
        # Use order_by + first() instead of scalar_one_or_none() to handle cases
        # where multiple profiles exist with the same athlete_id (e.g., in tests).
        # Pick the most recently created one.
        athlete_profile = session.execute(
            select(AthleteProfile)
            .where(AthleteProfile.athlete_id == athlete_id)
            .order_by(AthleteProfile.created_at.desc())
            .limit(1)
        ).scalars().first()

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

            # Step 6: Generate explanation
            explanation = None
            try:
                # Convert DB PlanRevision to Pydantic PlanRevision
                pydantic_revision = _db_revision_to_pydantic(revision, user_request=req.reason or "Regenerate plan")

                # Build athlete context
                athlete_context = {}
                if athlete_profile.race_date:
                    athlete_context["race_date"] = athlete_profile.race_date

                # Build deltas dict
                deltas_dict = revision.deltas or {}

                # Generate explanation (async)
                explanation = loop.run_until_complete(
                    explain_plan_revision(
                        revision=pydantic_revision,
                        deltas=deltas_dict,
                        athlete_profile=athlete_context if athlete_context else None,
                        constraints_triggered=None,
                    )
                )
                # Store explanation in revision deltas for later retrieval
                if revision.deltas is None:
                    revision.deltas = {}
                revision.deltas["explanation"] = explanation.model_dump() if explanation else None
                session.flush()
            except Exception as explain_error:
                logger.warning(
                    "Failed to generate explanation for regeneration",
                    revision_id=revision.id,
                    error=str(explain_error),
                )
                # Don't fail regeneration if explanation fails

            # Step 7: Commit
            session.commit()

            logger.info(
                "Plan regeneration complete",
                revision_id=revision.id,
                new_sessions_count=len(new_sessions),
                has_explanation=explanation is not None,
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
