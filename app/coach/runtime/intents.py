from enum import StrEnum


class CoachIntent(StrEnum):
    """Coaching intent classification enum.

    This enum represents all possible intents that the coach can handle.
    Used by the intent router to classify user messages.
    """

    NEXT_SESSION = "next_session"
    ADJUST_LOAD = "adjust_load"
    EXPLAIN_STATE = "explain_state"
    PLAN_RACE = "plan_race"
    PLAN_SEASON = "plan_season"
    PLAN_WEEK = "plan_week"
    ADD_WORKOUT = "add_workout"
    RUN_ANALYSIS = "run_analysis"
    SHARE_REPORT = "share_report"
    UNSUPPORTED = "unsupported"
    # Legacy intents (for backward compatibility)
    TODAY_SESSION = "today_session"
    FATIGUE_CHECK = "fatigue_check"
    LOAD_EXPLANATION = "load_explanation"
    FREE_CHAT = "free_chat"
