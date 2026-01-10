"""Tool name constants.

Central definition of all tool names used throughout the application.
This provides a single source of truth and prevents typos.
"""

# Tool names for coaching actions
PLAN_RACE_BUILD = "plan_race_build"
PLAN_WEEK = "plan_week"
PLAN_SEASON = "plan_season"
RECOMMEND_NEXT_SESSION = "recommend_next_session"
ADD_WORKOUT = "add_workout"
ADJUST_TRAINING_LOAD = "adjust_training_load"
EXPLAIN_TRAINING_STATE = "explain_training_state"

# All valid tool names (for validation)
VALID_TOOL_NAMES = {
    PLAN_RACE_BUILD,
    PLAN_WEEK,
    PLAN_SEASON,
    RECOMMEND_NEXT_SESSION,
    ADD_WORKOUT,
    ADJUST_TRAINING_LOAD,
    EXPLAIN_TRAINING_STATE,
}
