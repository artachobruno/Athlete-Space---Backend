"""Climate aggregation job.

Aggregates raw climate samples into activity-level summaries.
Updates activities table with climate summary columns.

Uses exact heat stress formula (v1.0):
- temp_stress = clamp((avg_temp_c - 10) / 25, 0.0, 1.0)
- dew_stress = clamp((avg_dew_point_c - 10) / 15, 0.0, 1.0)
- heat_stress_index = clamp(0.6 * temp_stress + 0.4 * dew_stress, 0.0, 1.0)

TSS adjustment (if applicable):
- heat_load_multiplier = 1 + min(0.10, heat_stress_index * 0.08)
- Max TSS inflation: +10%
- Only applies to running/cycling, aerobic steady efforts, duration ≥ 30 min
"""

from __future__ import annotations

from loguru import logger
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.db.models import Activity


def compute_heat_stress_index(avg_temp_c: float, avg_dew_point_c: float) -> float:
    """Compute heat stress index from temperature and dew point.

    Uses exact formula (v1.0):
    - temp_stress = clamp((avg_temp_c - 10) / 25, 0.0, 1.0)
    - dew_stress = clamp((avg_dew_point_c - 10) / 15, 0.0, 1.0)
    - heat_stress_index = clamp(0.6 * temp_stress + 0.4 * dew_stress, 0.0, 1.0)

    Args:
        avg_temp_c: Average temperature in Celsius
        avg_dew_point_c: Average dew point in Celsius

    Returns:
        Heat stress index (0.0 = no stress, 1.0 = extreme stress)
    """
    # Step 1: Normalize temperature stress
    # ≤10°C → no heat stress, ≥35°C → max temperature stress
    temp_stress = max(0.0, min(1.0, (avg_temp_c - 10.0) / 25.0))

    # Step 2: Normalize moisture stress (dew point)
    # ≤10°C dew point → dry, ≥25°C dew point → oppressive humidity
    dew_stress = max(0.0, min(1.0, (avg_dew_point_c - 10.0) / 15.0))

    # Step 3: Combine into Heat Stress Index
    # Temperature dominates (60%), dew point amplifies (40%)
    return max(0.0, min(1.0, 0.6 * temp_stress + 0.4 * dew_stress))


def generate_conditions_label(heat_stress_index: float) -> str:
    """Generate human-readable conditions label from heat stress index.

    Uses exact ranges (v1.0):
    - < 0.20 → Cool
    - 0.20 - 0.39 → Mild
    - 0.40 - 0.59 → Warm
    - 0.60 - 0.74 → Hot
    - ≥ 0.75 → Hot & Humid

    Args:
        heat_stress_index: Heat stress index (0.0-1.0)

    Returns:
        Conditions label
    """
    if heat_stress_index < 0.20:
        return "Cool"
    if heat_stress_index < 0.40:
        return "Mild"
    if heat_stress_index < 0.60:
        return "Warm"
    if heat_stress_index < 0.75:
        return "Hot"
    return "Hot & Humid"


def aggregate_activity_climate(session: Session, activity: Activity) -> bool:
    """Aggregate climate samples for an activity and update activity record.

    Args:
        session: Database session
        activity: Activity record

    Returns:
        True if aggregation was successful, False otherwise
    """
    try:
        # Fetch all samples for this activity
        result = session.execute(
            text(
                """
                SELECT
                    AVG(temperature_c) as avg_temp,
                    MAX(temperature_c) as max_temp,
                    AVG(dew_point_c) as avg_dew_point,
                    MAX(dew_point_c) as max_dew_point,
                    AVG(wind_speed_mps) as avg_wind,
                    SUM(precip_mm) as total_precip,
                    COUNT(*) as sample_count
                FROM activity_climate_samples
                WHERE activity_id = :activity_id
                """
            ),
            {"activity_id": activity.id},
        ).fetchone()

        if not result or result.sample_count == 0:
            logger.debug(f"[CLIMATE_AGG] No samples found for activity {activity.id}")
            return False

        avg_temp = float(result.avg_temp) if result.avg_temp is not None else None
        max_temp = float(result.max_temp) if result.max_temp is not None else None
        avg_dew_point = float(result.avg_dew_point) if result.avg_dew_point is not None else None
        max_dew_point = float(result.max_dew_point) if result.max_dew_point is not None else None
        avg_wind = float(result.avg_wind) if result.avg_wind is not None else None
        total_precip = float(result.total_precip) if result.total_precip is not None else 0.0

        if avg_temp is None or avg_dew_point is None:
            logger.debug(f"[CLIMATE_AGG] Incomplete climate data for activity {activity.id}")
            return False

        # Compute heat stress index (exact formula v1.0)
        heat_stress = compute_heat_stress_index(avg_temp, avg_dew_point)

        # Generate conditions label from heat stress index
        conditions_label = generate_conditions_label(heat_stress)

        # Compute TSS adjustment if applicable
        heat_tss_adjustment_pct = None
        adjusted_tss = None
        if _should_apply_tss_adjustment(activity):
            heat_load_multiplier = 1.0 + min(0.10, heat_stress * 0.08)
            if activity.tss is not None:
                adjusted_tss = activity.tss * heat_load_multiplier
                heat_tss_adjustment_pct = (heat_load_multiplier - 1.0) * 100.0

        # Update activity record
        session.execute(
            text(
                """
                UPDATE activities SET
                    has_climate_data = TRUE,
                    avg_temperature_c = :avg_temp,
                    max_temperature_c = :max_temp,
                    avg_dew_point_c = :avg_dew_point,
                    max_dew_point_c = :max_dew_point,
                    wind_avg_mps = :avg_wind,
                    precip_total_mm = :total_precip,
                    heat_stress_index = :heat_stress,
                    conditions_label = :conditions_label,
                    heat_tss_adjustment_pct = :heat_tss_adjustment_pct,
                    adjusted_tss = :adjusted_tss,
                    climate_model_version = :climate_model_version
                WHERE id = :activity_id
                """
            ),
            {
                "activity_id": activity.id,
                "avg_temp": avg_temp,
                "max_temp": max_temp,
                "avg_dew_point": avg_dew_point,
                "max_dew_point": max_dew_point,
                "avg_wind": avg_wind,
                "total_precip": total_precip,
                "heat_stress": heat_stress,
                "conditions_label": conditions_label,
                "heat_tss_adjustment_pct": heat_tss_adjustment_pct,
                "adjusted_tss": adjusted_tss,
                "climate_model_version": "v1.0",
            },
        )

        # Observability: Log climate aggregation once per activity
        logger.info(
            f"[CLIMATE_AGG] Aggregated climate for activity {activity.id}: "
            f"label={conditions_label}, heat_stress={heat_stress:.2f}, samples={result.sample_count}"
        )

        # Structured log for observability
        climate_log = {
            "activity_id": activity.id,
            "heat_stress_index": round(heat_stress, 2),
            "conditions_label": conditions_label,
            "tss_adjustment_pct": round(heat_tss_adjustment_pct, 1) if heat_tss_adjustment_pct else None,
            "climate_model_version": "v1.0",
        }
        logger.info(f"[CLIMATE_OBSERVABILITY] {climate_log}")

    except Exception as e:
        logger.warning(f"[CLIMATE_AGG] Failed to aggregate climate for activity {activity.id}: {e}")
        return False
    else:
        return True


def _should_apply_tss_adjustment(activity: Activity) -> bool:
    """Check if TSS adjustment should be applied to activity.

    Applies ONLY to:
    - Running or cycling
    - Aerobic steady efforts (not intervals/races)
    - Duration ≥ 30 min

    Does NOT apply to:
    - Intervals
    - Sprints
    - Races
    - Strength
    - HIIT

    Args:
        activity: Activity record

    Returns:
        True if TSS adjustment should be applied
    """
    # Only running and cycling
    if activity.sport not in {"run", "ride"}:
        return False

    # Must be at least 30 minutes
    if not activity.duration_seconds or activity.duration_seconds < 30 * 60:
        return False

    # Check if it's a race or interval session
    # This is a heuristic - we can refine based on activity title/notes
    title_lower = (activity.title or "").lower()
    notes_lower = (activity.notes or "").lower()

    # Skip races
    race_keywords = ["race", "competition", "event"]
    has_race_keyword = any(keyword in title_lower or keyword in notes_lower for keyword in race_keywords)

    # Skip obvious interval sessions (heuristic)
    interval_keywords = ["interval", "sprint", "hiit", "tabata"]
    has_interval_keyword = any(keyword in title_lower or keyword in notes_lower for keyword in interval_keywords)

    # Default: apply to steady aerobic efforts (not races or intervals)
    return not (has_race_keyword or has_interval_keyword)


def aggregate_climate_for_activities_with_samples(session: Session, limit: int = 100) -> int:
    """Aggregate climate for activities that have samples but no summary.

    Args:
        session: Database session
        limit: Maximum number of activities to process

    Returns:
        Number of activities successfully aggregated
    """
    # Find activities with samples but no summary
    result = session.execute(
        text(
            """
            SELECT DISTINCT a.id
            FROM activities a
            INNER JOIN activity_climate_samples s ON s.activity_id = a.id
            WHERE a.has_climate_data IS FALSE OR a.has_climate_data IS NULL
            LIMIT :limit
            """
        ),
        {"limit": limit},
    ).fetchall()

    activity_ids = [row[0] for row in result]

    if not activity_ids:
        logger.info("[CLIMATE_AGG] No activities found needing aggregation")
        return 0

    logger.info(f"[CLIMATE_AGG] Aggregating climate for {len(activity_ids)} activities")

    success_count = 0
    for activity_id in activity_ids:
        activity = session.execute(select(Activity).where(Activity.id == activity_id)).scalar_one_or_none()
        if activity and aggregate_activity_climate(session, activity):
            success_count += 1

    session.commit()
    logger.info(f"[CLIMATE_AGG] Successfully aggregated climate for {success_count}/{len(activity_ids)} activities")
    return success_count
