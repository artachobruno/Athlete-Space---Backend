"""Training summary builder.

Deterministic, structured training summary derived from canonical data sources:
- Planned sessions
- Reconciliation results
- Completed activities (matched only)
- Training load metrics (CTL/ATL/TSB)

This summary is the single factual input to the coach's reasoning layer.
No LLM usage. No planning logic. No recommendations. Pure signal extraction.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from typing import Literal

from loguru import logger
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.calendar.reconciliation import SessionStatus
from app.calendar.reconciliation_service import reconcile_calendar
from app.db.models import Activity, DailyTrainingLoad, PlannedSession, StravaAccount
from app.db.session import get_session


@dataclass
class KeySession:
    """Key session information."""

    date: str  # YYYY-MM-DD
    title: str
    status: str  # completed, missed, partial, etc.
    matched_activity_id: str | None


@dataclass
class ReliabilityFlags:
    """Reliability flags for data quality."""

    low_compliance: bool
    high_variance: bool
    sparse_data: bool


@dataclass
class TrainingSummary:
    """Deterministic training summary for a date window.

    All fields are derived from canonical data sources.
    Same inputs → same output. No side effects. No persistence.
    """

    window_start: date
    window_end: date
    days: int

    volume: dict[str, float | int]
    intensity_distribution: dict[str, float]
    load: dict[str, float | str]
    execution: dict[str, int | float]
    anomalies: list[str]
    last_key_sessions: list[KeySession]
    reliability_flags: ReliabilityFlags


def get_athlete_id_from_user_id(user_id: str) -> int | None:
    """Get athlete_id from user_id via StravaAccount.

    Args:
        user_id: User ID (Clerk)

    Returns:
        Athlete ID (Strava) or None if not found
    """
    with get_session() as session:
        result = session.execute(select(StravaAccount).where(StravaAccount.user_id == user_id)).first()
        if result:
            return int(result[0].athlete_id)
        return None


def build_training_summary(
    user_id: str,
    athlete_id: int | None = None,
    window_days: int = 14,
    end_date: date | None = None,
) -> TrainingSummary:
    """Build deterministic training summary for a date window.

    Args:
        user_id: User ID (Clerk)
        athlete_id: Athlete ID (Strava), will be resolved from user_id if None
        window_days: Number of days to analyze (default: 14)
        end_date: End date for window (default: today)

    Returns:
        TrainingSummary with all computed metrics

    Rules:
        - Same inputs → same output
        - No side effects
        - No persistence
        - Uses only canonical data sources
    """
    if athlete_id is None:
        athlete_id_resolved = get_athlete_id_from_user_id(user_id)
        if athlete_id_resolved is None:
            raise ValueError(f"No athlete_id found for user_id={user_id}")
        athlete_id = athlete_id_resolved

    if end_date is None:
        end_date = datetime.now(timezone.utc).date()

    window_start = end_date - timedelta(days=window_days - 1)

    logger.info(f"[TRAINING_SUMMARY] Building summary for user_id={user_id}, athlete_id={athlete_id}, window={window_start} to {end_date}")

    with get_session() as session:
        # Fetch all required data
        reconciliation_results = reconcile_calendar(
            user_id=user_id,
            athlete_id=athlete_id,
            start_date=window_start,
            end_date=end_date,
        )

        planned_sessions = _fetch_planned_sessions(
            session=session,
            user_id=user_id,
            _athlete_id=athlete_id,
            start_date=window_start,
            end_date=end_date,
        )

        matched_activity_ids = {r.matched_activity_id for r in reconciliation_results if r.matched_activity_id is not None}

        matched_activities = _fetch_matched_activities(
            session=session,
            user_id=user_id,
            activity_ids=matched_activity_ids,
        )

        load_metrics = _fetch_load_metrics(
            session=session,
            user_id=user_id,
            start_date=window_start,
            end_date=end_date,
        )

        # Compute all metrics
        execution_metrics = _compute_execution_metrics(reconciliation_results)
        volume_metrics = _compute_volume_metrics(matched_activities, reconciliation_results)
        intensity_dist = _compute_intensity_distribution(matched_activities, planned_sessions, reconciliation_results)
        load_trend = _compute_load_trend(load_metrics)
        anomalies = _detect_anomalies(reconciliation_results, planned_sessions, load_metrics)
        key_sessions = _extract_key_sessions(planned_sessions, reconciliation_results)
        reliability_flags = _compute_reliability_flags(execution_metrics, intensity_dist, len(matched_activities))

        summary = TrainingSummary(
            window_start=window_start,
            window_end=end_date,
            days=window_days,
            volume=volume_metrics,
            intensity_distribution=intensity_dist,
            load={
                "ctl": load_metrics.get("ctl_current", 0.0),
                "atl": load_metrics.get("atl_current", 0.0),
                "tsb": load_metrics.get("tsb_current", 0.0),
                "trend": load_trend,
            },
            execution=execution_metrics,
            anomalies=anomalies,
            last_key_sessions=key_sessions,
            reliability_flags=reliability_flags,
        )

        logger.info(
            f"[TRAINING_SUMMARY] Summary built: "
            f"compliance={execution_metrics.get('compliance_rate', 0.0):.2f}, "
            f"sessions_completed={execution_metrics.get('completed_sessions', 0)}, "
            f"anomalies={len(anomalies)}"
        )

        return summary


def _fetch_planned_sessions(
    session: Session,
    user_id: str,
    _athlete_id: int,  # Unused: kept for API compatibility
    start_date: date,
    end_date: date,
) -> list[PlannedSession]:
    """Fetch planned sessions in date range."""
    start_datetime = datetime.combine(start_date, datetime.min.time()).replace(tzinfo=timezone.utc)
    end_datetime = datetime.combine(end_date, datetime.max.time()).replace(tzinfo=timezone.utc)

    result = session.execute(
        select(PlannedSession)
        .where(
            PlannedSession.user_id == user_id,
            PlannedSession.starts_at >= start_datetime,
            PlannedSession.starts_at <= end_datetime,
        )
        .order_by(PlannedSession.starts_at)
    )

    return list(result.scalars().all())


def _fetch_matched_activities(
    session: Session,
    user_id: str,
    activity_ids: set[str],
) -> list[Activity]:
    """Fetch activities that were matched to planned sessions."""
    if not activity_ids:
        return []

    result = session.execute(
        select(Activity)
        .where(
            Activity.user_id == user_id,
            Activity.id.in_(activity_ids),
        )
        .order_by(Activity.starts_at)
    )

    return list(result.scalars().all())


def _fetch_load_metrics(
    session: Session,
    user_id: str,
    start_date: date,
    end_date: date,
) -> dict[str, float]:
    """Fetch training load metrics (CTL, ATL, TSB) for date range."""
    result = session.execute(
        select(DailyTrainingLoad)
        .where(
            DailyTrainingLoad.user_id == user_id,
            DailyTrainingLoad.day >= start_date,
            DailyTrainingLoad.day <= end_date,
        )
        .order_by(DailyTrainingLoad.day)
    )

    rows = list(result.scalars().all())

    if not rows:
        return {
            "ctl_current": 0.0,
            "atl_current": 0.0,
            "tsb_current": 0.0,
            "ctl_start": 0.0,
            "atl_start": 0.0,
        }

    # Get current (most recent) values
    latest = rows[-1]
    earliest = rows[0]

    return {
        "ctl_current": float(latest.ctl) if latest.ctl is not None else 0.0,
        "atl_current": float(latest.atl) if latest.atl is not None else 0.0,
        "tsb_current": float(latest.tsb) if latest.tsb is not None else 0.0,
        "ctl_start": float(earliest.ctl) if earliest.ctl is not None else 0.0,
        "atl_start": float(earliest.atl) if earliest.atl is not None else 0.0,
    }


def _compute_execution_metrics(
    reconciliation_results: list,
) -> dict[str, int | float]:
    """Compute execution metrics from reconciliation results."""
    completed = sum(1 for r in reconciliation_results if r.status == SessionStatus.COMPLETED)
    missed = sum(1 for r in reconciliation_results if r.status == SessionStatus.MISSED)
    substituted = sum(1 for r in reconciliation_results if r.status == SessionStatus.SUBSTITUTED)
    partial = sum(1 for r in reconciliation_results if r.status == SessionStatus.PARTIAL)
    skipped = sum(1 for r in reconciliation_results if r.status == SessionStatus.SKIPPED)
    total_planned = len(reconciliation_results)

    # Compliance rate: (completed + partial) / (total - skipped)
    total_for_compliance = total_planned - skipped
    if total_for_compliance > 0:
        compliance_rate = (completed + partial) / total_for_compliance
    else:
        compliance_rate = 1.0 if total_planned == 0 else 0.0

    return {
        "completed_sessions": completed,
        "missed_sessions": missed,
        "substituted_sessions": substituted,
        "partial_sessions": partial,
        "skipped_sessions": skipped,
        "compliance_rate": round(compliance_rate, 3),
    }


def _compute_volume_metrics(
    matched_activities: list[Activity],
    reconciliation_results: list,
) -> dict[str, float | int]:
    """Compute volume metrics from matched activities only."""
    total_duration_seconds = sum(a.duration_seconds or 0 for a in matched_activities)
    total_duration_minutes = int(total_duration_seconds / 60)
    total_distance_meters = sum(a.distance_meters or 0 for a in matched_activities)
    total_distance_km = round(total_distance_meters / 1000.0, 2)

    sessions_completed = sum(1 for r in reconciliation_results if r.status in {SessionStatus.COMPLETED, SessionStatus.PARTIAL})
    sessions_planned = len(reconciliation_results)

    # Compliance rate from execution metrics
    skipped = sum(1 for r in reconciliation_results if r.status == SessionStatus.SKIPPED)
    total_for_compliance = sessions_planned - skipped
    if total_for_compliance > 0:
        compliance_rate = sessions_completed / total_for_compliance
    else:
        compliance_rate = 1.0 if sessions_planned == 0 else 0.0

    return {
        "total_duration_minutes": total_duration_minutes,
        "total_distance_km": total_distance_km,
        "sessions_completed": sessions_completed,
        "sessions_planned": sessions_planned,
        "compliance_rate": round(compliance_rate, 3),
    }


def _compute_intensity_distribution(
    matched_activities: list[Activity],
    planned_sessions: list[PlannedSession],
    reconciliation_results: list,
) -> dict[str, float]:
    """Compute intensity distribution (easy/moderate/hard percentages).

    Uses planned session intensity if available, otherwise falls back to
    activity-based heuristics (heart rate, duration, TSS).
    """
    intensity_counts = {"easy": 0, "moderate": 0, "hard": 0}

    # Build map of session_id -> planned session
    session_map = {s.id: s for s in planned_sessions}

    # Count by planned intensity (preferred)
    for recon_result in reconciliation_results:
        planned = session_map.get(recon_result.session_id)
        if planned and planned.intensity:
            intensity_lower = planned.intensity.lower()
            if intensity_lower in {"easy", "recovery", "rest"}:
                intensity_counts["easy"] += 1
            elif intensity_lower in {"moderate", "steady", "tempo"}:
                intensity_counts["moderate"] += 1
            elif intensity_lower in {"hard", "interval", "race", "threshold", "vo2max"}:
                intensity_counts["hard"] += 1

    # For sessions without planned intensity, use activity-based heuristics
    for recon_result in reconciliation_results:
        planned = session_map.get(recon_result.session_id)
        if planned and not planned.intensity and recon_result.matched_activity_id:
            # Find matched activity
            activity = next(
                (a for a in matched_activities if a.id == recon_result.matched_activity_id),
                None,
            )
            if activity:
                intensity = _classify_activity_intensity(activity)
                intensity_counts[intensity] += 1

    total = sum(intensity_counts.values())
    if total == 0:
        return {"easy_pct": 0.0, "moderate_pct": 0.0, "hard_pct": 0.0}

    return {
        "easy_pct": round(intensity_counts["easy"] / total * 100, 1),
        "moderate_pct": round(intensity_counts["moderate"] / total * 100, 1),
        "hard_pct": round(intensity_counts["hard"] / total * 100, 1),
    }


def _classify_activity_intensity(activity: Activity) -> Literal["easy", "moderate", "hard"]:
    """Classify activity intensity using heuristics.

    Falls back to duration-based heuristic if heart rate/TSS unavailable.
    """
    raw_data = activity.raw_json or {}

    # Try heart rate first
    avg_hr = raw_data.get("average_heartrate")
    if avg_hr:
        if avg_hr < 140:
            return "easy"
        if avg_hr < 165:
            return "moderate"
        return "hard"

    # Try TSS
    tss = activity.tss
    if tss is not None:
        duration_hours = (activity.duration_seconds or 0) / 3600.0
        if duration_hours > 0:
            tss_per_hour = tss / duration_hours
            if tss_per_hour < 50:
                return "easy"
            if tss_per_hour < 80:
                return "moderate"
            return "hard"

    # Fall back to duration-based heuristic
    duration_minutes = (activity.duration_seconds or 0) / 60
    if duration_minutes < 30:
        return "easy"
    if duration_minutes < 90:
        return "moderate"
    return "hard"


def _compute_load_trend(
    load_metrics: dict[str, float],
) -> Literal["increasing", "stable", "decreasing"]:
    """Compute load trend from CTL and ATL changes.

    Rule-based classification:
    - increasing: CTL ↑ AND ATL ↑
    - stable: small deltas
    - decreasing: CTL ↓
    """
    ctl_current = load_metrics.get("ctl_current", 0.0)
    ctl_start = load_metrics.get("ctl_start", 0.0)
    atl_current = load_metrics.get("atl_current", 0.0)
    atl_start = load_metrics.get("atl_start", 0.0)

    ctl_delta = ctl_current - ctl_start
    atl_delta = atl_current - atl_start

    # Threshold for "significant" change (5% or 5 points, whichever is larger)
    threshold = max(5.0, abs(ctl_start) * 0.05) if ctl_start != 0 else 5.0

    if ctl_delta > threshold and atl_delta > threshold:
        return "increasing"
    if ctl_delta < -threshold:
        return "decreasing"
    return "stable"


def _detect_anomalies(
    reconciliation_results: list,
    planned_sessions: list[PlannedSession],
    load_metrics: dict[str, float],
) -> list[str]:
    """Detect anomalies using rule-based thresholds.

    Returns short factual strings only when thresholds are crossed.
    """
    anomalies: list[str] = []

    # Build map for lookups
    session_map = {s.id: s for s in planned_sessions}

    # Check for consecutive missed sessions
    missed_dates = sorted([datetime.fromisoformat(r.date).date() for r in reconciliation_results if r.status == SessionStatus.MISSED])

    consecutive_misses = 0
    for i, missed_date in enumerate(missed_dates):
        if i == 0 or (missed_date - missed_dates[i - 1]).days == 1:
            consecutive_misses += 1
        else:
            consecutive_misses = 1

        if consecutive_misses >= 3:
            anomalies.append(f"{consecutive_misses} consecutive missed workouts")
            break

    # Check for missed sessions in 7-day window
    if len(missed_dates) >= 2:
        recent_misses = [d for d in missed_dates if (datetime.now(timezone.utc).date() - d).days <= 7]
        if len(recent_misses) >= 2:
            anomalies.append(f"{len(recent_misses)} missed workouts in last 7 days")

    # Check for hard days back-to-back
    hard_dates: list[date] = []
    for recon_result in reconciliation_results:
        planned = session_map.get(recon_result.session_id)
        if planned and planned.intensity:
            intensity_lower = planned.intensity.lower()
            if intensity_lower in {"hard", "interval", "race", "threshold", "vo2max"}:
                session_date = datetime.fromisoformat(recon_result.date).date()
                hard_dates.append(session_date)

    hard_dates_sorted = sorted(hard_dates)
    for i in range(len(hard_dates_sorted) - 1):
        if (hard_dates_sorted[i + 1] - hard_dates_sorted[i]).days == 1:
            anomalies.append("High intensity clustered on back-to-back days")
            break

    # Check for partial completions (long runs significantly shorter)
    for recon_result in reconciliation_results:
        if (
            recon_result.status == SessionStatus.PARTIAL
            and (planned := session_map.get(recon_result.session_id))
            and planned.duration_minutes
            and planned.duration_minutes >= 60
            and "shortfall" in recon_result.reason_code.value.lower()
        ):
            # This is a long session - check if it was significantly shorter
            # We'd need the actual activity to compute this, but for now
            # we can flag based on reason code
            anomalies.append("Long run completed at <70% planned duration")
            break

    # Check for ATL spike
    atl_current = load_metrics.get("atl_current", 0.0)
    atl_start = load_metrics.get("atl_start", 0.0)
    if atl_start > 0 and atl_current > 0:
        atl_change_pct = ((atl_current - atl_start) / abs(atl_start)) * 100
        if atl_change_pct > 30:
            anomalies.append("ATL spike >30% week-over-week")

    return anomalies


def _extract_key_sessions(
    planned_sessions: list[PlannedSession],
    reconciliation_results: list,
) -> list[KeySession]:
    """Extract up to 3 key sessions.

    Priority order:
    1. Long run
    2. Hard workout
    3. Most recent completed
    """
    session_map = {s.id: s for s in planned_sessions}
    recon_map = {r.session_id: r for r in reconciliation_results}

    key_sessions: list[KeySession] = []
    used_session_ids: set[str] = set()

    # Priority 1: Long run (duration >= 90 minutes or distance >= 15km)
    for planned in planned_sessions:
        if planned.id in recon_map and planned.id not in used_session_ids:
            recon = recon_map[planned.id]
            is_long = (planned.duration_minutes and planned.duration_minutes >= 90) or (planned.distance_km and planned.distance_km >= 15.0)
            if is_long and len(key_sessions) < 3:
                key_sessions.append(
                    KeySession(
                        date=recon.date,
                        title=planned.title or "",
                        status=recon.status.value,
                        matched_activity_id=recon.matched_activity_id,
                    )
                )
                used_session_ids.add(planned.id)

    # Priority 2: Hard workout
    if len(key_sessions) < 3:
        for planned in planned_sessions:
            if planned.id in recon_map and planned.id not in used_session_ids:
                recon = recon_map[planned.id]
                if planned.intensity and planned.intensity.lower() in {
                    "hard",
                    "interval",
                    "race",
                    "threshold",
                    "vo2max",
                }:
                    key_sessions.append(
                        KeySession(
                            date=recon.date,
                            title=planned.title or "",
                            status=recon.status.value,
                            matched_activity_id=recon.matched_activity_id,
                        )
                    )
                    used_session_ids.add(planned.id)
                    if len(key_sessions) >= 3:
                        break

    # Priority 3: Most recent completed
    if len(key_sessions) < 3:
        completed_results = [
            r for r in reconciliation_results if r.status == SessionStatus.COMPLETED and r.session_id not in used_session_ids
        ]
        completed_results.sort(key=lambda x: x.date, reverse=True)

        for recon in completed_results[: 3 - len(key_sessions)]:
            planned = session_map.get(recon.session_id)
            if planned:
                key_sessions.append(
                    KeySession(
                        date=recon.date,
                        title=planned.title or "",
                        status=recon.status.value,
                        matched_activity_id=recon.matched_activity_id,
                    )
                )
                used_session_ids.add(recon.session_id)

    # Sort by date (most recent first)
    key_sessions.sort(key=lambda x: x.date, reverse=True)

    return key_sessions[:3]


def _compute_reliability_flags(
    execution_metrics: dict[str, int | float],
    intensity_dist: dict[str, float],
    matched_activity_count: int,
) -> ReliabilityFlags:
    """Compute reliability flags based on data quality."""
    compliance_rate_value = execution_metrics.get("compliance_rate", 0.0)
    if isinstance(compliance_rate_value, int):
        compliance_rate = float(compliance_rate_value)
    elif isinstance(compliance_rate_value, float):
        compliance_rate = compliance_rate_value
    else:
        compliance_rate = 0.0
    low_compliance = compliance_rate < 0.6

    # High variance: intensity distribution is very skewed (>80% in one zone)
    max_intensity_pct = max(
        intensity_dist.get("easy_pct", 0.0),
        intensity_dist.get("moderate_pct", 0.0),
        intensity_dist.get("hard_pct", 0.0),
    )
    high_variance = max_intensity_pct > 80.0

    sparse_data = matched_activity_count < 3

    return ReliabilityFlags(
        low_compliance=low_compliance,
        high_variance=high_variance,
        sparse_data=sparse_data,
    )
