"""Climate expectation copy for outdoor aerobic sessions.

Computes one-line primary + optional detail (numbers behind affordance).
Used only for outdoor aerobic activities with climate data.
"""

from __future__ import annotations

from app.coach.tools.climate_context import convert_activity_performance_for_conditions
from app.db.models import Activity

_RESULT = dict[str, str | None] | None


def _is_indoor(activity: Activity) -> bool:
    """True if activity has no GPS (indoor)."""
    streams = activity.metrics.get("streams_data") if activity.metrics else None
    if not streams:
        return True
    latlng = streams.get("latlng")
    if isinstance(latlng, dict) and "data" in latlng:
        data = latlng["data"]
    elif isinstance(latlng, list):
        data = latlng
    else:
        data = []
    return not data or len(data) == 0


def _is_interval_or_race(activity: Activity) -> bool:
    """True if title suggests interval or race."""
    title = (activity.title or "").lower()
    return "interval" in title or "race" in title


def _primary_line(label: str) -> str:
    """Primary one-line copy from conditions label."""
    if label == "Hot & Humid":
        return (
            "Warm, humid conditions today. "
            "This effort likely felt harder than pace alone suggests."
        )
    if label == "Hot":
        return (
            "Warm conditions today. "
            "This effort likely felt harder than pace alone suggests."
        )
    if label == "Warm":
        return (
            "Warm conditions today. "
            "This effort likely felt harder than pace alone suggests."
        )
    if label == "Cool":
        return (
            "Cool, dry conditions today. "
            "These conditions tend to support efficient pacing."
        )
    return ""


def _detail_hot() -> str:
    """Detail string for hot conditions (numbers hidden behind affordance)."""
    return "In similar conditions, steady efforts often feel ~10-20 sec/mi slower."


def _detail_cool() -> str:
    """Detail string for cool conditions (numbers hidden behind affordance)."""
    return "In cooler conditions, steady efforts may feel ~5-10 sec/mi easier."


def _equivalency_available(activity: Activity, duration_min: float) -> bool:
    """True if we can compute heat equivalency (pace + duration, adjustment >= 2%)."""
    if activity.sport not in {"run", "ride"}:
        return False
    dist = activity.distance_meters
    if not dist or dist <= 0:
        return False
    pace_sec_per_km = 1000.0 * float(activity.duration_seconds) / float(dist)
    hsi = activity.heat_stress_index
    ehi = activity.effective_heat_stress_index
    if hsi is None:
        return False
    hsi_val = float(hsi)
    ehi_val = float(ehi) if ehi is not None else None
    equiv = convert_activity_performance_for_conditions(
        sport=activity.sport,
        observed_pace_sec_per_km=pace_sec_per_km,
        heat_stress_index=hsi_val,
        duration_min=duration_min,
        effective_heat_stress_index=ehi_val,
    )
    adj = equiv.get("adjustment_pct")
    if adj is None:
        return False
    return float(adj) >= 2.0


def _can_show_cool_detail(activity: Activity) -> bool:
    """True if we have pace + duration for cool detail (no heat equivalency)."""
    if activity.sport not in {"run", "ride"}:
        return False
    dist = activity.distance_meters
    return bool(dist and dist > 0 and activity.duration_seconds >= 1800)


def generate_climate_expectation(activity: Activity) -> _RESULT:
    """Compute expectation copy for outdoor aerobic sessions.

    Returns:
        {"primary": str, "detail": str | None} or None.

    None when:
    - not activity.has_climate_data
    - conditions_label == "Mild"
    - indoor or < 30 min
    - interval/race (title heuristic)
    - sport not run/ride
    """
    if not activity.has_climate_data:
        return None
    label = activity.conditions_label
    if not label or label == "Mild":
        return None
    if activity.sport not in {"run", "ride"}:
        return None
    if activity.duration_seconds < 1800:
        return None
    if _is_indoor(activity):
        return None
    if _is_interval_or_race(activity):
        return None

    duration_min = activity.duration_seconds / 60.0
    primary = _primary_line(label)
    if not primary:
        return None

    detail: str | None = None
    if label in {"Hot", "Hot & Humid", "Warm"} and _equivalency_available(
        activity, duration_min
    ):
        detail = _detail_hot()
    elif label == "Cool" and _can_show_cool_detail(activity):
        detail = _detail_cool()

    return {"primary": primary, "detail": detail}
