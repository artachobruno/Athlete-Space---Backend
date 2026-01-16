"""Session-level diffing logic."""

from app.coach.diff.diff_models import FieldChange, SessionFieldDiff
from app.coach.diff.diff_utils import comparable_dict


def diff_sessions(before, after) -> SessionFieldDiff | None:
    """Compute field-level diff between two sessions.

    Args:
        before: Original PlannedSession
        after: Modified PlannedSession

    Returns:
        SessionFieldDiff if changes detected, None if identical
    """
    changes = []

    before_dict = comparable_dict(before)
    after_dict = comparable_dict(after)

    for field, before_val in before_dict.items():
        after_val = after_dict.get(field)
        if before_val != after_val:
            changes.append(
                FieldChange(
                    field=field,
                    before=before_val,
                    after=after_val,
                )
            )

    if not changes:
        return None

    return SessionFieldDiff(
        session_id=before.id,
        changes=changes,
    )
