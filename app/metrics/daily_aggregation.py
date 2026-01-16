"""Daily training aggregation from activities to daily_training_summary.

This module aggregates activities by UTC date and writes to the derived
daily_training_summary table. The aggregation is idempotent and always
recomputes the last 60 days to handle updates and corrections.
"""

from __future__ import annotations

import json
from datetime import date, datetime, timedelta, timezone
from typing import cast

from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from app.db.models import Activity, StravaAccount
from app.db.session import get_session
from app.metrics.training_load import DailyTrainingRow


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
    - Skips if all activities are already fetched and no activities exist in date range

    Args:
        user_id: Clerk user ID (string) to aggregate for
    """
    with get_session() as session:
        # Calculate date range (last 60 days)
        end_date = datetime.now(timezone.utc).date()
        start_date = end_date - timedelta(days=60)

        # Check if all activities are already fetched
        account = session.execute(select(StravaAccount).where(StravaAccount.user_id == user_id)).scalar_one_or_none()
        if account and account.full_history_synced:
            # Check if there are any activities in the date range
            activity_count = session.execute(
                select(Activity.id).where(
                    Activity.user_id == user_id,
                    func.date(Activity.starts_at) >= start_date,
                    func.date(Activity.starts_at) <= end_date,
                ).limit(1)
            ).scalar()

            # If no activities in range, skip aggregation
            if not activity_count:
                return

        # Delete existing rows for this user in the date range (idempotent)
        session.execute(
            text(
                """
                DELETE FROM daily_training_summary
                WHERE user_id = :user_id
                AND day >= :start_date
                AND day <= :end_date
                """
            ),
            {
                "user_id": user_id,
                "start_date": start_date.isoformat(),
                "end_date": end_date.isoformat(),
            },
        )

        # Aggregate activities by UTC date for this user
        rows = session.execute(
            text(
                """
                SELECT
                    DATE(starts_at) as day,
                    SUM(duration_seconds) as duration_seconds,
                    SUM(distance_meters) as distance_meters,
                    SUM(elevation_gain_meters) as elevation_gain_meters
                FROM activities
                WHERE user_id = :user_id
                AND DATE(starts_at) >= :start_date
                AND DATE(starts_at) <= :end_date
                GROUP BY DATE(starts_at)
                ORDER BY day
                """
            ),
            {
                "user_id": user_id,
                "start_date": start_date.isoformat(),
                "end_date": end_date.isoformat(),
            },
        ).fetchall()

        # Insert aggregated rows (storing data in summary JSONB)
        for row in rows:
            day_str = row.day if isinstance(row.day, str) else row.day.isoformat()
            duration_seconds = int(row.duration_seconds) if row.duration_seconds else 0
            distance_meters = float(row.distance_meters) if row.distance_meters else 0.0
            elevation_gain_meters = float(row.elevation_gain_meters) if row.elevation_gain_meters else 0.0

            # Calculate load_score (duration in hours for v1)
            load_score = duration_seconds / 3600.0

            # Serialize summary dict to JSON string for PostgreSQL JSONB
            summary_json = json.dumps({
                "duration_s": duration_seconds,
                "distance_m": distance_meters,
                "elevation_m": elevation_gain_meters,
                "load_score": load_score,
            })
            session.execute(
                text(
                    """
                    INSERT INTO daily_training_summary
                    (user_id, day, summary)
                    VALUES (:user_id, :day, :summary::jsonb)
                    """
                ),
                {
                    "user_id": user_id,
                    "day": day_str,
                    "summary": summary_json,
                },
            )

        session.commit()


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

    # Fetch existing rows from database (extract from summary JSONB)
    rows = session.execute(
        text(
            """
            SELECT
                day,
                (summary->>'duration_s')::double precision AS duration_s,
                (summary->>'distance_m')::double precision AS distance_m,
                (summary->>'elevation_m')::double precision AS elevation_m,
                (summary->>'load_score')::double precision AS load_score
            FROM daily_training_summary
            WHERE user_id = :user_id
            AND day >= :start_date
            AND day <= :end_date
            ORDER BY day
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
        row_date = row.day if isinstance(row.day, date) else datetime.fromisoformat(row.day).date()
        data_map[row_date] = {
            "date": row_date.isoformat(),
            "duration_s": int(row.duration_s) if row.duration_s else 0,
            "distance_m": float(row.distance_m) if row.distance_m else 0.0,
            "elevation_m": float(row.elevation_m) if row.elevation_m else 0.0,
            "load_score": float(row.load_score) if row.load_score else 0.0,
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
