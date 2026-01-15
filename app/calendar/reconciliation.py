"""Calendar reconciliation engine.

Deterministic, backend-only calendar reconciliation that compares:
- Planned training sessions
- Completed activities (Strava + manual uploads)

and produces a single authoritative session status per planned workout.

This logic is the source of truth for:
- Calendar display
- Coach reasoning
- Missed / skipped detection
- Progress reporting

No LLM usage. No database mutation. Read-only and deterministic.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from enum import StrEnum

from loguru import logger


def ensure_utc(dt: datetime | None) -> datetime | None:
    """Normalize datetime to UTC-aware.

    Converts naive datetimes to UTC-aware, and converts aware datetimes to UTC.
    Returns None if input is None.

    Args:
        dt: Datetime to normalize (may be naive or aware)

    Returns:
        UTC-aware datetime, or None if input was None
    """
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


class SessionStatus(StrEnum):
    """Session reconciliation status."""

    COMPLETED = "completed"
    MISSED = "missed"
    SUBSTITUTED = "substituted"
    PARTIAL = "partial"
    SKIPPED = "skipped"


class ReasonCode(StrEnum):
    """Machine-readable reason codes for reconciliation results."""

    EXACT_MATCH = "EXACT_MATCH"
    DURATION_SHORTFALL = "DURATION_SHORTFALL"
    DISTANCE_SHORTFALL = "DISTANCE_SHORTFALL"
    DURATION_AND_DISTANCE_SHORTFALL = "DURATION_AND_DISTANCE_SHORTFALL"
    WRONG_ACTIVITY_TYPE = "WRONG_ACTIVITY_TYPE"
    NO_ACTIVITY_FOUND = "NO_ACTIVITY_FOUND"
    MULTIPLE_CANDIDATES = "MULTIPLE_CANDIDATES"
    USER_MARKED_SKIPPED = "USER_MARKED_SKIPPED"
    REST_DAY_OVERRIDE = "REST_DAY_OVERRIDE"
    TYPE_MATCH_PARTIAL_VOLUME = "TYPE_MATCH_PARTIAL_VOLUME"


@dataclass
class PlannedSessionInput:
    """Input representation of a planned session."""

    session_id: str
    date: date
    type: str
    duration_minutes: int | None
    distance_km: float | None
    intensity: str | None
    status: str | None  # Current status from DB (planned, skipped, etc.)


@dataclass
class CompletedActivityInput:
    """Input representation of a completed activity."""

    activity_id: str
    start_time: datetime
    type: str | None
    duration_seconds: int | None
    distance_meters: float | None
    source: str


@dataclass
class ReconciliationResult:
    """Reconciliation result for a single planned session."""

    session_id: str
    date: str  # YYYY-MM-DD
    status: SessionStatus
    matched_activity_id: str | None
    confidence: float  # 0.0 - 1.0
    reason_code: ReasonCode
    explanation: str


@dataclass
class ReconciliationConfig:
    """Configuration for reconciliation matching."""

    time_tolerance_hours: int = 12  # ±12 hours window
    duration_threshold: float = 0.8  # 80% of planned duration required
    distance_threshold: float = 0.8  # 80% of planned distance required


def reconcile_sessions(
    planned_sessions: list[PlannedSessionInput],
    completed_activities: list[CompletedActivityInput],
    config: ReconciliationConfig | None = None,
) -> list[ReconciliationResult]:
    """Reconcile planned sessions with completed activities.

    For each planned session, determines the authoritative status by:
    1. Finding candidate activities within time window
    2. Matching by type and volume
    3. Enforcing one-activity-per-session rule
    4. Computing confidence and reason codes

    Args:
        planned_sessions: List of planned sessions to reconcile
        completed_activities: List of completed activities to match against
        config: Optional configuration (uses defaults if None)

    Returns:
        List of reconciliation results, one per planned session
    """
    if config is None:
        config = ReconciliationConfig()

    results: list[ReconciliationResult] = []
    matched_activity_ids: set[str] = set()

    for planned in planned_sessions:
        result = _reconcile_single_session(
            planned=planned,
            completed_activities=completed_activities,
            matched_activity_ids=matched_activity_ids,
            config=config,
        )
        results.append(result)

        if result.matched_activity_id:
            matched_activity_ids.add(result.matched_activity_id)

        logger.info(
            f"[RECONCILIATION] session_id={result.session_id} "
            f"status={result.status.value} "
            f"reason_code={result.reason_code.value} "
            f"confidence={result.confidence:.2f}"
        )

    return results


def _reconcile_single_session(
    planned: PlannedSessionInput,
    completed_activities: list[CompletedActivityInput],
    matched_activity_ids: set[str],
    config: ReconciliationConfig,
) -> ReconciliationResult:
    """Reconcile a single planned session.

    Args:
        planned: Planned session to reconcile
        completed_activities: All available activities
        matched_activity_ids: Set of activity IDs already matched to other sessions
        config: Reconciliation configuration

    Returns:
        Reconciliation result for this session
    """
    # Check for explicit skip
    if planned.status in {"skipped", "cancelled"}:
        return ReconciliationResult(
            session_id=planned.session_id,
            date=planned.date.isoformat(),
            status=SessionStatus.SKIPPED,
            matched_activity_id=None,
            confidence=1.0,
            reason_code=ReasonCode.USER_MARKED_SKIPPED,
            explanation="Session was marked as skipped or cancelled",
        )

    # Check for rest day
    if planned.type.lower() in {"rest", "rest day", "recovery"}:
        return ReconciliationResult(
            session_id=planned.session_id,
            date=planned.date.isoformat(),
            status=SessionStatus.SKIPPED,
            matched_activity_id=None,
            confidence=1.0,
            reason_code=ReasonCode.REST_DAY_OVERRIDE,
            explanation="Rest day - no activity expected",
        )

    # Find candidate activities within time window
    candidates = _find_candidate_activities(
        planned=planned,
        completed_activities=completed_activities,
        matched_activity_ids=matched_activity_ids,
        config=config,
    )

    if not candidates:
        return ReconciliationResult(
            session_id=planned.session_id,
            date=planned.date.isoformat(),
            status=SessionStatus.MISSED,
            matched_activity_id=None,
            confidence=1.0,
            reason_code=ReasonCode.NO_ACTIVITY_FOUND,
            explanation=f"No activity recorded on {planned.date.isoformat()}",
        )

    # If multiple candidates, choose best match
    if len(candidates) > 1:
        best_match = _select_best_match(planned, candidates)
        if best_match is None:
            # Ambiguous - multiple candidates but none clearly best
            return ReconciliationResult(
                session_id=planned.session_id,
                date=planned.date.isoformat(),
                status=SessionStatus.SUBSTITUTED,
                matched_activity_id=candidates[0].activity_id,
                confidence=0.4,
                reason_code=ReasonCode.MULTIPLE_CANDIDATES,
                explanation=f"Multiple activities found on {planned.date.isoformat()}, best match selected",
            )
        candidate = best_match
    else:
        candidate = candidates[0]

    # Evaluate match quality
    return _evaluate_match(planned, candidate, config)


def _find_candidate_activities(
    planned: PlannedSessionInput,
    completed_activities: list[CompletedActivityInput],
    matched_activity_ids: set[str],
    config: ReconciliationConfig,
) -> list[CompletedActivityInput]:
    """Find candidate activities for a planned session.

    Filters activities by:
    - Same calendar day (with tolerance)
    - Not already matched to another session

    Args:
        planned: Planned session
        completed_activities: All activities
        matched_activity_ids: Already matched activity IDs
        config: Configuration with time tolerance

    Returns:
        List of candidate activities
    """
    candidates: list[CompletedActivityInput] = []

    # Calculate time window
    planned_datetime = datetime.combine(planned.date, datetime.min.time()).replace(tzinfo=timezone.utc)
    window_start = planned_datetime - timedelta(hours=config.time_tolerance_hours)
    window_end = planned_datetime + timedelta(hours=24 + config.time_tolerance_hours)

    # Normalize window bounds to UTC
    window_start_utc = ensure_utc(window_start)
    window_end_utc = ensure_utc(window_end)

    for activity in completed_activities:
        # Skip if already matched
        if activity.activity_id in matched_activity_ids:
            continue

        # Normalize activity time and check if within time window
        activity_time = ensure_utc(activity.start_time)
        if activity_time is not None and window_start_utc is not None and window_end_utc is not None:
            if window_start_utc <= activity_time <= window_end_utc:
                candidates.append(activity)

    return candidates


def _select_best_match(
    planned: PlannedSessionInput,
    candidates: list[CompletedActivityInput],
) -> CompletedActivityInput | None:
    """Select the best matching activity from multiple candidates.

    Scoring criteria (in order of priority):
    1. Type match
    2. Duration closeness
    3. Distance closeness

    Args:
        planned: Planned session
        candidates: List of candidate activities

    Returns:
        Best matching activity, or None if ambiguous
    """
    if not candidates:
        return None

    scored: list[tuple[CompletedActivityInput, float]] = []

    for candidate in candidates:
        score = 0.0

        # Type match (highest priority)
        if _types_match(planned.type, candidate.type):
            score += 100.0
        else:
            score -= 50.0

        # Duration closeness
        if planned.duration_minutes and candidate.duration_seconds:
            planned_duration_sec = planned.duration_minutes * 60
            diff = abs(planned_duration_sec - candidate.duration_seconds)
            if planned_duration_sec > 0:
                closeness = 1.0 - (diff / planned_duration_sec)
                score += closeness * 20.0

        # Distance closeness
        if planned.distance_km and candidate.distance_meters:
            planned_distance_m = planned.distance_km * 1000.0
            diff = abs(planned_distance_m - candidate.distance_meters)
            if planned_distance_m > 0:
                closeness = 1.0 - (diff / planned_distance_m)
                score += closeness * 20.0

        scored.append((candidate, score))

    # Sort by score (descending)
    scored.sort(key=lambda x: x[1], reverse=True)

    if len(scored) == 1:
        return scored[0][0]

    # If top score is significantly better than second, return it
    if len(scored) >= 2:
        top_score = scored[0][1]
        second_score = scored[1][1]
        if top_score - second_score > 10.0:  # Significant gap
            return scored[0][0]

    # Ambiguous - return None to indicate multiple candidates
    return None


def _evaluate_match(
    planned: PlannedSessionInput,
    activity: CompletedActivityInput,
    config: ReconciliationConfig,
) -> ReconciliationResult:
    """Evaluate the quality of a match between planned session and activity.

    Args:
        planned: Planned session
        activity: Matched activity
        config: Configuration with thresholds

    Returns:
        Reconciliation result
    """
    type_matches = _types_match(planned.type, activity.type)

    if not type_matches:
        return ReconciliationResult(
            session_id=planned.session_id,
            date=planned.date.isoformat(),
            status=SessionStatus.SUBSTITUTED,
            matched_activity_id=activity.activity_id,
            confidence=0.4,
            reason_code=ReasonCode.WRONG_ACTIVITY_TYPE,
            explanation=f"{activity.type or 'Unknown'} activity substituted for planned {planned.type}",
        )

    # Type matches - check volume
    duration_shortfall = False
    distance_shortfall = False

    if planned.duration_minutes and activity.duration_seconds:
        planned_duration_sec = planned.duration_minutes * 60
        actual_duration_sec = activity.duration_seconds
        if actual_duration_sec < planned_duration_sec * config.duration_threshold:
            duration_shortfall = True

    if planned.distance_km and activity.distance_meters:
        planned_distance_m = planned.distance_km * 1000.0
        actual_distance_m = activity.distance_meters
        if actual_distance_m < planned_distance_m * config.distance_threshold:
            distance_shortfall = True

    # Both duration and distance specified and both shortfall
    if planned.duration_minutes and planned.distance_km and duration_shortfall and distance_shortfall:
        return ReconciliationResult(
            session_id=planned.session_id,
            date=planned.date.isoformat(),
            status=SessionStatus.PARTIAL,
            matched_activity_id=activity.activity_id,
            confidence=0.7,
            reason_code=ReasonCode.DURATION_AND_DISTANCE_SHORTFALL,
            explanation=_build_partial_explanation(planned, activity, duration_shortfall, distance_shortfall),
        )

    # Duration shortfall only
    if duration_shortfall:
        return ReconciliationResult(
            session_id=planned.session_id,
            date=planned.date.isoformat(),
            status=SessionStatus.PARTIAL,
            matched_activity_id=activity.activity_id,
            confidence=0.75,
            reason_code=ReasonCode.DURATION_SHORTFALL,
            explanation=_build_partial_explanation(planned, activity, duration_shortfall, distance_shortfall),
        )

    # Distance shortfall only
    if distance_shortfall:
        return ReconciliationResult(
            session_id=planned.session_id,
            date=planned.date.isoformat(),
            status=SessionStatus.PARTIAL,
            matched_activity_id=activity.activity_id,
            confidence=0.75,
            reason_code=ReasonCode.DISTANCE_SHORTFALL,
            explanation=_build_partial_explanation(planned, activity, duration_shortfall, distance_shortfall),
        )

    # Perfect match
    return ReconciliationResult(
        session_id=planned.session_id,
        date=planned.date.isoformat(),
        status=SessionStatus.COMPLETED,
        matched_activity_id=activity.activity_id,
        confidence=1.0,
        reason_code=ReasonCode.EXACT_MATCH,
        explanation=f"{planned.type} completed as planned",
    )


def _types_match(planned_type: str, activity_type: str | None) -> bool:
    """Check if activity type matches planned type.

    Handles case-insensitive matching and common variations.

    Args:
        planned_type: Planned session type
        activity_type: Activity type (may be None)

    Returns:
        True if types match
    """
    if not activity_type:
        return False

    planned_lower = planned_type.lower().strip()
    activity_lower = activity_type.lower().strip()

    # Exact match
    if planned_lower == activity_lower:
        return True

    # Common variations
    type_mappings: dict[str, list[str]] = {
        "run": ["run", "running"],
        "ride": ["ride", "bike", "cycling", "virtualride"],
        "bike": ["ride", "bike", "cycling", "virtualride"],
        "swim": ["swim", "swimming"],
        "walk": ["walk", "walking"],
    }

    return any(planned_lower in variations and activity_lower in variations for variations in type_mappings.values())


def _build_partial_explanation(
    planned: PlannedSessionInput,
    activity: CompletedActivityInput,
    duration_shortfall: bool,
    distance_shortfall: bool,
) -> str:
    """Build explanation string for partial completion.

    Args:
        planned: Planned session
        activity: Actual activity
        duration_shortfall: Whether duration was insufficient
        distance_shortfall: Whether distance was insufficient

    Returns:
        Explanation string
    """
    parts: list[str] = []

    if duration_shortfall and planned.duration_minutes and activity.duration_seconds:
        planned_sec = planned.duration_minutes * 60
        actual_sec = activity.duration_seconds
        pct = (actual_sec / planned_sec) * 100 if planned_sec > 0 else 0
        parts.append(f"duration {pct:.0f}% of planned")

    if distance_shortfall and planned.distance_km and activity.distance_meters:
        planned_m = planned.distance_km * 1000.0
        actual_m = activity.distance_meters
        pct = (actual_m / planned_m) * 100 if planned_m > 0 else 0
        parts.append(f"distance {pct:.0f}% of planned")

    if parts:
        return f"{planned.type} completed with " + ", ".join(parts)

    return f"{planned.type} completed but below planned volume"
