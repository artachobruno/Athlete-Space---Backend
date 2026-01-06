"""Daily training aggregation from activities to daily_training_summary.

This module aggregates activities by UTC date and writes to the derived
daily_training_summary table. The aggregation is idempotent and always
recomputes the last 60 days to handle updates and corrections.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from typing import cast

from loguru import logger
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.db.session import get_session
from app.services.metrics.training_load import DailyTrainingRow


def aggregate_daily_training(user_id: str) -> None:
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
        user_id: Clerk user ID (string) to aggregate for
    """
    logger.info(f"[AGGREGATION] Starting daily aggregation for user_id={user_id}")

    with get_session() as session:
        # Calculate date range (last 60 days)
        end_date = datetime.now(timezone.utc).date()
        start_date = end_date - timedelta(days=60)

        # Delete existing rows for this user in the date range (idempotent)
        logger.info(f"[AGGREGATION] Deleting existing daily summary rows for user_id={user_id} (date range: {start_date} to {end_date})")
        session.execute(
            text(
                """
                DELETE FROM daily_training_summary
                WHERE user_id = :user_id
                AND date >= :start_date
                AND date <= :end_date
                """
            ),
            {
                "user_id": user_id,
                "start_date": start_date.isoformat(),
                "end_date": end_date.isoformat(),
            },
        )
        logger.info(f"[AGGREGATION] Deleted existing rows for user_id={user_id}")

        # Aggregate activities by UTC date for this user
        logger.info(f"[AGGREGATION] Aggregating activities for user_id={user_id} (date range: {start_date} to {end_date})")
        rows = session.execute(
            text(
                """
                SELECT
                    DATE(start_time) as date,
                    SUM(duration_seconds) as duration_seconds,
                    SUM(distance_meters) as distance_meters,
                    SUM(elevation_gain_meters) as elevation_gain_meters
                FROM activities
                WHERE user_id = :user_id
                AND DATE(start_time) >= :start_date
                AND DATE(start_time) <= :end_date
                GROUP BY DATE(start_time)
                ORDER BY date
                """
            ),
            {
                "user_id": user_id,
                "start_date": start_date.isoformat(),
                "end_date": end_date.isoformat(),
            },
        ).fetchall()

        logger.info(f"[AGGREGATION] Found {len(rows)} days with activities for user_id={user_id}")

        # Insert aggregated rows
        inserted_count = 0
        for row in rows:
            date_str = row.date if isinstance(row.date, str) else row.date.isoformat()
            duration_seconds = int(row.duration_seconds) if row.duration_seconds else 0
            distance_meters = float(row.distance_meters) if row.distance_meters else 0.0
            elevation_gain_meters = float(row.elevation_gain_meters) if row.elevation_gain_meters else 0.0

            # Calculate load_score (duration in hours for v1)
            load_score = duration_seconds / 3600.0

            session.execute(
                text(
                    """
                    INSERT INTO daily_training_summary
                    (user_id, date, duration_s, distance_m, elevation_m, load_score)
                    VALUES (:user_id, :date, :duration_s, :distance_m, :elevation_m, :load_score)
                    """
                ),
                {
                    "user_id": user_id,
                    "date": date_str,
                    "duration_s": duration_seconds,
                    "distance_m": distance_meters,
                    "elevation_m": elevation_gain_meters,
                    "load_score": load_score,
                },
            )
            inserted_count += 1

        session.commit()
        logger.info(
            f"[AGGREGATION] Aggregated {inserted_count} days of training data for user_id={user_id} "
            f"(date range: {start_date} to {end_date})"
        )


def get_daily_rows(session: Session, user_id: str, days: int = 60) -> list[DailyTrainingRow]:
    """Get daily training rows from daily_training_summary.

    Returns data for all days in the requested range, filling missing days with zero values.

    Args:
        session: Database session
        user_id: Clerk user ID (string)
        days: Number of days to look back (default: 60)

    Returns:
        List of daily rows with keys: date, duration_s, distance_m, elevation_m, load_score
        Ordered chronologically. All days from start_date to end_date are included,
        with missing days filled with zero values.
    """
    end_date = datetime.now(timezone.utc).date()
    start_date = end_date - timedelta(days=days)

    # Fetch existing rows from database
    rows = session.execute(
        text(
            """
            SELECT date, duration_s, distance_m, elevation_m, load_score
            FROM daily_training_summary
            WHERE user_id = :user_id
            AND date >= :start_date
            AND date <= :end_date
            ORDER BY date
            """
        ),
        {
            "user_id": user_id,
            "start_date": start_date.isoformat(),
            "end_date": end_date.isoformat(),
        },
    ).fetchall()

    # Create a map of date -> row data for quick lookup
    data_map: dict[date, DailyTrainingRow] = {}
    for row in rows:
        row_date = row.date if isinstance(row.date, date) else datetime.fromisoformat(row.date).date()
        data_map[row_date] = {
            "date": row_date.isoformat(),
            "duration_s": int(row.duration_s),
            "distance_m": float(row.distance_m),
            "elevation_m": float(row.elevation_m),
            "load_score": float(row.load_score),
        }

    # Build complete list for all days in range, filling missing days with zeros
    result: list[DailyTrainingRow] = []
    current_date = start_date
    while current_date <= end_date:
        if current_date in data_map:
            result.append(data_map[current_date])
        else:
            # Fill missing day with zero values
            result.append({
                "date": current_date.isoformat(),
                "duration_s": 0,
                "distance_m": 0.0,
                "elevation_m": 0.0,
                "load_score": 0.0,
            })
        current_date += timedelta(days=1)

    return cast(list[DailyTrainingRow], result)
