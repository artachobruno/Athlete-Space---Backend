"""Scheduled jobs for generating training intelligence.

Generates daily decisions for all active users on a schedule.
"""

from datetime import date, datetime, timedelta, timezone

from loguru import logger
from sqlalchemy import select

from app.db.models import StravaAccount
from app.db.session import get_session
from app.services.intelligence.context_builder import build_daily_decision_context
from app.services.intelligence.store import IntentStore
from app.services.intelligence.triggers import RegenerationTriggers


def _process_user_daily_decision(
    user_id: str,
    athlete_id: int,
    today: date,
    triggers: RegenerationTriggers,
    store: IntentStore,
) -> tuple[bool, bool]:
    """Process daily decision generation for a single user.

    Args:
        user_id: User ID
        athlete_id: Athlete ID
        today: Today's date
        triggers: RegenerationTriggers instance
        store: IntentStore instance

    Returns:
        Tuple of (success: bool, skipped: bool)
    """
    # Check if decision already exists
    decision_date_dt = datetime.combine(today, datetime.min.time()).replace(tzinfo=timezone.utc)
    existing = store.get_latest_daily_decision(athlete_id, decision_date_dt, active_only=True)

    if existing:
        logger.debug(f"Daily decision already exists for user_id={user_id}, athlete_id={athlete_id}, skipping")
        return False, True

    # Build context
    context = build_daily_decision_context(user_id, athlete_id, today)

    # Get weekly intent ID if available
    week_start = today - timedelta(days=today.weekday())
    week_start_dt = datetime.combine(week_start, datetime.min.time()).replace(tzinfo=timezone.utc)
    weekly_intent_model = store.get_latest_weekly_intent(athlete_id, week_start_dt, active_only=True)
    weekly_intent_id = weekly_intent_model.id if weekly_intent_model else None

    # Generate decision
    decision_id = triggers.maybe_regenerate_daily_decision(
        user_id=user_id,
        athlete_id=athlete_id,
        decision_date=today,
        context=context,
        weekly_intent_id=weekly_intent_id,
    )

    if decision_id:
        logger.info(f"Generated daily decision for user_id={user_id}, athlete_id={athlete_id}, decision_id={decision_id}")
        return True, False

    logger.debug(f"Daily decision generation skipped (context unchanged) for user_id={user_id}, athlete_id={athlete_id}")
    return False, True


def generate_daily_decisions_for_all_users() -> None:
    """Generate daily decisions for all users with connected Strava accounts.

    This function is designed to run overnight (e.g., via scheduler) to pre-generate
    daily decisions for all active users. This ensures decisions are available when
    users check their dashboard in the morning.

    Logs progress and errors but does not raise exceptions to avoid breaking the scheduler.
    """
    logger.info("Starting overnight daily decision generation for all users")

    triggers = RegenerationTriggers()
    store = IntentStore()
    today = datetime.now(timezone.utc).date()

    # Get all users with connected Strava accounts
    with get_session() as session:
        accounts = session.execute(select(StravaAccount)).scalars().all()
        user_accounts = [(acc.user_id, int(acc.athlete_id)) for acc in accounts]

    total_users = len(user_accounts)
    logger.info(f"Found {total_users} users with connected Strava accounts")

    success_count = 0
    error_count = 0
    skipped_count = 0

    for user_id, athlete_id in user_accounts:
        try:
            success, skipped = _process_user_daily_decision(user_id, athlete_id, today, triggers, store)
            if success:
                success_count += 1
            elif skipped:
                skipped_count += 1
        except Exception as e:
            logger.error(
                f"Failed to generate daily decision for user_id={user_id}, athlete_id={athlete_id}: {e}",
                exc_info=True,
            )
            error_count += 1

    logger.info(
        f"Completed overnight daily decision generation: "
        f"total={total_users}, success={success_count}, skipped={skipped_count}, errors={error_count}"
    )
