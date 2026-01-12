"""Volume allocator for structure-aware weekly volume distribution (B4).

This module implements pure mathematical volume allocation:
- Takes weekly volume and week structure
- Distributes volume across days based on session group ratios
- No planning logic, no intelligence, no repair
- Deterministic and reproducible

If input is invalid → raises VolumeAllocationError.
"""

from collections import defaultdict
from pathlib import Path

import yaml

from app.domains.training_plan.errors import VolumeAllocationError
from app.domains.training_plan.models import DaySkeleton, DistributedDay, WeekStructure

# Session type to DayType mapping (reverse of week_structure.py mapping)
# Used to map DayType back to possible session types
# Must match all session types in week_structure.py SESSION_TYPE_TO_DAY_TYPE
_SESSION_TYPE_TO_DAY_TYPE_MAP: dict[str, str] = {
    # Easy/Recovery sessions
    "easy": "easy",
    "easy_plus_strides": "easy",
    "easy_or_shakeout": "easy",
    "easy_or_marathon_touch": "easy",
    "easy_or_steady_short": "easy",
    "easy_or_light_fartlek": "easy",
    "easy_or_terrain_touch": "easy",
    "medium_easy": "easy",
    "recovery": "easy",
    "pre_race_shakeout": "easy",
    "aerobic": "easy",
    "aerobic_plus_strides": "easy",
    "aerobic_steady": "easy",
    "aerobic_steady_light": "easy",
    "aerobic_steady_or_climb": "easy",
    # Quality/Intensity sessions
    "threshold": "quality",
    "threshold_light": "quality",
    "threshold_double": "quality",
    "threshold_double_or_marathon": "quality",
    "threshold_or_marathon": "quality",
    "threshold_or_speed_endurance": "quality",
    "threshold_or_steady": "quality",
    "vo2": "quality",
    "vo2_light": "quality",
    "vo2_or_hill_reps": "quality",
    "vo2_or_speed": "quality",
    "speed_or_vo2": "quality",
    "marathon_pace": "quality",
    "marathon_pace_light": "quality",
    "economy_light": "quality",
    "economy_or_specific": "quality",
    "hill_strength_or_fartlek": "quality",
    "marathon_specific_or_progression": "quality",
    # Long runs
    "long": "long",
    "long_back_to_back": "long",
    "long_back_to_back_hike": "long",
    "long_mountain": "long",
    "long_progressive": "long",
    "long_specific": "long",
    "medium_long": "long",
    "moderate_long": "long",
    "short_long": "long",
    # Rest
    "rest": "rest",
    # Race
    "race": "race",
    "race_day": "race",
    # Cross/Terrain
    "cross": "cross",
    "downhill_economy_or_technical": "cross",
    "uphill_light": "cross",
    "uphill_strength_or_hike": "cross",
    "short_mountain": "cross",
    "short_specific": "cross",
}

_RAG_PATH = Path(__file__).resolve().parents[3] / "data" / "rag" / "session_group_ratios.yaml"


def _map_day_to_group(day_type_value: str, session_groups: dict[str, list[str]]) -> str | None:
    """Map a day's DayType to its session group.

    Args:
        day_type_value: DayType enum value (e.g., "easy", "quality", "long")
        session_groups: Session groups mapping from WeekStructure

    Returns:
        Group name if found, None otherwise
    """
    # Find all session types that map to this DayType
    matching_session_types = [
        session_type
        for session_type, mapped_day_type in _SESSION_TYPE_TO_DAY_TYPE_MAP.items()
        if mapped_day_type == day_type_value
    ]

    # Find which group contains any of these session types
    for group_name, group_session_types in session_groups.items():
        if any(st in group_session_types for st in matching_session_types):
            return group_name

    return None


def allocate_week_volume(
    weekly_distance: float,
    structure: WeekStructure,
) -> list[DistributedDay]:
    """Allocate weekly volume across days based on structure and session group ratios.

    Algorithm (MANDATED):
    1. Map each day → its session group
    2. Count days per group
    3. Compute raw group volume using ratios
    4. Normalize so total == weekly volume
    5. Divide group volume evenly across days
    6. Round to 0.1
    7. Assign rounding drift to long run

    Args:
        weekly_distance: Total weekly distance (must be > 0)
        structure: WeekStructure with days, session_groups, etc.

    Returns:
        List of DistributedDay with allocated distances

    Raises:
        VolumeAllocationError: If allocation fails or inputs are invalid
    """
    if weekly_distance <= 0:
        raise VolumeAllocationError("Weekly distance must be positive")

    if not _RAG_PATH.exists():
        raise VolumeAllocationError("Session group ratio RAG missing")

    with _RAG_PATH.open() as f:
        ratios = yaml.safe_load(f)

    if not isinstance(ratios, dict):
        raise VolumeAllocationError("Invalid session group ratio RAG format")

    # Step 1: Map day -> group
    # Skip race days and rest days - they don't need volume allocation
    day_to_group: dict[int, str] = {}
    for day in structure.days:
        # Race days: skip volume allocation (race distance is determined by the race itself)
        if day.day_type.value == "race":
            continue
        # Rest days: skip volume allocation (no training)
        if day.day_type.value == "rest":
            continue

        group = _map_day_to_group(day.day_type.value, structure.session_groups)
        if group is None:
            raise VolumeAllocationError(
                f"Day type '{day.day_type.value}' not found in any session group"
            )
        day_to_group[day.day_index] = group

    if not day_to_group:
        raise VolumeAllocationError("No session groups resolved")

    # Step 2: Group days
    group_days: dict[str, list[DaySkeleton]] = defaultdict(list)
    for day in structure.days:
        group = day_to_group.get(day.day_index)
        if group:
            group_days[group].append(day)

    # Step 3: Compute group ratios
    raw: dict[str, float] = {}
    for group in group_days:
        group_ratios = ratios.get(group)
        if group_ratios is None:
            raise VolumeAllocationError(f"No ratio for group '{group}'")
        if not isinstance(group_ratios, dict):
            raise VolumeAllocationError(f"Invalid ratio format for group '{group}'")
        ratio = group_ratios.get("default")
        if ratio is None:
            raise VolumeAllocationError(f"No ratio for group '{group}'")
        if not isinstance(ratio, (int, float)):
            raise VolumeAllocationError(f"Invalid ratio type for group '{group}'")
        raw[group] = float(ratio)

    total_ratio = sum(raw.values())
    if total_ratio <= 0:
        raise VolumeAllocationError("Invalid group ratios")

    # Step 4: Normalize
    normalized = {g: (r / total_ratio) * weekly_distance for g, r in raw.items()}

    # Step 5: Allocate per day
    allocations: dict[int, float] = {}
    for group, days in group_days.items():
        if len(days) == 0:
            continue
        per_day = normalized[group] / len(days)
        for d in days:
            allocations[d.day_index] = per_day

    # Step 6: Round
    rounded: dict[int, float] = {k: round(v, 1) for k, v in allocations.items()}
    drift = round(weekly_distance - sum(rounded.values()), 1)

    # Step 7: Drift correction
    # Prefer long run, fallback to highest volume day if no long run exists
    long_days = [
        day_index for day_index, group in day_to_group.items() if group == "long"
    ]

    if long_days:
        # Assign drift to first long run day (preferred)
        drift_day_index = long_days[0]
    else:
        # Fallback: assign drift to day with highest volume
        if not rounded:
            raise VolumeAllocationError("No days available for drift correction")
        drift_day_index = max(rounded.items(), key=lambda x: x[1])[0]

    rounded[drift_day_index] = round(rounded[drift_day_index] + drift, 1)

    # Final validation
    total_allocated = round(sum(rounded.values()), 1)
    if total_allocated != round(weekly_distance, 1):
        raise VolumeAllocationError(
            f"Volume mismatch after allocation: {total_allocated} != {weekly_distance}"
        )

    # Build final allocation - race and rest days get 0 distance
    return [
        DistributedDay(
            day_index=day.day_index,
            day_type=day.day_type,
            distance=(
                0.0
                if day.day_type.value in {"race", "rest"}
                else rounded.get(day.day_index, 0.0)
            ),
        )
        for day in structure.days
    ]
