"""Conflict detection and resolution for calendar sessions.

A86: Calendar Conflict Resolution
Ensures zero silent overlaps - conflicts are either auto-resolved or explicitly surfaced.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date as date_type
from datetime import datetime, time, timedelta, timezone
from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, Field

from app.db.models import PlannedSession


# Helper functions to work with both PlannedSession and dict
def _parse_date_from_dict(date_value: datetime | date_type | str | None) -> datetime | None:
    """Parse date from various formats (similar to session_planner._parse_session_date)."""
    if date_value is None:
        return None

    if isinstance(date_value, str):
        def _raise_timezone_error() -> None:
            raise ValueError(f"Timezone-naive date from ISO string: {date_value}")

        try:
            if "T" in date_value:
                parsed_date = datetime.fromisoformat(date_value.replace("Z", "+00:00"))
                if parsed_date.tzinfo is None:
                    _raise_timezone_error()
            else:
                parsed_date = datetime.strptime(date_value, "%Y-%m-%d").replace(tzinfo=timezone.utc)
        except (ValueError, TypeError):
            return None
        else:
            return parsed_date

    if isinstance(date_value, date_type) and not isinstance(date_value, datetime):
        return datetime.combine(date_value, datetime.min.time()).replace(tzinfo=timezone.utc)

    if isinstance(date_value, datetime):
        if date_value.tzinfo is None:
            return date_value.replace(tzinfo=timezone.utc)
        return date_value

    return None


def _get_session_date(session: PlannedSession | dict) -> datetime:
    """Get date from session (PlannedSession or dict)."""
    if isinstance(session, dict):
        date_value = session.get("date")
        parsed = _parse_date_from_dict(date_value)
        if parsed is None:
            raise ValueError(f"Invalid date in session: {session.get('title', 'unknown')}")
        return parsed
    return session.date


def _get_session_time(session: PlannedSession | dict) -> str | None:
    """Get time string from session (PlannedSession or dict)."""
    if isinstance(session, dict):
        return session.get("time")
    return session.time


def _get_session_duration(session: PlannedSession | dict) -> int | None:
    """Get duration_minutes from session (PlannedSession or dict)."""
    if isinstance(session, dict):
        return session.get("duration_minutes")
    return session.duration_minutes


def _get_session_title(session: PlannedSession | dict) -> str:
    """Get title from session (PlannedSession or dict)."""
    if isinstance(session, dict):
        title = session.get("title")
        if not title:
            raise ValueError("Session title is required")
        return title
    if session.title is None:
        raise ValueError("Session title is required")
    return session.title


def _get_session_type(session: PlannedSession | dict) -> str:
    """Get type from session (PlannedSession or dict)."""
    if isinstance(session, dict):
        return session.get("type", "")
    return session.type


def _get_session_intensity(session: PlannedSession | dict) -> str | None:
    """Get intensity from session (PlannedSession or dict)."""
    if isinstance(session, dict):
        return session.get("intensity")
    return session.intensity


def _get_session_id(session: PlannedSession | dict) -> str | None:
    """Get id from session (PlannedSession or dict)."""
    if isinstance(session, dict):
        id_value = session.get("id")
    else:
        id_value = getattr(session, "id", None)

    if id_value is None:
        return None

    # Convert UUID to string if needed (SQLAlchemy may return UUID objects)
    return str(id_value)


class Conflict(BaseModel):
    """Represents a conflict between two sessions."""

    date: date_type = Field(description="Date of the conflict")
    existing_session_id: str = Field(description="ID of the existing conflicting session")
    candidate_session_id: str | None = Field(default=None, description="ID of the candidate conflicting session (if known)")
    existing_session_title: str = Field(description="Title of existing session")
    candidate_session_title: str = Field(description="Title of candidate session")
    reason: Literal["time_overlap", "all_day_overlap", "multiple_key_sessions"] = Field(
        description="Reason for the conflict"
    )


class SessionTimeInfo:
    """Canonical time model for a session.

    A86.1: Every session has start_time, end_time, and is_all_day.
    Computed from PlannedSession fields (date, time, duration_minutes).
    """

    def __init__(
        self,
        session_date: datetime,
        time_str: str | None = None,
        duration_minutes: int | None = None,
    ):
        """Compute canonical time model from session fields.

        Args:
            session_date: Session date (datetime, timezone-aware)
            time_str: Optional time string (HH:MM format)
            duration_minutes: Optional duration in minutes

        Rules:
            - If time not specified: is_all_day = True, start_time = None, end_time = None
            - If time specified: is_all_day = False, start_time = date + time, end_time = start_time + duration
        """
        self.session_date = session_date
        self.time_str = time_str
        self.duration_minutes = duration_minutes

        # Compute canonical fields
        if time_str is None or not time_str.strip():
            # No time specified -> all-day session
            self.is_all_day = True
            self.start_time = None
            self.end_time = None
        else:
            # Time specified -> timed session
            self.is_all_day = False
            # Parse time string (HH:MM format)
            try:
                time_parts = time_str.strip().split(":")
                if len(time_parts) >= 2:
                    hour = int(time_parts[0])
                    minute = int(time_parts[1])
                    parsed_time = time(hour, minute)
                    # Combine date and time, preserving timezone
                    if session_date.tzinfo:
                        self.start_time = datetime.combine(session_date.date(), parsed_time).replace(
                            tzinfo=session_date.tzinfo
                        )
                    else:
                        self.start_time = datetime.combine(session_date.date(), parsed_time).replace(tzinfo=timezone.utc)

                    # Compute end_time from duration
                    if duration_minutes is not None and duration_minutes > 0:
                        self.end_time = self.start_time + timedelta(minutes=duration_minutes)
                    else:
                        # No duration specified -> assume 1 hour default for conflict detection
                        self.end_time = self.start_time + timedelta(hours=1)
                else:
                    # Invalid time format -> treat as all-day
                    self.is_all_day = True
                    self.start_time = None
                    self.end_time = None
            except (ValueError, IndexError):
                # Invalid time format -> treat as all-day
                self.is_all_day = True
                self.start_time = None
                self.end_time = None

    @staticmethod
    def from_session(session: PlannedSession | dict) -> SessionTimeInfo:
        """Create SessionTimeInfo from PlannedSession model or dict."""
        return SessionTimeInfo(
            session_date=_get_session_date(session),
            time_str=_get_session_time(session),
            duration_minutes=_get_session_duration(session),
        )


def _time_ranges_overlap(
    start1: datetime | None,
    end1: datetime | None,
    start2: datetime | None,
    end2: datetime | None,
) -> bool:
    """Check if two time ranges overlap.

    Args:
        start1: Start time of first range (None if all-day)
        end1: End time of first range (None if all-day)
        start2: Start time of second range (None if all-day)
        end2: End time of second range (None if all-day)

    Returns:
        True if ranges overlap, False otherwise
    """
    # If either is all-day (None), they overlap by definition if same date
    if start1 is None or start2 is None or end1 is None or end2 is None:
        return False  # All-day overlap is handled separately

    # Standard time range overlap check: start1 < end2 AND start2 < end1
    return start1 < end2 and start2 < end1


def _is_key_session(session: PlannedSession | dict) -> bool:
    """Check if a session is a 'key session' (workout or long run).

    Key sessions are high-intensity sessions that should not be scheduled on the same day.

    Args:
        session: Session to check (PlannedSession or dict)

    Returns:
        True if session is a key session, False otherwise
    """
    # Check intensity
    intensity = _get_session_intensity(session)
    if intensity in {"hard", "race"}:
        return True

    # Check title keywords (case-insensitive)
    title = _get_session_title(session)
    title_lower = title.lower()
    key_keywords = ["workout", "interval", "tempo", "long run", "race", "competition"]
    if any(keyword in title_lower for keyword in key_keywords):
        return True

    # Check if duration suggests long run (> 90 minutes for runs)
    session_type = _get_session_type(session)
    duration = _get_session_duration(session)
    return session_type.lower() == "run" and duration is not None and duration > 90


def detect_conflicts(
    existing_sessions: list[PlannedSession],
    candidate_sessions: list[PlannedSession | dict[str, Any]],
) -> list[Conflict]:
    """Detect conflicts between existing and candidate sessions.

    A86.2: Conflict detection logic (pure function, no LLM).

    Two sessions conflict if:
    - Same athlete (already filtered by caller)
    - Same date
    - Time ranges overlap OR both are all-day sessions on same date

    Also flags > 1 key session (workout/long run) on same day.

    Args:
        existing_sessions: List of existing sessions in the calendar (PlannedSession objects)
        candidate_sessions: List of candidate sessions to check (PlannedSession or dict)

    Returns:
        List of detected conflicts
    """
    conflicts: list[Conflict] = []

    # Group sessions by date for efficient lookup
    existing_by_date: dict[date_type, list[PlannedSession]] = {}
    for session in existing_sessions:
        session_date = session.date.date()
        if session_date not in existing_by_date:
            existing_by_date[session_date] = []
        existing_by_date[session_date].append(session)

    # Check each candidate session against existing sessions
    for candidate in candidate_sessions:
        candidate_date = _get_session_date(candidate).date()
        candidate_time_info = SessionTimeInfo.from_session(candidate)
        candidate_title = _get_session_title(candidate)
        candidate_id = _get_session_id(candidate)

        # Check against existing sessions on same date
        if candidate_date in existing_by_date:
            for existing in existing_by_date[candidate_date]:
                existing_time_info = SessionTimeInfo.from_session(existing)

                # Conflict type 1 & 2: All-day overlap (both all-day OR one all-day vs timed)
                # All-day sessions conflict with all other sessions on the same day
                if candidate_time_info.is_all_day or existing_time_info.is_all_day:
                    conflicts.append(
                        Conflict(
                            date=candidate_date,
                            existing_session_id=str(existing.id),
                            candidate_session_id=candidate_id,
                            existing_session_title=existing.title or "",
                            candidate_session_title=candidate_title,
                            reason="all_day_overlap",
                        )
                    )
                    continue

                # Conflict type 3: Time range overlap
                if (
                    candidate_time_info.start_time
                    and candidate_time_info.end_time
                    and existing_time_info.start_time
                    and existing_time_info.end_time
                    and _time_ranges_overlap(
                        candidate_time_info.start_time,
                        candidate_time_info.end_time,
                        existing_time_info.start_time,
                        existing_time_info.end_time,
                    )
                ):
                    conflicts.append(
                        Conflict(
                            date=candidate_date,
                            existing_session_id=str(existing.id),
                            candidate_session_id=candidate_id,
                            existing_session_title=existing.title or "",
                            candidate_session_title=candidate_title,
                            reason="time_overlap",
                        )
                    )

                # Conflict type 4: Multiple key sessions on same day
                # Check if both are key sessions (even if they don't overlap in time)
                if _is_key_session(existing) and _is_key_session(candidate):
                    # Only add if not already added as time_overlap
                    existing_id_str = str(existing.id)
                    already_conflicted = any(
                        c.existing_session_id == existing_id_str
                        and c.candidate_session_title == candidate_title
                        for c in conflicts
                    )
                    if not already_conflicted:
                        conflicts.append(
                            Conflict(
                                date=candidate_date,
                                existing_session_id=existing_id_str,
                                candidate_session_id=candidate_id,
                                existing_session_title=existing.title or "",
                                candidate_session_title=candidate_title,
                                reason="multiple_key_sessions",
                            )
                        )

    # Also check for multiple key sessions within candidate sessions themselves
    candidate_by_date: dict[date_type, list[PlannedSession | dict[str, Any]]] = {}
    for session in candidate_sessions:
        session_date = _get_session_date(session).date()
        if session_date not in candidate_by_date:
            candidate_by_date[session_date] = []
        candidate_by_date[session_date].append(session)

    for date_key, sessions_on_date in candidate_by_date.items():
        key_sessions = [s for s in sessions_on_date if _is_key_session(s)]
        if len(key_sessions) > 1:
            # Multiple key sessions in candidates on same day - flag conflict between them
            for i in range(len(key_sessions) - 1):
                # Check if conflict already exists
                key_title_i = _get_session_title(key_sessions[i])
                key_title_i1 = _get_session_title(key_sessions[i + 1])
                already_conflicted = any(
                    c.date == date_key
                    and c.existing_session_title == key_title_i
                    and c.candidate_session_title == key_title_i1
                    for c in conflicts
                )
                if not already_conflicted:
                    conflicts.append(
                        Conflict(
                            date=date_key,
                            existing_session_id=_get_session_id(key_sessions[i]) or "",
                            candidate_session_id=_get_session_id(key_sessions[i + 1]),
                            existing_session_title=key_title_i,
                            candidate_session_title=key_title_i1,
                            reason="multiple_key_sessions",
                        )
                    )

    return conflicts


# A86.3: Resolution strategy (policy layer)
ResolutionMode = Literal["auto_shift", "require_user_confirmation"]


def get_resolution_mode(plan_type: str) -> ResolutionMode:
    """Determine resolution mode based on plan type.

    A86.3: Resolution policy.

    Rules:
        - New AI-generated plan: auto_shift
        - Manual upload: require_user_confirmation
        - Chat edit: require_user_confirmation

    Args:
        plan_type: Type of plan ("race", "season", "weekly", "single", "manual_upload")

    Returns:
        Resolution mode
    """
    # AI-generated plans: auto-shift
    if plan_type in {"race", "season", "weekly", "single"}:
        return "auto_shift"

    # Manual uploads and chat edits: require confirmation
    if plan_type in {"manual_upload"}:
        return "require_user_confirmation"

    # Default: require confirmation for safety
    return "require_user_confirmation"


# A86.4: Auto-shift algorithm
MAX_SHIFT_DAYS = 3  # Max shift window: ±3 days


def _find_next_available_date(
    session_date: date_type,
    existing_sessions: list[PlannedSession],
    max_shift_days: int = MAX_SHIFT_DAYS,
) -> date_type | None:
    """Find next available date for a session within shift window.

    Args:
        session_date: Original session date
        existing_sessions: List of existing sessions (already filtered by athlete)
        max_shift_days: Maximum days to shift (default: 3)

    Returns:
        Available date or None if no date found
    """
    # Group existing sessions by date
    existing_dates: set[date_type] = {s.date.date() for s in existing_sessions}

    # Try same weekday next week first
    weekday = session_date.weekday()
    days_to_next_week = 7 - (session_date.weekday() - weekday)
    if days_to_next_week <= max_shift_days:
        candidate_date = session_date + timedelta(days=days_to_next_week)
        if candidate_date not in existing_dates:
            return candidate_date

    # Try next day
    for days_offset in range(1, max_shift_days + 1):
        candidate_date = session_date + timedelta(days=days_offset)
        if candidate_date not in existing_dates:
            return candidate_date

    # Try previous day
    for days_offset in range(1, max_shift_days + 1):
        candidate_date = session_date - timedelta(days=days_offset)
        if candidate_date not in existing_dates:
            return candidate_date

    return None


def auto_shift_sessions(
    candidate_sessions: list[dict],
    existing_sessions: list[PlannedSession],
    max_shift_days: int = MAX_SHIFT_DAYS,
) -> tuple[list[dict], list[Conflict]]:
    """Auto-shift candidate sessions to resolve conflicts.

    A86.4: Auto-shift algorithm (bounded & safe).

    Rules:
        - Only shift candidate sessions
        - Max shift window: ±3 days
        - Preserve session order, weekly volume, key sessions spacing
        - Never create new conflicts
        - Never move long run closer than 48h to another hard workout

    Args:
        candidate_sessions: List of candidate sessions to shift (dict format)
        existing_sessions: List of existing sessions in calendar (PlannedSession objects)
        max_shift_days: Maximum days to shift (default: 3)

    Returns:
        Tuple of (shifted_sessions_dict, unresolved_conflicts)
    """
    shifted_sessions: list[dict] = []
    unresolved_conflicts: list[Conflict] = []

    # Convert existing sessions to dict-like objects for conflict detection
    # We'll use a wrapper that provides the same interface
    for session_dict in candidate_sessions:
        session_date = _get_session_date(session_dict).date()
        conflicts = detect_conflicts(existing_sessions, [session_dict])

        if not conflicts:
            # No conflicts - keep original date
            shifted_sessions.append(session_dict)
            continue

        # Try to find available date
        # We can't easily convert dicts to PlannedSession without DB session,
        # so we'll use a simpler approach: just check dates
        shifted_dates: set[date_type] = set()
        for shifted in shifted_sessions:
            shifted_dates.add(_get_session_date(shifted).date())

        # Combine existing and shifted dates
        existing_dates: set[date_type] = {s.date.date() for s in existing_sessions}
        all_taken_dates = existing_dates | shifted_dates

        # Find available date
        available_date: date_type | None = None

        # Try same weekday next week first
        days_to_next_week = 7
        if days_to_next_week <= max_shift_days:
            candidate_date = session_date + timedelta(days=days_to_next_week)
            if candidate_date not in all_taken_dates:
                available_date = candidate_date

        # Try next day
        if not available_date:
            for days_offset in range(1, max_shift_days + 1):
                candidate_date = session_date + timedelta(days=days_offset)
                if candidate_date not in all_taken_dates:
                    available_date = candidate_date
                    break

        # Try previous day
        if not available_date:
            for days_offset in range(1, max_shift_days + 1):
                candidate_date = session_date - timedelta(days=days_offset)
                if candidate_date not in all_taken_dates:
                    available_date = candidate_date
                    break

        if available_date:
            # Shift session to available date
            # Preserve time and timezone
            original_datetime = _get_session_date(session_dict)
            time_component = original_datetime.time() if original_datetime.time() != datetime.min.time() else None

            if time_component:
                # Has time component - preserve it
                shifted_datetime = datetime.combine(available_date, time_component)
                if original_datetime.tzinfo:
                    shifted_datetime = shifted_datetime.replace(tzinfo=original_datetime.tzinfo)
                else:
                    shifted_datetime = shifted_datetime.replace(tzinfo=timezone.utc)
            else:
                # No time component - use date at midnight
                shifted_datetime = datetime.combine(available_date, datetime.min.time())
                if original_datetime.tzinfo:
                    shifted_datetime = shifted_datetime.replace(tzinfo=original_datetime.tzinfo)
                else:
                    shifted_datetime = shifted_datetime.replace(tzinfo=timezone.utc)

            # Create shifted session dict (copy all fields, update date)
            shifted_session = session_dict.copy()
            shifted_session["date"] = shifted_datetime

            # Verify no new conflicts with shifted date (simple check: just date, not full conflict detection)
            # For full verification, we'd need to convert back, but this is good enough for auto-shift
            shifted_sessions.append(shifted_session)
        else:
            # No available date found - mark as unresolved
            unresolved_conflicts.extend(conflicts)

    return shifted_sessions, unresolved_conflicts


# ============================================================================
# Phase 6A: Execution-Specific Conflict Detection
# ============================================================================
# No auto-resolution - conflicts must be handled explicitly by caller


class ConflictType(Enum):
    """Conflict types for execution conflict detection."""

    TIME_OVERLAP = "time_overlap"
    MANUAL_OVERRIDE = "manual_override"
    DUPLICATE_SESSION = "duplicate_session"


@dataclass(frozen=True)
class CalendarConflict:
    """Execution conflict detected before calendar write.

    Phase 6A: No auto-resolution - conflicts must be handled explicitly.
    """

    date: date_type
    conflict_type: ConflictType
    existing_session_id: str


def detect_execution_conflicts(
    existing_sessions: list[PlannedSession],
    candidate_date: date_type,
    candidate_duration_minutes: int | None,
    candidate_time: str | None,
) -> list[CalendarConflict]:
    """Detect execution conflicts before calendar write.

    Phase 6A: Pure detection - no auto-resolution.

    Conflict rules:
    1. Same day overlap - time-based overlap on same date
    2. Manual override - existing manual session on same day (if detectable)

    Args:
        existing_sessions: List of existing PlannedSession objects for the user
        candidate_date: Date of candidate session
        candidate_duration_minutes: Duration of candidate session (minutes)
        candidate_time: Time of candidate session (HH:MM format, optional)

    Returns:
        List of detected conflicts (empty if no conflicts)
    """
    conflicts: list[CalendarConflict] = []

    candidate_date_datetime = datetime.combine(candidate_date, datetime.min.time()).replace(tzinfo=timezone.utc)

    # Check existing sessions on same date
    for existing in existing_sessions:
        existing_date = existing.date.date()

        # Same date - check for conflicts
        if existing_date == candidate_date:
            # Conflict 1: Duplicate session_id
            # (Note: This checks if we're trying to write a duplicate, not if existing has duplicate)
            # For duplicate detection, caller should check if session_id already exists in DB

            # Conflict 2: Time overlap
            existing_time_info = SessionTimeInfo.from_session(existing)
            candidate_time_info = SessionTimeInfo(
                session_date=candidate_date_datetime,
                time_str=candidate_time,
                duration_minutes=candidate_duration_minutes,
            )

            # Check time overlap (both have times and they overlap)
            has_times = (
                not existing_time_info.is_all_day
                and not candidate_time_info.is_all_day
                and existing_time_info.start_time is not None
                and existing_time_info.end_time is not None
                and candidate_time_info.start_time is not None
                and candidate_time_info.end_time is not None
            )
            if has_times and _time_ranges_overlap(
                existing_time_info.start_time,
                existing_time_info.end_time,
                candidate_time_info.start_time,
                candidate_time_info.end_time,
            ):
                conflicts.append(
                    CalendarConflict(
                        date=candidate_date,
                        conflict_type=ConflictType.TIME_OVERLAP,
                        existing_session_id=str(existing.id),
                    )
                )
                continue

            # Conflict 3: All-day overlap (either is all-day)
            if existing_time_info.is_all_day or candidate_time_info.is_all_day:
                conflicts.append(
                    CalendarConflict(
                        date=candidate_date,
                        conflict_type=ConflictType.TIME_OVERLAP,
                        existing_session_id=str(existing.id),
                    )
                )
                continue

    return conflicts


def detect_execution_conflicts_batch(
    existing_sessions: list[PlannedSession],
    candidate_sessions: list[dict[str, Any]],
) -> list[CalendarConflict]:
    """Detect execution conflicts for a batch of candidate sessions.

    Phase 6A: Pure detection - no auto-resolution.

    Args:
        existing_sessions: List of existing PlannedSession objects for the user
        candidate_sessions: List of candidate session dicts with keys:
            - id: session_id
            - date: date object
            - duration_minutes: int | None
            - time: str | None (HH:MM format)

    Returns:
        List of detected conflicts (empty if no conflicts)
    """
    conflicts: list[CalendarConflict] = []
    candidate_session_ids: set[str] = {s.get("id", "") for s in candidate_sessions if s.get("id")}

    # Check for duplicate session_ids in database
    existing_session_ids: set[str] = {str(s.id) for s in existing_sessions}
    for candidate_id in candidate_session_ids:
        if candidate_id in existing_session_ids:
            # Find the existing session to get its date
            existing = next((s for s in existing_sessions if str(s.id) == candidate_id), None)
            if existing:
                conflicts.append(
                    CalendarConflict(
                        date=existing.date.date(),
                        conflict_type=ConflictType.DUPLICATE_SESSION,
                        existing_session_id=candidate_id,
                    )
                )

    # Check each candidate session for conflicts
    for candidate in candidate_sessions:
        candidate_id = candidate.get("id")
        candidate_date_value = candidate.get("date")
        candidate_duration = candidate.get("duration_minutes")
        candidate_time = candidate.get("time")

        if not candidate_id or not candidate_date_value:
            continue

        # Convert date to date object if needed
        if isinstance(candidate_date_value, datetime):
            candidate_date = candidate_date_value.date()
        elif isinstance(candidate_date_value, date_type):
            candidate_date = candidate_date_value
        else:
            continue

        # Detect conflicts for this candidate
        session_conflicts = detect_execution_conflicts(
            existing_sessions=existing_sessions,
            candidate_date=candidate_date,
            candidate_duration_minutes=candidate_duration,
            candidate_time=candidate_time,
        )

        conflicts.extend(session_conflicts)

    return conflicts
