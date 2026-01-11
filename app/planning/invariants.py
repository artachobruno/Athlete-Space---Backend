"""Global Planning Invariants - Single Source of Truth.

This module defines non-negotiable planning invariants that must be
enforced before ANY calendar write. Every validator, allocator, and
executor must import from here - nowhere else.

🔐 Rule: Every validator, allocator, and executor must import from here — nowhere else.

ARCHITECTURAL COMMITMENT: TIME-BASED PLANNING
=============================================
The system commits to TIME (minutes) as the internal planning currency.
Distance (miles) is ALWAYS derived from: distance_miles = duration_minutes x pace_min_per_mile

No planner component is allowed to invent distance.
Distance is computed deterministically from time + pace.
"""

# Maximum hard days per week by race type
MAX_HARD_DAYS_PER_WEEK: dict[str, int] = {
    "default": 2,
    "5k": 2,
    "10k": 2,
    "half": 2,
    "marathon": 2,
}

# Long run requirements
LONG_RUN_REQUIRED = True
LONG_RUNS_PER_WEEK = 1

# Weekly time-based constraints (PRIMARY)
# Time is the internal planning currency - distance is derived
MAX_WEEKLY_TIME_DELTA_PCT = 0.02  # ±2% tolerance for weekly duration (minutes)
MAX_WEEKLY_INCREASE_PCT = 0.10  # Maximum 10% increase week-over-week

# Derived distance tolerance (for display/validation only, not planning)
# Distance is computed from time + pace, this is just for rounding checks
DERIVED_DISTANCE_TOLERANCE = 0.01  # 0.01 miles rounding tolerance

# Hard day spacing constraints
MIN_EASY_DAY_GAP_BETWEEN_HARD = 1  # At least 1 easy day between hard days

# Taper constraints
TAPER_MIN_WEEKS = 1
