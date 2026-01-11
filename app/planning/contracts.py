"""Planning Contracts - Field Contracts & LLM Restrictions.

This module defines:
1. PRIMARY vs DERIVED vs FORBIDDEN fields
2. What LLMs are allowed and forbidden to do

🔒 These contracts are enforced by validators and tests.
"""

# ---- Field Contracts ----

# PRIMARY fields - these are allocated/computed by the planner
PLANNER_PRIMARY_FIELDS: set[str] = {
    "duration_minutes",
    "total_duration_min",
    "weekly_duration_targets_min",
    "min_duration_min",
    "max_duration_min",
}

# DERIVED fields - these are computed from PRIMARY fields
PLANNER_DERIVED_FIELDS: set[str] = {
    "distance_miles",
    "total_distance_miles",
}

# FORBIDDEN fields - these must NEVER appear in planner schemas
FORBIDDEN_PLANNER_FIELDS: set[str] = {
    "weekly_mileage",
    "distance_km",
    "total_miles",  # Use total_distance_miles (DERIVED) instead
    "miles",  # Use distance_miles (DERIVED) instead
}

# ---- LLM Action Restrictions ----

# Actions LLMs are allowed to perform
LLM_ALLOWED_ACTIONS: set[str] = {
    "SELECT_TEMPLATE_IDS",
    "GENERATE_TEXT_EXPLANATION",
}

# Actions LLMs are forbidden to perform (must be done by code)
LLM_FORBIDDEN_ACTIONS: set[str] = {
    "COMPUTE_MILES",
    "COMPUTE_TOTALS",
    "ALLOCATE_VOLUME",
    "CREATE_STRUCTURE",
    "COMPUTE_DURATION",  # Added: LLMs cannot compute duration
    "COMPUTE_DISTANCE",  # Added: LLMs cannot compute distance
}
