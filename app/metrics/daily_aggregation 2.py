"""Daily training aggregation from activities to daily_training_summary.

This module aggregates activities by UTC date and writes to the derived
daily_training_summary table. The aggregation is idempotent and always
recomputes the last 60 days to handle updates and corrections.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from loguru import logger
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.state.db import get_session


def aggregate_daily_training(athlete_id: int) -> None:
    """Aggregate activities into daily training summary.

    Reads from activities table, groups by UTC date, and writes to
    daily_training_summary. Always recomputes last 60 days to handle
    updates and corrections.

    Rules:
    - Sum duration, distance, elevation per day
    - Ignore duplicate activities (handled by unique constraint)
    - Always recompute last 60 days (idempotent)
    - Missing days = no row (explicit gaps)

    Args:
        athlete_id: Athlete ID to aggregate for

    Note:
        Since Activity table doesn't have athlete_id, we currently
        aggregate all activities. In a multi-user system, athlete_id
        should be added to Activity table.
    """
    logger.info(f"[AGGREGATION] Starting daily aggregation for athlete_id={athlete_id}")

    with get_session() as session:
        # Calculate date range (last 60 days)
        end_date = datetime.now(timezone.utc).date()
        start_date = end_date - timedelta(days=60)

        # Delete existing rows for this athlete in the date range (idempotent)
        session.execute(
            text(
                """
                DELETE FROM daily_training_summary
                WHERE athlete_id = :athlete_id
                AND date >= :start_date
                AND date <= :end_date
                """
            ),
            {
                "athlete_id": athlete_id,
                "start_date": start_date.isoformat(),
                "end_date": end_date.isoformat(),
            },
        )

        # Aggregate activities by UTC date
        # Note: Since Activity table doesn't have athlete_id, we aggregate all activities
        # In a proper multi-user system, we'd filter by athlete_id here
        rows = session.execute(
            text(
                """
                SELECT
                    DATE(start_time) as date,
                    SUM(duration_s) as duration_s,
                    SUM(distance_m) as distance_m,
                    SUM(elevation_m) as elevation_m
                FROM activities
                WHERE DATE(start_time) >= :start_date
                AND DATE(start_time) <= :end_date
                GROUP BY DATE(start_time)
                ORDER BY date
                """
            ),
            {
                "start_date": start_date.isoformat(),
                "end_date": end_date.isoformat(),
            },
        ).fetchall()

        # Insert aggregated rows
        inserted_count = 0
        for row in rows:
            date_str = row.date if isinstance(row.date, str) else row.date.isoformat()
            duration_s = int(row.duration_s) if row.duration_s else 0
            distance_m = float(row.distance_m) if row.distance_m else 0.0
            elevation_m = float(row.elevation_m) if row.elevation_m else 0.0

            # Calculate load_score (duration in hours for v1)
            load_score = duration_s / 3600.0

            session.execute(
                text(
                    """
                    INSERT INTO daily_training_summary
                    (athlete_id, date, duration_s, distance_m, elevation_m, load_score)
                    VALUES (:athlete_id, :date, :duration_s, :distance_m, :elevation_m, :load_score)
                    """
                ),
                {
                    "athlete_id": athlete_id,
                    "date": date_str,
                    "duration_s": duration_s,
                    "distance_m": distance_m,
                    "elevation_m": elevation_m,
                    "load_score": load_score,
                },
            )
            inserted_count += 1

        session.commit()
        logger.info(
            f"[AGGREGATION] Aggregated {inserted_count} days of training data for athlete_id={athlete_id} "
            f"(date range: {start_date} to {end_date})"
        )


def get_daily_rows(session: Session, athlete_id: int, days: int = 60) -> list[dict[str, str | int | float]]:
    """Get daily training rows from daily_training_summary.

    Args:
        session: Database session
        athlete_id: Athlete ID
        days: Number of days to look back (default: 60)

    Returns:
        List of daily rows with keys: date, duration_s, distance_m, elevation_m, load_score
        Ordered chronologically. Missing days are not included (explicit gaps).

    Note:
        Returns dict format compatible with DailyTrainingRow TypedDict.
    """
    end_date = datetime.now(timezone.utc).date()
    start_date = end_date - timedelta(days=days)

    rows = session.execute(
        text(
            """
            SELECT date, duration_s, distance_m, elevation_m, load_score
            FROM daily_training_summary
            WHERE athlete_id = :athlete_id
            AND date >= :start_date
            AND date <= :end_date
            ORDER BY date
            """
        ),
        {
            "athlete_id": athlete_id,
            "start_date": start_date.isoformat(),
            "end_date": end_date.isoformat(),
        },
    ).fetchall()

    return [
        {
            "date": row.date if isinstance(row.date, str) else row.date.isoformat(),
            "duration_s": int(row.duration_s),
            "distance_m": float(row.distance_m),
            "elevation_m": float(row.elevation_m),
            "load_score": float(row.load_score),
        }
        for row in rows
    ]
