"""WeekSkeleton for hierarchical planner.

Deterministic week structure that guarantees:
- Exactly one long run
- At most 2 hard sessions
- Proper spacing

This skeleton is created BEFORE LLM generation.
LLM only fills in details (distance, pace, description).
"""

from dataclasses import dataclass

from loguru import logger

from app.planning.schema.session_spec import SessionSpec, SessionType


@dataclass(frozen=True)
class WeekSkeleton:
    """Week structure definition for hierarchical planner.

    Maps day_of_week (0-6, where 0=Monday) to SessionType.
    This ensures structural invariants are enforced before LLM generation.

    Attributes:
        days: Dictionary mapping day_of_week (int 0-6) to SessionType
    """

    days: dict[int, SessionType]

    def __hash__(self) -> int:
        """Make skeleton hashable for immutability checks."""
        return hash(tuple(sorted(self.days.items())))


def generate_week_skeleton(input) -> WeekSkeleton:
    """Generate a deterministic week skeleton from PlanWeekInput.

    Creates a skeleton with guaranteed invariants:
    - Exactly one long run (placed on Sunday by default, or last available day)
    - At most 2 hard sessions (placed on Tuesday/Thursday if available)
    - Remaining days filled with easy sessions
    - Only uses days in days_available

    Args:
        input: PlanWeekInput with week parameters

    Returns:
        WeekSkeleton with guaranteed structure
    """
    days: dict[int, SessionType] = {}
    available = sorted(input.days_available)

    if not available:
        raise ValueError("No days available for week skeleton")

    # Place long run on last available day (typically Sunday)
    long_day = available[-1]
    days[long_day] = SessionType.LONG

    # Place hard sessions on Tuesday/Thursday if available
    hard_days = [1, 3]  # Tuesday, Thursday
    hard_count = 0
    for day in hard_days:
        if day in available and day != long_day and hard_count < 2:
            days[day] = SessionType.TEMPO  # Default hard session type
            hard_count += 1

    # Fill remaining available days with easy sessions
    for day in available:
        if day not in days:
            days[day] = SessionType.EASY

    skeleton = WeekSkeleton(days=days)

    # Validate skeleton invariants
    long_count = sum(1 for st in days.values() if st == SessionType.LONG)
    if long_count != 1:
        raise ValueError(f"Week skeleton must contain exactly one long run, got {long_count}")

    logger.debug(
        "Generated week skeleton",
        week_number=input.week_number,
        day_count=len(days),
        long_day=long_day,
        hard_days=[d for d, st in days.items() if st in {SessionType.TEMPO, SessionType.THRESHOLD, SessionType.VO2}],
    )

    return skeleton


def validate_skeleton_match(specs: list[SessionSpec], skeleton: WeekSkeleton) -> None:
    """Validate that SessionSpecs match the skeleton structure.

    Ensures immutability: LLM cannot change session types defined by skeleton.

    Args:
        specs: List of SessionSpec objects from LLM
        skeleton: WeekSkeleton that defines required structure

    Raises:
        ValueError: If specs don't match skeleton structure
    """
    # Extract session types from specs by day
    spec_types: dict[int, SessionSpec] = {}
    for spec in specs:
        if spec.day_of_week in spec_types:
            raise ValueError(f"Duplicate session on day {spec.day_of_week}")
        spec_types[spec.day_of_week] = spec

    # Check that skeleton days match spec session types
    for day, expected_type in skeleton.days.items():
        if day not in spec_types:
            raise ValueError(f"Skeleton requires session on day {day}, but LLM output has none")
        actual_type = spec_types[day].session_type
        if actual_type != expected_type:
            raise ValueError(
                f"Skeleton mismatch on day {day}: skeleton requires {expected_type.value}, "
                f"LLM output has {actual_type.value}"
            )

    # Check that all specs have corresponding skeleton days (no extra sessions)
    for day in spec_types:
        if day not in skeleton.days:
            raise ValueError(f"LLM output has session on day {day}, but skeleton doesn't include it")

    # Verify skeleton hash matches (immutability check)
    skeleton_hash = hash(skeleton)
    extracted_skeleton = WeekSkeleton(days={day: spec.session_type for day, spec in spec_types.items()})
    extracted_hash = hash(extracted_skeleton)

    if skeleton_hash != extracted_hash:
        raise ValueError(
            f"Skeleton immutability violation: expected hash {skeleton_hash}, got {extracted_hash}"
        )
