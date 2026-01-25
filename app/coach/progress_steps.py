"""Fixed progress step IDs and labels for plan_week.

Used by api_chat (planned events) and plan_week (per-phase in_progress/completed).
Must match frontend PREVIEW_CHECKLIST_STEPS in CoachProgressPanel.
"""

PLAN_WEEK_STEPS: list[tuple[str, str]] = [
    ("review", "Review CTL / ATL / TSB"),
    ("focus", "Determine weekly focus"),
    ("workouts", "Plan key workouts"),
    ("recovery", "Insert recovery"),
]
