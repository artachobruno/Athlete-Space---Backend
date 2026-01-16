"""Calendar Write Service - Phase 6A.

Single entry point for all calendar writes.
All writes are idempotent and transactional.
"""

from dataclasses import dataclass
from datetime import datetime, timezone

from loguru import logger
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.calendar.conflicts import CalendarConflict, ConflictType, detect_execution_conflicts_batch
from app.db.models import AthleteProfile, PlannedSession, StravaAccount
from app.db.schema_v2_map import (
    combine_date_time,
    km_to_meters,
    mi_to_meters,
    minutes_to_seconds,
    normalize_sport,
)
from app.db.session import get_session
from app.planning.execution.contracts import ExecutableSession
from app.planning.execution.guards import validate_executable_sessions


@dataclass(frozen=True)
class WriteResult:
    """Result of a calendar write operation.

    Attributes:
        sessions_written: Number of sessions successfully written
        conflicts_detected: List of conflicts that prevented writes
        dry_run: Whether this was a dry run (no actual writes)
    """

    sessions_written: int
    conflicts_detected: list[CalendarConflict]
    dry_run: bool


class CalendarWriteService:
    """Service for idempotent calendar writes.

    Phase 6A: All writes are atomic, idempotent, and transactional.
    Uses (user_id, session_id) as idempotency key.
    """

    @staticmethod
    def _get_athlete_id(session: Session, user_id: str) -> int | None:
        """Get athlete_id from user_id.

        Checks AthleteProfile first, then StravaAccount.

        Args:
            session: Database session
            user_id: User ID

        Returns:
            Athlete ID or None if not found
        """
        # Try AthleteProfile first
        profile = session.execute(select(AthleteProfile).where(AthleteProfile.user_id == user_id)).first()
        if profile and profile[0].athlete_id:
            return int(profile[0].athlete_id)

        # Fallback to StravaAccount
        account = session.execute(select(StravaAccount).where(StravaAccount.user_id == user_id)).first()
        if account:
            return int(account[0].athlete_id)

        return None

    @staticmethod
    def _executable_to_planned_session(
        executable: ExecutableSession,
        user_id: str,
        athlete_id: int,  # Kept for _get_athlete_id compatibility, but not used in PlannedSession schema v2
    ) -> PlannedSession:
        """Convert ExecutableSession to PlannedSession model (schema v2).

        Args:
            executable: ExecutableSession to convert
            user_id: User ID
            athlete_id: Athlete ID (for compatibility, not used in PlannedSession schema v2)

        Returns:
            PlannedSession model instance (not yet added to session)
        """
        # Schema v2: Convert date to datetime and combine with time (if any) into starts_at
        session_date = datetime.combine(executable.date, datetime.min.time()).replace(tzinfo=timezone.utc)
        starts_at = combine_date_time(session_date, None)  # No time in ExecutableSession, use midnight

        # Schema v2: Convert distance_miles to distance_meters
        distance_meters = mi_to_meters(executable.distance_miles) if executable.distance_miles else None

        # Generate title from session_type
        title = executable.session_type.title() + " Run"

        # Determine intensity from session_type (simple mapping)
        intensity_map: dict[str, str] = {
            "easy": "easy",
            "recovery": "easy",
            "long": "moderate",
            "tempo": "hard",
            "interval": "hard",
            "hills": "hard",
            "strides": "moderate",
        }
        intensity = intensity_map.get(executable.session_type)

        # Schema v2: Convert duration_minutes to duration_seconds
        duration_seconds = minutes_to_seconds(executable.duration_minutes)

        return PlannedSession(
            id=executable.session_id,
            user_id=user_id,
            starts_at=starts_at,  # Schema v2: combined date + time (TIMESTAMPTZ)
            sport=normalize_sport("run"),  # Schema v2: sport instead of type, default to "run"
            title=title,
            duration_seconds=duration_seconds,  # Schema v2: duration_seconds instead of duration_minutes
            distance_meters=distance_meters,  # Schema v2: distance_meters instead of distance_km
            intensity=intensity,
            notes=None,
            season_plan_id=executable.plan_id,  # Schema v2: season_plan_id instead of plan_id
            status="planned",  # Schema v2: default status
            session_type=executable.session_type,  # Store session type
            tags=[],  # Schema v2: tags is JSONB array, default empty
            source="planner_v2",  # Schema v2: source field
        )

    def write_week(
        self,
        user_id: str,
        plan_id: str,
        sessions: list[ExecutableSession],
        *,
        dry_run: bool = False,
    ) -> WriteResult:
        """Write a week of sessions to the calendar.

        Phase 6A: Atomic write - either all sessions are written or none.

        Behavior:
        - Uses (user_id, session_id) as idempotency key
        - Writes inside a DB transaction
        - Supports dry_run=True for previews
        - Either writes all sessions or none

        Args:
            user_id: User ID
            plan_id: Plan ID (must match all sessions)
            sessions: List of ExecutableSession to write
            dry_run: If True, detect conflicts but don't write

        Returns:
            WriteResult with sessions_written and conflicts_detected

        Raises:
            ValueError: If plan_id doesn't match sessions or validation fails
        """
        logger.info(
            "[EXECUTION] Week write started",
            user_id=user_id,
            plan_id=plan_id,
            sessions_count=len(sessions),
            dry_run=dry_run,
        )

        # Validate guards
        validate_executable_sessions(sessions)

        # Validate plan_id matches
        for session in sessions:
            if session.plan_id != plan_id:
                raise ValueError(f"Session {session.session_id} has mismatched plan_id: {session.plan_id} != {plan_id}")

        with get_session() as session:
            # Get athlete_id
            athlete_id = self._get_athlete_id(session, user_id)
            if athlete_id is None:
                raise ValueError(f"Cannot find athlete_id for user_id: {user_id}")

            # Get date range for existing sessions query
            if not sessions:
                return WriteResult(sessions_written=0, conflicts_detected=[], dry_run=dry_run)

            # Schema v2: Build starts_at timestamps for date range query
            session_dates = [s.date for s in sessions]
            min_date = min(session_dates)
            max_date = max(session_dates)
            min_datetime = datetime.combine(min_date, datetime.min.time()).replace(tzinfo=timezone.utc)
            max_datetime = datetime.combine(max_date, datetime.max.time()).replace(tzinfo=timezone.utc)

            # Fetch existing sessions (schema v2: use starts_at instead of date, remove athlete_id)
            existing_sessions = list(
                session.execute(
                    select(PlannedSession)
                    .where(
                        PlannedSession.user_id == user_id,
                        PlannedSession.starts_at >= min_datetime,
                        PlannedSession.starts_at <= max_datetime,
                    )
                ).scalars()
            )

            # Check for duplicate session_ids
            existing_session_ids = {s.id for s in existing_sessions}
            duplicate_conflicts: list[CalendarConflict] = []
            for executable in sessions:
                if executable.session_id in existing_session_ids:
                    existing = next((s for s in existing_sessions if s.id == executable.session_id), None)
                    if existing:
                        duplicate_conflicts.append(
                            CalendarConflict(
                                date=executable.date,
                                conflict_type=ConflictType.DUPLICATE_SESSION,
                                existing_session_id=executable.session_id,
                            )
                        )

            # Convert ExecutableSession to dict format for conflict detection
            # Schema v2: Convert to starts_at for conflict detection (if conflict detection expects it)
            # Note: conflict detection may still expect old format - update separately if needed
            candidate_sessions = [
                {
                    "id": s.session_id,
                    "date": s.date,  # Conflict detection may still use this
                    "duration_minutes": s.duration_minutes,  # Conflict detection may still use this
                    "time": None,  # No time in ExecutableSession
                }
                for s in sessions
            ]

            # Detect conflicts
            batch_conflicts = detect_execution_conflicts_batch(existing_sessions, candidate_sessions)
            all_conflicts = duplicate_conflicts + batch_conflicts

            if all_conflicts:
                logger.warning(
                    "[EXECUTION] Conflict detected",
                    user_id=user_id,
                    plan_id=plan_id,
                    conflicts_count=len(all_conflicts),
                    dry_run=dry_run,
                )
                return WriteResult(sessions_written=0, conflicts_detected=all_conflicts, dry_run=dry_run)

            if dry_run:
                logger.info(
                    "[EXECUTION] Week write committed (dry run)",
                    user_id=user_id,
                    plan_id=plan_id,
                    sessions_count=len(sessions),
                )
                return WriteResult(sessions_written=len(sessions), conflicts_detected=[], dry_run=True)

            # Write all sessions (idempotent - check exists before insert)
            sessions_written = 0
            for executable in sessions:
                # Check if already exists (idempotency)
                existing = session.execute(
                    select(PlannedSession).where(
                        PlannedSession.id == executable.session_id,
                        PlannedSession.user_id == user_id,
                    )
                ).first()

                if existing:
                    # Already exists - skip (idempotent)
                    logger.debug(
                        "[EXECUTION] Session already exists, skipping",
                        session_id=executable.session_id,
                        user_id=user_id,
                    )
                    continue

                # Create PlannedSession
                planned = self._executable_to_planned_session(executable, user_id, athlete_id)
                session.add(planned)
                sessions_written += 1

            # Commit transaction (all or nothing)
            session.commit()

            logger.info(
                "[EXECUTION] Week write committed",
                user_id=user_id,
                plan_id=plan_id,
                sessions_written=sessions_written,
                sessions_total=len(sessions),
            )

            return WriteResult(sessions_written=sessions_written, conflicts_detected=[], dry_run=False)
