# Plans Module - Volume & Pace Semantics

## ⚠️ CRITICAL RULES (Non-Negotiable)

This module enforces foundational rules for volume and pace semantics:

1. **Distance units are ALWAYS miles**
   - No kilometers
   - No mixed units
   - All distance calculations use miles

2. **Pace is always numeric**
   - Pace must have `pace_min_per_mile` value
   - Pace labels are derived, not free-text
   - No pace strings without numeric backing

3. **Race goal pace is the anchor**
   - All training paces are estimated from race goal pace
   - Use `estimate_pace()` function - no hard-coded paces
   - Race goal pace is stored in `AthletePaceProfile`

4. **Volume is derived, never stored**
   - Volume = sum of distance (miles) OR duration (minutes)
   - Never mix distance and duration in volume calculation
   - Use `compute_weekly_volume_miles()` for volume

5. **Every workout has a primary metric**
   - Either `primary="distance"` with `distance_miles`
   - Or `primary="duration"` with `duration_min`
   - Never both as primary

6. **Workout intent is required and immutable by default**
   - Every workout must have `intent` (rest, easy, long, quality)
   - **Intent is session-level, not metrics-level** (on `MaterializedSession`, not `WorkoutMetrics`)
   - Intent describes purpose, not pace
   - Intent cannot change unless explicitly requested (e.g., "make this an easy day instead")
   - **MODIFY must preserve intent by default** - never re-infer intent during modification
   - Exactly one long run per week

## Module Structure

- `types.py`: Canonical `WorkoutMetrics`, `PaceMetrics`, and `WorkoutIntent` schemas
- `validators.py`: Hard guardrails via `validate_workout_metrics()` and `validate_workout_intent()`
- `pace.py`: Single source of truth for pace estimation
- `volume.py`: Miles-only volume computation
- `week_planner.py`: Week planning utilities with intent assignment
- `intent_rules.py`: Intent-pace constraints (non-enforcing, for Step 3)

## Usage Examples

### Creating Workout Metrics

```python
from app.plans.types import WorkoutMetrics
from app.plans.pace import estimate_pace
from app.plans.validators import validate_workout_metrics
from app.planning.output.models import MaterializedSession

# WorkoutMetrics does NOT include intent (intent is session-level)
race_pace = 8.0  # 8 min/mile
metrics = WorkoutMetrics(
    primary="distance",
    distance_miles=6.0,
    pace=estimate_pace("easy", race_pace, pace_source="race_goal"),
)
validate_workout_metrics(metrics)

# Intent is set on MaterializedSession, not WorkoutMetrics
session = MaterializedSession(
    day="mon",
    intent="easy",  # Intent is session-level
    session_template_id="test",
    session_type="easy",
    duration_minutes=60,
    distance_miles=metrics.distance_miles,
)
```

### Assigning Workout Intent

```python
from app.plans.week_planner import assign_intent_from_day_type

# Assign intent based on day type
intent = assign_intent_from_day_type(
    day_type="long",
    is_long_run_day=True,
    is_quality_day=False,
    is_rest_day=False,
)
# Returns "long"
```

### Computing Weekly Volume

```python
from app.plans.volume import compute_weekly_volume_miles
from app.plans.types import WorkoutMetrics

workouts = [
    {"metrics": WorkoutMetrics(primary="distance", distance_miles=5.0)},
    {"metrics": WorkoutMetrics(primary="distance", distance_miles=8.0)},
]
total_miles = compute_weekly_volume_miles(workouts)  # 13.0 miles
```

### MODIFY Logic - Intent Preservation

```python
# When modifying a workout, preserve intent from original session
original_session = MaterializedSession(
    day="mon",
    intent="quality",  # Original intent
    session_template_id="test",
    session_type="tempo",
    duration_minutes=45,
    distance_miles=5.0,
)

# Create new metrics (intent NOT in metrics)
new_metrics = WorkoutMetrics(
    primary="distance",
    distance_miles=6.0,  # Modified distance
)

# Preserve intent when creating modified session
modified_session = MaterializedSession(
    day=original_session.day,
    intent=original_session.intent,  # PRESERVE intent
    session_template_id=original_session.session_template_id,
    session_type=original_session.session_type,
    duration_minutes=original_session.duration_minutes,
    distance_miles=new_metrics.distance_miles,
)
# Intent is preserved: "quality"
```

## Forbidden Patterns

❌ **DO NOT:**
- Use `distance_km` or kilometers in plan code
- Create pace labels without numeric values
- Hard-code pace values (use `estimate_pace()`)
- Mix distance and duration in volume calculation
- Use free-text pace strings
- Create workouts without intent (intent is required on `MaterializedSession`)
- Put intent in `WorkoutMetrics` (intent is session-level, not metrics-level)
- Infer intent from pace (intent ≠ pace)
- Re-infer intent during MODIFY (preserve original intent)
- Change intent when modifying distance/pace (unless explicitly requested)

✅ **DO:**
- Always use miles for distance
- Always provide numeric pace values
- Use race goal pace as anchor
- Validate metrics with `validate_workout_metrics()`
- Use `estimate_pace()` for all pace calculations
- Always assign intent to `MaterializedSession` (session-level)
- Preserve intent when modifying workouts (MODIFY rule)
- Ensure exactly one long run per week
- Use `infer_intent_from_session_type()` only for planning, never for MODIFY

## Testing

Run tests with:
```bash
pytest tests/plans/test_volume_and_pace_semantics.py
pytest tests/plans/test_workout_intent.py
```

Tests enforce all critical rules and catch regressions.
