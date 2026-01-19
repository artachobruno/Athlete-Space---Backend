"""Race service for multi-race season support.

Handles race creation, priority management, and active race tracking.
"""

from datetime import datetime, timezone

from loguru import logger
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import ConversationProgress, RacePlan, RacePriority
from app.db.session import get_session


def resolve_race_focus(
    athlete_id: int,
    user_id: str,
    race_date: datetime,
    race_distance: str,
    race_name: str | None = None,
    target_time: str | None = None,
    race_priority: str | None = None,
    conversation_id: str | None = None,
) -> tuple[RacePlan, bool]:
    """Resolve race creation vs focus switching for multi-race season.

    Logic:
    - Case A: No races exist → Create race, set priority = A, set active_race_id
    - Case B: Race already exists (same date + distance) → Switch active_race_id
    - Case C: New race AND race exists → Create race with priority = B (default), active_race_id unchanged

    Args:
        athlete_id: Athlete ID
        user_id: User ID
        race_date: Race date
        race_distance: Race distance
        race_name: Optional race name
        target_time: Optional target time
        race_priority: Optional priority (A/B/C) - only used if explicitly provided
        conversation_id: Optional conversation ID for updating active_race_id

    Returns:
        Tuple of (RacePlan instance, was_created: bool)
        - was_created: True if a new race was created, False if existing race was found
    """
    with get_session() as db:
        # Check if race already exists (same date + distance)
        existing_race = db.execute(
            select(RacePlan).where(
                RacePlan.athlete_id == athlete_id,
                RacePlan.race_date == race_date,
                RacePlan.race_distance == race_distance,
            )
        ).scalar_one_or_none()

        if existing_race:
            # Case B: Race exists → Switch active_race_id
            logger.info(
                "Race already exists, switching focus",
                race_id=existing_race.id,
                athlete_id=athlete_id,
                conversation_id=conversation_id,
            )
            if conversation_id:
                _update_active_race_id(db, conversation_id, existing_race.id)
            return existing_race, False

        # Check if athlete has any existing races
        existing_races = db.execute(
            select(RacePlan).where(RacePlan.athlete_id == athlete_id).order_by(RacePlan.race_date)
        ).scalars().all()

        # Determine priority
        if race_priority:
            priority = race_priority
        elif len(existing_races) == 0:
            # Case A: No races exist → priority A
            priority = RacePriority.A.value
        else:
            # Case C: New race AND race exists → priority B (default)
            priority = RacePriority.B.value

        # Create new race
        new_race = RacePlan(
            user_id=user_id,
            athlete_id=athlete_id,
            race_date=race_date,
            race_distance=race_distance,
            race_name=race_name,
            target_time=target_time,
            priority=priority,
        )
        db.add(new_race)
        db.commit()
        db.refresh(new_race)

        logger.info(
            "Created new race",
            race_id=new_race.id,
            athlete_id=athlete_id,
            priority=priority,
            conversation_id=conversation_id,
        )

        # If this is the first race (priority A), set it as active
        if priority == RacePriority.A.value and conversation_id:
            _update_active_race_id(db, conversation_id, new_race.id)

        return new_race, True


def _update_active_race_id(db: Session, conversation_id: str, race_id: str) -> None:
    """Update active_race_id in conversation progress.

    Args:
        db: Database session
        conversation_id: Conversation ID
        race_id: Race ID to set as active
    """
    progress = db.execute(
        select(ConversationProgress).where(ConversationProgress.conversation_id == conversation_id)
    ).scalar_one_or_none()

    if progress:
        progress.active_race_id = race_id
        db.commit()
        logger.info(
            "Updated active_race_id",
            conversation_id=conversation_id,
            race_id=race_id,
        )
    else:
        logger.warning(
            "ConversationProgress not found for active_race_id update",
            conversation_id=conversation_id,
        )


def update_race_priority(
    athlete_id: int,
    race_id: str,
    new_priority: str,
    conversation_id: str | None = None,
) -> RacePlan:
    """Update race priority with invariant: only one A race per athlete.

    When promoting a race to A:
    - Current active race → priority = A
    - Previous A race → demoted to B

    Args:
        athlete_id: Athlete ID
        race_id: Race ID to update
        new_priority: New priority (A, B, or C)
        conversation_id: Optional conversation ID for updating active_race_id

    Returns:
        Updated RacePlan instance

    Raises:
        ValueError: If new_priority is invalid
        RuntimeError: If race not found
    """
    if new_priority not in {"A", "B", "C"}:
        raise ValueError(f"Invalid priority: {new_priority}. Must be A, B, or C.")

    with get_session() as db:
        race = db.execute(select(RacePlan).where(RacePlan.id == race_id, RacePlan.athlete_id == athlete_id)).scalar_one_or_none()

        if not race:
            raise RuntimeError(f"Race not found: {race_id} for athlete {athlete_id}")

        # If promoting to A, demote existing A race to B
        if new_priority == RacePriority.A.value and race.priority != RacePriority.A.value:
            existing_a_race = db.execute(
                select(RacePlan).where(
                    RacePlan.athlete_id == athlete_id,
                    RacePlan.priority == RacePriority.A.value,
                    RacePlan.id != race_id,
                )
            ).scalar_one_or_none()

            if existing_a_race:
                existing_a_race.priority = RacePriority.B.value
                logger.info(
                    "Demoted previous A race to B",
                    previous_a_race_id=existing_a_race.id,
                    athlete_id=athlete_id,
                )

        # Update race priority
        old_priority = race.priority
        race.priority = new_priority
        db.commit()
        db.refresh(race)

        logger.info(
            "Updated race priority",
            race_id=race_id,
            athlete_id=athlete_id,
            old_priority=old_priority,
            new_priority=new_priority,
        )

        # If this race is now priority A, set it as active
        if new_priority == RacePriority.A.value and conversation_id:
            _update_active_race_id(db, conversation_id, race_id)

        return race
