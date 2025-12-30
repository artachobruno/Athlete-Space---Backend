from enum import StrEnum


class CoachIntent(StrEnum):
    TODAY_SESSION = "today_session"
    FATIGUE_CHECK = "fatigue_check"
    LOAD_EXPLANATION = "load_explanation"
    FREE_CHAT = "free_chat"
