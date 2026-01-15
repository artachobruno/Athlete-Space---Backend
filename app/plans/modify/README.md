# MODIFY → day Module

## Intent Persistence

**Intent is now a persisted, authoritative field on PlannedSession.**

- `intent` field on `PlannedSession` is the source of truth
- `session_type` is legacy/auxiliary (kept for backward compatibility)
- HR reconciliation can now answer: "Was this supposed to be easy or quality?"

## Backfill

To populate intent for existing sessions:

```python
from app.plans.modify.backfill import backfill_intent_from_session_type

stats = backfill_intent_from_session_type()
# Returns: {"total": N, "updated": M, "skipped": K, "errors": 0}
```

This is a one-time migration. After running, all new sessions will have intent set explicitly.

## MODIFY → day Rules

- Intent is preserved by default
- Intent only changes if `explicit_intent_change` is provided
- Never re-infer intent during modification
- Original session remains (new session created)

## Usage

```python
from app.coach.tools.modify_day import modify_day
from app.plans.modify.types import DayModification

modification = DayModification(
    change_type="adjust_distance",
    value=6.0,
    reason="Increase distance",
)

result = modify_day({
    "user_id": "user-123",
    "athlete_id": 1,
    "target_date": date(2024, 1, 15),
    "modification": modification.model_dump(),
})
```
