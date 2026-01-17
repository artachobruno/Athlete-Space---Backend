"""Backfill metadata fields in daily_decisions table from decision_data JSON.

This script extracts recommendation_type, recommended_intensity, and has_workout
from the decision_data JSON column and populates the metadata fields.

This is optional - the system works without it, but backfilling enables
fast queries on metadata fields for historical data.
"""

import sys
from pathlib import Path

# Add project root to Python path
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from sqlalchemy import text

from app.db.session import engine


def backfill_daily_decisions_metadata() -> None:
    """Backfill metadata fields from decision_data JSON."""
    print("Starting backfill: daily_decisions metadata fields")

    with engine.begin() as conn:
        # Check if there are rows with NULL metadata but non-empty decision_data
        result = conn.execute(
            text(
                """
                SELECT COUNT(*)
                FROM daily_decisions
                WHERE decision_data IS NOT NULL
                AND decision_data != '{}'::jsonb
                AND (
                    recommendation_type IS NULL
                    OR recommended_intensity IS NULL
                    OR has_workout IS NULL
                )
                """
            )
        )
        count = result.scalar() or 0

        if count == 0:
            print("✅ No rows need backfilling. All metadata fields are already populated.")
            return

        print(f"Found {count} rows that need metadata backfilling")

        # Update metadata fields from decision_data JSON
        # Extract recommendation -> recommendation_type
        # Extract intensity_focus -> recommended_intensity
        # Extract recommendation != 'rest' -> has_workout
        conn.execute(
            text(
                """
                UPDATE daily_decisions
                SET
                    recommendation_type = CASE
                        WHEN decision_data->>'recommendation' IS NOT NULL
                        THEN decision_data->>'recommendation'
                        ELSE recommendation_type
                    END,
                    recommended_intensity = CASE
                        WHEN decision_data->>'intensity_focus' IS NOT NULL
                        THEN decision_data->>'intensity_focus'
                        ELSE recommended_intensity
                    END,
                    has_workout = CASE
                        WHEN decision_data->>'recommendation' IS NOT NULL
                        THEN (decision_data->>'recommendation') != 'rest'
                        ELSE has_workout
                    END,
                    updated_at = now()
                WHERE decision_data IS NOT NULL
                AND decision_data != '{}'::jsonb
                AND (
                    recommendation_type IS NULL
                    OR recommended_intensity IS NULL
                    OR has_workout IS NULL
                )
                """
            )
        )

        # Verify the update
        result = conn.execute(
            text(
                """
                SELECT COUNT(*)
                FROM daily_decisions
                WHERE decision_data IS NOT NULL
                AND decision_data != '{}'::jsonb
                AND (
                    recommendation_type IS NULL
                    OR recommended_intensity IS NULL
                    OR has_workout IS NULL
                )
                """
            )
        )
        remaining = result.scalar() or 0

        if remaining == 0:
            print(f"✅ Successfully backfilled metadata for {count} rows")
        else:
            print(f"⚠️  Backfilled {count - remaining} rows, but {remaining} rows still have NULL metadata")
            print("   This may be due to missing fields in decision_data JSON")


if __name__ == "__main__":
    backfill_daily_decisions_metadata()
