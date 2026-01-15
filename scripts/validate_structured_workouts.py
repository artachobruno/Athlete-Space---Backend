"""Data integrity check script for structured workouts.

Validates that all workout steps have proper names.
Fails if any structured step is missing a name.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import TypedDict

# Add parent directory to path to import app modules
sys.path.insert(0, str(Path(__file__).parent.parent))

from sqlalchemy import select

from app.db.session import get_session
from app.workouts.models import Workout, WorkoutStep
from app.workouts.step_utils import infer_step_name


class IssueDict(TypedDict):
    """Type definition for validation issue dictionary."""

    workout_id: str
    step_id: str
    step_order: int
    step_type: str


def validate_structured_workouts() -> tuple[int, int]:
    """Validate all structured workout steps have names.

    Returns:
        Tuple of (total_steps_checked, steps_with_missing_names)
    """
    with get_session() as session:
        # Get all workouts with steps
        stmt = select(Workout).where(Workout.parse_status == "success")
        workouts = session.execute(stmt).scalars().all()

        total_steps = 0
        missing_names = 0
        issues: list[IssueDict] = []

        for workout in workouts:
            steps_stmt = (
                select(WorkoutStep)
                .where(WorkoutStep.workout_id == workout.id)
                .order_by(WorkoutStep.order)
            )
            steps = session.execute(steps_stmt).scalars().all()

            for step in steps:
                total_steps += 1
                # Check if step has a name (purpose or instructions)
                has_name = bool(step.purpose or step.instructions)

                if not has_name:
                    missing_names += 1
                    issues.append({
                        "workout_id": workout.id,
                        "step_id": step.id,
                        "step_order": step.order,
                        "step_type": step.type or "unknown",
                    })

        if issues:
            print(f"\n❌ Validation failed: {missing_names} steps missing names out of {total_steps} total steps\n")
            print("Issues found:")
            for issue in issues[:20]:  # Show first 20 issues
                print(
                    f"  Workout {issue['workout_id'][:8]}... "
                    f"Step {issue['step_order']} ({issue['step_type']}) - Missing name"
                )
            if len(issues) > 20:
                print(f"  ... and {len(issues) - 20} more issues")
            return total_steps, missing_names

        print(f"\n✅ Validation passed: All {total_steps} steps have names\n")
        return total_steps, 0


if __name__ == "__main__":
    total, missing = validate_structured_workouts()
    if missing > 0:
        sys.exit(1)
    sys.exit(0)
