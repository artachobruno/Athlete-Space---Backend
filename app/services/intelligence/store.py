"""Persistence and versioning layer for training intents.

Intent is data. Store it like a first-class entity.
Never overwrite - append versions.
"""

from datetime import date, datetime, timezone

from dateutil import parser as date_parser
from loguru import logger
from sqlalchemy import select

from app.coach.schemas.intent_schemas import DailyDecision, SeasonPlan, WeeklyIntent, WeeklyReport
from app.coach.utils.date_extraction import extract_date_from_text, extract_session_count_from_text
from app.db.models import DailyDecision as DailyDecisionModel
from app.db.models import SeasonPlan as SeasonPlanModel
from app.db.models import WeeklyIntent as WeeklyIntentModel
from app.db.models import WeeklyReport as WeeklyReportModel
from app.db.session import get_session
from app.services.intelligence.weekly_report_metrics import compute_weekly_report_metrics


def _extract_date_from_race_string(race_str: str, season_start: date, season_end: date) -> date | None:
    """Extract date from race string using LLM extraction.

    Args:
        race_str: Race string (e.g., "Marathon - April 15, 2024" or "Spring Marathon - April 15")
        season_start: Season start date (for validation)
        season_end: Season end date (for validation)

    Returns:
        Parsed date or None if parsing fails
    """
    # First try dateutil parser as a fast fallback for common formats
    try:
        default_dt = datetime(season_start.year, 1, 1, tzinfo=timezone.utc)
        parsed_date = date_parser.parse(race_str, fuzzy=True, default=default_dt)
        if parsed_date:
            parsed_date_obj = parsed_date.date()
            if season_start <= parsed_date_obj <= season_end:
                logger.debug(f"Extracted date using dateutil parser: {parsed_date_obj}", race_str=race_str[:50])
                return parsed_date_obj
    except (ValueError, TypeError):
        pass

    # Use LLM extraction as primary method
    extracted_date = extract_date_from_text(
        text=race_str,
        context="race date",
        min_date=season_start,
        max_date=season_end,
    )

    if extracted_date:
        logger.debug(f"Extracted date using LLM: {extracted_date}", race_str=race_str[:50])
        return extracted_date

    return None


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

            # Extract metadata fields for fast queries
            start_date_dt = datetime.combine(plan.season_start, datetime.min.time()).replace(tzinfo=timezone.utc)
            end_date_dt = datetime.combine(plan.season_end, datetime.min.time()).replace(tzinfo=timezone.utc)
            total_weeks = (plan.season_end - plan.season_start).days // 7

            # Extract primary race info if available
            primary_race_date = None
            primary_race_name = None
            if plan.target_races:
                # Try to extract date from race strings and find the nearest/first race
                for race_str in plan.target_races:
                    # Try to parse date from race string (e.g., "Marathon - April 15, 2024")
                    race_date = _extract_date_from_race_string(race_str, plan.season_start, plan.season_end)
                    if race_date:
                        primary_race_date = datetime.combine(race_date, datetime.min.time()).replace(tzinfo=timezone.utc)
                        primary_race_name = race_str
                        break  # Use first race with valid date

                # If no date found, use first race as primary
                if primary_race_name is None:
                    primary_race_name = plan.target_races[0]

            # Create plan name from focus
            plan_name = plan.focus[:100] if len(plan.focus) > 100 else plan.focus

            # Create new plan
            # Use mode='json' to serialize dates/datetimes to strings for JSON storage
            plan_dict = plan.model_dump(mode="json")
            plan_dict["_context_hash"] = context_hash
            new_plan = SeasonPlanModel(
                user_id=user_id,
                athlete_id=athlete_id,
                plan_name=plan_name,
                start_date=start_date_dt,
                end_date=end_date_dt,
                primary_race_date=primary_race_date,
                primary_race_name=primary_race_name,
                total_weeks=total_weeks,
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

            # Extract metadata fields for fast queries
            primary_focus = intent.focus[:100] if len(intent.focus) > 100 else intent.focus
            target_volume_hours = intent.volume_target_hours

            # Estimate total sessions from intensity_distribution using LLM extraction
            total_sessions = None
            if intent.intensity_distribution:
                extracted_count = extract_session_count_from_text(intent.intensity_distribution)
                if extracted_count is not None:
                    total_sessions = extracted_count
            # Fallback: estimate from volume (rough heuristic: ~1.5 hours per session)
            if total_sessions is None and target_volume_hours:
                total_sessions = int(target_volume_hours / 1.5) if target_volume_hours > 0 else None

            # Create new intent
            # Use mode='json' to serialize dates/datetimes to strings for JSON storage
            intent_dict = intent.model_dump(mode="json")
            intent_dict["_context_hash"] = context_hash
            new_intent = WeeklyIntentModel(
                user_id=user_id,
                athlete_id=athlete_id,
                primary_focus=primary_focus,
                total_sessions=total_sessions,
                target_volume_hours=target_volume_hours,
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

            # Extract metadata fields for fast queries
            recommendation_type = decision.recommendation
            recommended_intensity = decision.intensity_focus
            has_workout = decision.recommendation != "rest"

            # Create new decision
            # Use mode='json' to serialize dates/datetimes to strings for JSON storage
            decision_dict = decision.model_dump(mode="json")
            decision_dict["_context_hash"] = context_hash
            new_decision = DailyDecisionModel(
                user_id=user_id,
                athlete_id=athlete_id,
                recommendation_type=recommendation_type,
                recommended_intensity=recommended_intensity,
                has_workout=has_workout,
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

    @staticmethod
    def save_weekly_report(
        user_id: str,
        athlete_id: int,
        report: WeeklyReport,
    ) -> str:
        """Save a weekly report with versioning.

        Args:
            user_id: User ID
            athlete_id: Athlete ID
            report: WeeklyReport to save

        Returns:
            Report ID (UUID string)
        """
        with get_session() as session:
            # Get next version
            week_start_dt = datetime.combine(report.week_start, datetime.min.time()).replace(tzinfo=timezone.utc)
            existing = (
                session.execute(
                    select(WeeklyReportModel)
                    .where(
                        WeeklyReportModel.athlete_id == athlete_id,
                        WeeklyReportModel.week_start == week_start_dt,
                    )
                    .order_by(WeeklyReportModel.version.desc())
                )
            ).scalar_one_or_none()

            next_version = (existing.version + 1) if existing else 1

            # Deactivate previous active reports for this week
            if existing:
                existing.is_active = False
                session.add(existing)

            # Extract metadata fields for fast queries
            key_insights_count = len(report.progress_highlights) if report.progress_highlights else None

            # Compute metrics for the week
            week_start_dt = datetime.combine(report.week_start, datetime.min.time()).replace(tzinfo=timezone.utc)
            metrics = compute_weekly_report_metrics(athlete_id, week_start_dt)

            summary_score = metrics["summary_score"]
            activities_completed = metrics["activities_completed"]
            adherence_percentage = metrics["adherence_percentage"]

            # Create new report
            new_report = WeeklyReportModel(
                user_id=user_id,
                athlete_id=athlete_id,
                summary_score=summary_score,
                key_insights_count=key_insights_count,
                activities_completed=activities_completed,
                adherence_percentage=adherence_percentage,
                report_data=report.model_dump(),
                week_start=datetime.combine(report.week_start, datetime.min.time()).replace(tzinfo=timezone.utc),
                week_end=datetime.combine(report.week_end, datetime.min.time()).replace(tzinfo=timezone.utc),
                version=next_version,
                is_active=True,
            )

            session.add(new_report)
            session.flush()

            report_id = new_report.id
            session.commit()

            logger.info(
                "Weekly report saved",
                report_id=report_id,
                user_id=user_id,
                athlete_id=athlete_id,
                week_start=report.week_start.isoformat(),
                version=next_version,
            )

            return report_id

    @staticmethod
    def get_latest_weekly_report(
        athlete_id: int,
        week_start: datetime,
        active_only: bool = True,
    ) -> WeeklyReportModel | None:
        """Get the latest weekly report for a specific week.

        Args:
            athlete_id: Athlete ID
            week_start: Week start date (Monday)
            active_only: If True, only return active reports

        Returns:
            Latest WeeklyReportModel or None
        """
        with get_session() as session:
            query = select(WeeklyReportModel).where(
                WeeklyReportModel.athlete_id == athlete_id,
                WeeklyReportModel.week_start == week_start,
            )

            if active_only:
                query = query.where(WeeklyReportModel.is_active.is_(True))

            return session.execute(query.order_by(WeeklyReportModel.version.desc())).scalar_one_or_none()
