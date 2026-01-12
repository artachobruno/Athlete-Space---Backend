"""Structure specification validator.

This module validates structure specifications to ensure they meet all
structural invariants. This is the MOST IMPORTANT validation layer.

Validation rules:
- Week shape: exactly 7 days with valid day names
- Hard day logic: count <= hard_days_max, no consecutive hard days
- Long run enforcement: required_count matches actual count
- Phase overrides: taper-specific rules
"""

from app.planning.structure.types import StructureSpec


class StructureValidationError(RuntimeError):
    """Raised when structure validation fails.

    Attributes:
        code: Error code
        structure_id: Structure ID that failed validation
        details: List of error detail strings
    """

    def __init__(self, code: str, structure_id: str, details: list[str]) -> None:
        self.code = code
        self.structure_id = structure_id
        self.details = details
        message = f"{code} (structure={structure_id}): {'; '.join(details)}"
        super().__init__(message)


_VALID_DAYS = {"mon", "tue", "wed", "thu", "fri", "sat", "sun"}


def _validate_week_shape(spec: StructureSpec) -> None:
    """Validate week pattern has exactly 7 valid days.

    Args:
        spec: Structure specification

    Raises:
        StructureValidationError: If week shape is invalid
    """
    week_pattern = spec.week_pattern

    # Must have exactly 7 days
    if len(week_pattern) != 7:
        raise StructureValidationError(
            "INVALID_WEEK_SHAPE",
            spec.metadata.id,
            [f"week_pattern must have exactly 7 days, got {len(week_pattern)}"],
        )

    # All days must be valid
    invalid_days = [day for day in week_pattern if day not in _VALID_DAYS]
    if invalid_days:
        raise StructureValidationError(
            "INVALID_DAY_NAMES",
            spec.metadata.id,
            [
                f"Invalid day names in week_pattern: {invalid_days}. "
                f"Valid days are: {sorted(_VALID_DAYS)}"
            ],
        )

    # Check all valid days are present
    missing_days = _VALID_DAYS - set(week_pattern.keys())
    if missing_days:
        raise StructureValidationError(
            "MISSING_DAYS",
            spec.metadata.id,
            [f"Missing days in week_pattern: {sorted(missing_days)}"],
        )


def _count_hard_days(spec: StructureSpec) -> list[str]:
    """Count hard days in week pattern.

    Args:
        spec: Structure specification

    Returns:
        List of day names that are hard days
    """
    hard_sessions = set(spec.session_groups.get("hard", []))
    return [day for day, session in spec.week_pattern.items() if session in hard_sessions]


def _validate_hard_day_logic(spec: StructureSpec) -> None:
    """Validate hard day count and consecutive day rules.

    Args:
        spec: Structure specification

    Raises:
        StructureValidationError: If hard day logic is violated
    """
    rules = spec.rules

    # Get hard_days_max
    hard_days_max = rules.get("hard_days_max")
    if hard_days_max is None:
        return  # No constraint

    if not isinstance(hard_days_max, int):
        raise StructureValidationError(
            "INVALID_HARD_DAYS_MAX",
            spec.metadata.id,
            [f"hard_days_max must be an integer, got {type(hard_days_max)}"],
        )

    # Count hard days
    hard_days = _count_hard_days(spec)
    hard_days_count = len(hard_days)

    # Validate count
    if hard_days_count > hard_days_max:
        raise StructureValidationError(
            "TOO_MANY_HARD_DAYS",
            spec.metadata.id,
            [
                f"Found {hard_days_count} hard days ({hard_days}), "
                f"but hard_days_max is {hard_days_max}"
            ],
        )

    # Check consecutive hard days
    no_consecutive = rules.get("no_consecutive_hard_days", False)
    if no_consecutive and hard_days_count > 1:
        # Convert days to ordered list for checking adjacency
        day_order = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]
        hard_day_indices = [day_order.index(day) for day in hard_days if day in day_order]

        # Check adjacent days (including wrap-around: sun -> mon)
        for i, idx in enumerate(hard_day_indices):
            next_idx = hard_day_indices[(i + 1) % len(hard_day_indices)]
            # Adjacent in week (next day) or wrap-around (sun -> mon)
            if next_idx == (idx + 1) % 7:
                day1 = day_order[idx]
                day2 = day_order[next_idx]
                raise StructureValidationError(
                    "CONSECUTIVE_HARD_DAYS",
                    spec.metadata.id,
                    [f"Consecutive hard days found: {day1} and {day2}"],
                )


def _count_long_run_days(spec: StructureSpec) -> list[str]:
    """Count long run days in week pattern.

    Args:
        spec: Structure specification

    Returns:
        List of day names that are long run days
    """
    long_sessions = set(spec.session_groups.get("long", []))
    return [day for day, session in spec.week_pattern.items() if session in long_sessions]


def _validate_long_run_enforcement(spec: StructureSpec) -> None:
    """Validate long run required_count matches actual count.

    Args:
        spec: Structure specification

    Raises:
        StructureValidationError: If long run count mismatch
    """
    rules = spec.rules
    long_run_config = rules.get("long_run")

    if not isinstance(long_run_config, dict):
        return  # No long_run configuration

    required_count = long_run_config.get("required_count")
    if required_count is None:
        return  # No required count

    if not isinstance(required_count, int):
        raise StructureValidationError(
            "INVALID_LONG_RUN_COUNT",
            spec.metadata.id,
            [f"long_run.required_count must be an integer, got {type(required_count)}"],
        )

    # Count actual long run days
    long_days = _count_long_run_days(spec)
    actual_count = len(long_days)

    # Must match exactly
    if actual_count != required_count:
        raise StructureValidationError(
            "LONG_RUN_COUNT_MISMATCH",
            spec.metadata.id,
            [
                f"Expected {required_count} long run(s), found {actual_count}. "
                f"Long run days: {long_days if long_days else 'none'}"
            ],
        )


def _validate_phase_overrides(spec: StructureSpec) -> None:
    """Validate phase-specific rules (e.g., taper constraints).

    Args:
        spec: Structure specification

    Raises:
        StructureValidationError: If phase overrides are violated
    """
    phase = spec.metadata.phase

    # Taper-specific rules
    if phase == "taper":
        rules = spec.rules

        # long_run.required_count must be 0 or 1 for taper
        long_run_config = rules.get("long_run")
        if isinstance(long_run_config, dict):
            required_count = long_run_config.get("required_count")
            if isinstance(required_count, int) and required_count > 1:
                raise StructureValidationError(
                    "TAPER_LONG_RUN_TOO_MANY",
                    spec.metadata.id,
                    [
                        f"Taper phase requires long_run.required_count <= 1, "
                        f"got {required_count}"
                    ],
                )

        # hard_days_max <= 1 for taper
        hard_days_max = rules.get("hard_days_max")
        if isinstance(hard_days_max, int) and hard_days_max > 1:
            raise StructureValidationError(
                "TAPER_HARD_DAYS_TOO_MANY",
                spec.metadata.id,
                [f"Taper phase requires hard_days_max <= 1, got {hard_days_max}"],
            )


def validate_structure(spec: StructureSpec) -> StructureSpec:
    """Validate a structure specification.

    This is the MOST IMPORTANT validation layer. It ensures all structural
    invariants are met before the structure is used in planning.

    Args:
        spec: Structure specification to validate

    Returns:
        The same structure specification (unchanged, for chaining)

    Raises:
        StructureValidationError: If validation fails
    """
    # Validate week shape
    _validate_week_shape(spec)

    # Validate hard day logic
    _validate_hard_day_logic(spec)

    # Validate long run enforcement
    _validate_long_run_enforcement(spec)

    # Validate phase overrides
    _validate_phase_overrides(spec)

    # Return unchanged (validation only, no modification)
    return spec
