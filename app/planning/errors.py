"""Canonical Planning Error Types.

This module defines standard error types for planning invariant violations.
No raw RuntimeErrors should be used - all planning errors must use these types.

Standard error codes:
- MISSING_LONG_RUN: Week must contain exactly one long run
- TOO_MANY_HARD_DAYS: Week exceeds maximum hard days per week
- ADJACENT_HARD_DAYS: Hard days are too close together
- INVALID_WEEKLY_TIME: Weekly duration (minutes) does not match target
- EXECUTION_WITH_EMPTY_SLOTS: Execution attempted without required slots
"""


class PlanningInvariantError(RuntimeError):
    """Raised when a planning invariant is violated.

    Attributes:
        code: Error code (e.g., "MISSING_LONG_RUN", "TOO_MANY_HARD_DAYS", "INVALID_WEEKLY_TIME")
        details: List of error detail strings
    """

    def __init__(self, code: str, details: list[str]):
        self.code = code
        self.details = details
        super().__init__(f"{code}: {details}")
