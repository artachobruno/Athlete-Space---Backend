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
        athlete_id: int,
    ) -> PlannedSession:
        """Convert ExecutableSession to PlannedSession model.

        Args:
            executable: ExecutableSession to convert
            user_id: User ID
            athlete_id: Athlete ID

        Returns:
            PlannedSession model instance (not yet added to session)
        """
        # Convert date to datetime (midnight UTC)
        session_date = datetime.combine(executable.date, datetime.min.time()).replace(tzinfo=timezone.utc)

        # Convert distance_miles to distance_km (1 mile = 1.60934 km)
        distance_km = executable.distance_miles * 1.60934

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

        return PlannedSession(
            id=executable.session_id,
            user_id=user_id,
            athlete_id=athlete_id,
            date=session_date,
            time=None,  # No time specified in ExecutableSession
            type="Run",  # Default to Run for Phase 6A
            title=title,
            duration_minutes=executable.duration_minutes,
            distance_km=round(distance_km, 2),
            intensity=intensity,
            notes=None,
            plan_type="season",  # Default for Phase 6A
            plan_id=executable.plan_id,
            week_number=executable.week_index + 1,  # week_number is 1-based
            status="planned",
            completed=False,
            completed_at=None,
            completed_activity_id=None,
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

            session_dates = [s.date for s in sessions]
            min_date = min(session_dates)
            max_date = max(session_dates)

            # Fetch existing sessions
            existing_sessions = list(
                session.execute(
                    select(PlannedSession)
                    .where(
                        PlannedSession.user_id == user_id,
                        PlannedSession.athlete_id == athlete_id,
                        PlannedSession.date >= datetime.combine(min_date, datetime.min.time()).replace(tzinfo=timezone.utc),
                        PlannedSession.date <= datetime.combine(max_date, datetime.max.time()).replace(tzinfo=timezone.utc),
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
            candidate_sessions = [
                {
                    "id": s.session_id,
                    "date": s.date,
                    "duration_minutes": s.duration_minutes,
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
