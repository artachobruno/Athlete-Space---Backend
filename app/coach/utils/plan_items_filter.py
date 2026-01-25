"""Filter plan_items to remove internal process steps (planner trace) from user-facing output.

Process-step language (e.g. "Review...", "Consider...", "Adjust...") describes what the
system is doing, not what the athlete gets. Such items must never be shown to users.
"""

# Verbs/phrases that indicate process steps (internal planning), not athlete-facing outcomes.
# Items starting with these (case-insensitive) are stripped before sending to frontend.
PROCESS_STEP_PREFIXES = (
    "review ",
    "consider ",
    "adjust ",
    "determine ",
    "schedule ",
    "include ",
    "retrieving ",
    "providing ",
    "highlighting ",
    "analyzing ",
    "assessing ",
)


def filter_athlete_facing_plan_items(
    plan_items: list[str] | None,
) -> list[str] | None:
    """Remove process-step-like items; return only athlete-facing outcomes.

    Returns None if input is None or if all items are filtered out.
    """
    if not plan_items:
        return None
    kept: list[str] = []
    for item in plan_items:
        s = (item.strip() if isinstance(item, str) else "").lower()
        if not s:
            continue
        if any(s.startswith(prefix) for prefix in PROCESS_STEP_PREFIXES):
            continue
        kept.append(item)
    return kept if kept else None
