"""Persistence and versioning layer for training intents.

Intent is data. Store it like a first-class entity.
Never overwrite - append versions.
"""

from datetime import datetime, timezone

from loguru import logger
from sqlalchemy import select

from app.coach.schemas.intent_schemas import DailyDecision, SeasonPlan, WeeklyIntent
from app.db.models import DailyDecision as DailyDecisionModel
from app.db.models import SeasonPlan as SeasonPlanModel
from app.db.models import WeeklyIntent as WeeklyIntentModel
from app.db.session import get_session


class IntentStore:
    """Store for persisting and retrieving training intents.

    Handles:
    - Storing intents with versioning
    - Retrieving latest valid versions
    - Never overwriting - always appending
    """

    @staticmethod
    def save_season_plan(
        user_id: str,
        athlete_id: int,
        plan: SeasonPlan,
        context_hash: str,
    ) -> str:
        """Save a season plan with versioning.

        Args:
            user_id: User ID
            athlete_id: Athlete ID
            plan: SeasonPlan to save
            context_hash: Hash of the context used to generate this plan

        Returns:
            Plan ID (UUID string)
        """
        with get_session() as session:
            # Deactivate previous active plans for this athlete
            existing = (
                session.execute(
                    select(SeasonPlanModel).where(
                        SeasonPlanModel.athlete_id == athlete_id,
                        SeasonPlanModel.is_active.is_(True),
                    )
                )
                .scalars()
                .all()
            )

            for existing_plan in existing:
                existing_plan.is_active = False
                existing_plan.updated_at = datetime.now(timezone.utc)

            # Get next version number
            max_version = session.execute(
                select(SeasonPlanModel.version)
                .where(
                    SeasonPlanModel.athlete_id == athlete_id,
                )
                .order_by(SeasonPlanModel.version.desc())
            ).scalar()

            next_version = (max_version or 0) + 1

            # Create new plan
            # Use mode='json' to serialize dates/datetimes to strings for JSON storage
            plan_dict = plan.model_dump(mode="json")
            plan_dict["_context_hash"] = context_hash
            new_plan = SeasonPlanModel(
                user_id=user_id,
                athlete_id=athlete_id,
                plan_data=plan_dict,
                version=next_version,
                is_active=True,
            )

            session.add(new_plan)
            session.commit()

            logger.info(
                "Season plan saved",
                plan_id=new_plan.id,
                user_id=user_id,
                athlete_id=athlete_id,
                version=next_version,
            )

            return new_plan.id

    @staticmethod
    def get_latest_season_plan(
        athlete_id: int,
        active_only: bool = True,
    ) -> SeasonPlanModel | None:
        """Get the latest season plan for an athlete.

        Args:
            athlete_id: Athlete ID
            active_only: If True, only return active plans

        Returns:
            Latest SeasonPlanModel or None
        """
        with get_session() as session:
            query = select(SeasonPlanModel).where(
                SeasonPlanModel.athlete_id == athlete_id,
            )

            if active_only:
                query = query.where(SeasonPlanModel.is_active.is_(True))

            return session.execute(query.order_by(SeasonPlanModel.version.desc())).scalar_one_or_none()

    @staticmethod
    def save_weekly_intent(
        user_id: str,
        athlete_id: int,
        intent: WeeklyIntent,
        season_plan_id: str | None,
        context_hash: str,
    ) -> str:
        """Save a weekly intent with versioning.

        Args:
            user_id: User ID
            athlete_id: Athlete ID
            intent: WeeklyIntent to save
            season_plan_id: Optional reference to season plan
            context_hash: Hash of the context used to generate this intent

        Returns:
            Intent ID (UUID string)
        """
        with get_session() as session:
            # Deactivate previous active intents for this week
            week_start_dt = datetime.combine(intent.week_start, datetime.min.time()).replace(tzinfo=timezone.utc)
            existing = (
                session.execute(
                    select(WeeklyIntentModel).where(
                        WeeklyIntentModel.athlete_id == athlete_id,
                        WeeklyIntentModel.week_start == week_start_dt,
                        WeeklyIntentModel.is_active.is_(True),
                    )
                )
                .scalars()
                .all()
            )

            for existing_intent in existing:
                existing_intent.is_active = False
                existing_intent.updated_at = datetime.now(timezone.utc)

            # Get next version number for this week
            max_version = session.execute(
                select(WeeklyIntentModel.version)
                .where(
                    WeeklyIntentModel.athlete_id == athlete_id,
                    WeeklyIntentModel.week_start == week_start_dt,
                )
                .order_by(WeeklyIntentModel.version.desc())
            ).scalar()

            next_version = (max_version or 0) + 1

            # Create new intent
            # Use mode='json' to serialize dates/datetimes to strings for JSON storage
            intent_dict = intent.model_dump(mode="json")
            intent_dict["_context_hash"] = context_hash
            new_intent = WeeklyIntentModel(
                user_id=user_id,
                athlete_id=athlete_id,
                intent_data=intent_dict,
                season_plan_id=season_plan_id,
                week_start=week_start_dt,
                week_number=intent.week_number,
                version=next_version,
                is_active=True,
            )

            session.add(new_intent)
            session.commit()

            logger.info(
                "Weekly intent saved",
                intent_id=new_intent.id,
                user_id=user_id,
                athlete_id=athlete_id,
                week_start=intent.week_start.isoformat(),
                version=next_version,
            )

            return new_intent.id

    @staticmethod
    def get_latest_weekly_intent(
        athlete_id: int,
        week_start: datetime,
        active_only: bool = True,
    ) -> WeeklyIntentModel | None:
        """Get the latest weekly intent for a specific week.

        Args:
            athlete_id: Athlete ID
            week_start: Week start date (Monday)
            active_only: If True, only return active intents

        Returns:
            Latest WeeklyIntentModel or None
        """
        with get_session() as session:
            query = select(WeeklyIntentModel).where(
                WeeklyIntentModel.athlete_id == athlete_id,
                WeeklyIntentModel.week_start == week_start,
            )

            if active_only:
                query = query.where(WeeklyIntentModel.is_active.is_(True))

            return session.execute(query.order_by(WeeklyIntentModel.version.desc())).scalar_one_or_none()

    @staticmethod
    def save_daily_decision(
        user_id: str,
        athlete_id: int,
        decision: DailyDecision,
        weekly_intent_id: str | None,
        context_hash: str,
    ) -> str:
        """Save a daily decision with versioning.

        Args:
            user_id: User ID
            athlete_id: Athlete ID
            decision: DailyDecision to save
            weekly_intent_id: Optional reference to weekly intent
            context_hash: Hash of the context used to generate this decision

        Returns:
            Decision ID (UUID string)
        """
        with get_session() as session:
            # Deactivate previous active decisions for this date
            decision_date_dt = datetime.combine(decision.decision_date, datetime.min.time()).replace(tzinfo=timezone.utc)
            existing = (
                session.execute(
                    select(DailyDecisionModel).where(
                        DailyDecisionModel.athlete_id == athlete_id,
                        DailyDecisionModel.decision_date == decision_date_dt,
                        DailyDecisionModel.is_active.is_(True),
                    )
                )
                .scalars()
                .all()
            )

            for existing_decision in existing:
                existing_decision.is_active = False
                existing_decision.updated_at = datetime.now(timezone.utc)

            # Get next version number for this date
            max_version = session.execute(
                select(DailyDecisionModel.version)
                .where(
                    DailyDecisionModel.athlete_id == athlete_id,
                    DailyDecisionModel.decision_date == decision_date_dt,
                )
                .order_by(DailyDecisionModel.version.desc())
            ).scalar()

            next_version = (max_version or 0) + 1

            # Create new decision
            # Use mode='json' to serialize dates/datetimes to strings for JSON storage
            decision_dict = decision.model_dump(mode="json")
            decision_dict["_context_hash"] = context_hash
            new_decision = DailyDecisionModel(
                user_id=user_id,
                athlete_id=athlete_id,
                decision_data=decision_dict,
                weekly_intent_id=weekly_intent_id,
                decision_date=decision_date_dt,
                version=next_version,
                is_active=True,
            )

            session.add(new_decision)
            session.flush()  # Flush to ensure ID is generated and object is persisted

            # Verify ID is set after flush
            if not new_decision.id:
                raise ValueError("Decision ID was not generated after flush")

            # Refresh to ensure object is fully synchronized with database
            session.refresh(new_decision)

            decision_id = new_decision.id
            logger.debug(f"Decision ID after flush and refresh: {decision_id}, type: {type(decision_id)}")

            session.commit()

            # Verify the decision can be retrieved immediately after commit
            verification = session.execute(select(DailyDecisionModel).where(DailyDecisionModel.id == decision_id)).scalar_one_or_none()
            if verification is None:
                logger.error(
                    f"CRITICAL: Decision {decision_id} was committed but cannot be retrieved in same session. "
                    f"This indicates a database transaction issue."
                )
            else:
                logger.debug(f"Verified decision exists in database: {decision_id}")

            logger.info(
                "Daily decision saved",
                decision_id=decision_id,
                user_id=user_id,
                athlete_id=athlete_id,
                decision_date=decision.decision_date.isoformat(),
                version=next_version,
            )

            return decision_id

    @staticmethod
    def get_daily_decision_by_id(decision_id: str) -> DailyDecisionModel | None:
        """Get a daily decision by its ID.

        Args:
            decision_id: Decision ID (UUID string)

        Returns:
            DailyDecisionModel or None if not found
        """
        logger.debug(f"Looking up decision by ID: {decision_id}, type: {type(decision_id)}")
        with get_session() as session:
            result = session.execute(select(DailyDecisionModel).where(DailyDecisionModel.id == decision_id)).scalar_one_or_none()
            if result is None:
                # Debug: Check if any decisions exist for this athlete/date
                logger.warning(f"Decision not found by ID: {decision_id}")
            else:
                logger.debug(f"Found decision: id={result.id}, type={type(result.id)}")
            return result

    @staticmethod
    def get_latest_daily_decision(
        athlete_id: int,
        decision_date: datetime,
        active_only: bool = True,
    ) -> DailyDecisionModel | None:
        """Get the latest daily decision for a specific date.

        Args:
            athlete_id: Athlete ID
            decision_date: Decision date
            active_only: If True, only return active decisions

        Returns:
            Latest DailyDecisionModel or None
        """
        with get_session() as session:
            query = select(DailyDecisionModel).where(
                DailyDecisionModel.athlete_id == athlete_id,
                DailyDecisionModel.decision_date == decision_date,
            )

            if active_only:
                query = query.where(DailyDecisionModel.is_active.is_(True))

            return session.execute(query.order_by(DailyDecisionModel.version.desc())).scalar_one_or_none()
