"""Backfill script to fix a single planned_session with NULL workout_id.

This script backfills the workout for a specific planned_session that has
a NULL workout_id, ensuring data integrity before enforcing the NOT NULL constraint.
"""

import sys
from pathlib import Path

# Add project root to Python path (must be absolute for Render/production)
script_dir = Path(__file__).parent.resolve()
project_root = script_dir.parent.resolve()

# Verify project root contains app directory or pyproject.toml
if not (project_root / "app").exists() and not (project_root / "pyproject.toml").exists():
    # If parent doesn't have app/ or pyproject.toml, try current working directory
    cwd = Path.cwd().resolve()
    if (cwd / "app").exists() or (cwd / "pyproject.toml").exists():
        project_root = cwd
    else:
        # Last resort: try going up one more level (for cases where script is in src/scripts/)
        parent_parent = script_dir.parent.parent.resolve()
        if (parent_parent / "app").exists() or (parent_parent / "pyproject.toml").exists():
            project_root = parent_parent

# Ensure project root is in path
project_root_str = str(project_root)
if project_root_str not in sys.path:
    sys.path.insert(0, project_root_str)

from loguru import logger

from app.db.models import PlannedSession
from app.db.session import get_session
from app.workouts.workout_factory import WorkoutFactory

TARGET_ID = "50888be1-906e-4506-9d41-e9e833fd3937"


def main() -> None:
    """Backfill workout for the target planned_session."""
    logger.info(f"Starting backfill for planned_session: {TARGET_ID}")

    with get_session() as session:
        ps = session.get(PlannedSession, TARGET_ID)
        if not ps:
            raise RuntimeError(f"PlannedSession {TARGET_ID} not found")

        if ps.workout_id:
            logger.info(f"PlannedSession {TARGET_ID} already has workout_id={ps.workout_id}. Nothing to do.")
            return

        logger.info(f"Backfilling workout for planned_session {TARGET_ID}")
        workout = WorkoutFactory.get_or_create_for_planned_session(
            session=session,
            planned_session=ps,
        )

        logger.info(f"✓ Backfilled workout {workout.id} for planned_session {ps.id}")
        session.commit()

    logger.info("Backfill complete")


if __name__ == "__main__":
    try:
        main()
        print("✅ Success! Workout backfilled successfully.")
    except Exception as e:
        logger.error(f"Error backfilling workout: {e}", exc_info=True)
        print(f"❌ Error: {e}", file=sys.stderr)
        sys.exit(1)
