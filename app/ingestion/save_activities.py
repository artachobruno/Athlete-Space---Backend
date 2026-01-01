"""Save activity records to the database."""

from __future__ import annotations

from loguru import logger
from sqlalchemy.orm import Session

from app.state.models import Activity
from models.activity import ActivityRecord


def save_activity_record(session: Session, record: ActivityRecord) -> Activity:
    """Save a single ActivityRecord to the database.

    Args:
        session: Database session
        record: ActivityRecord to save (must include athlete_id)

    Returns:
        Saved Activity model instance
    """
    # Check if activity already exists for this athlete
    existing = session.query(Activity).filter_by(athlete_id=record.athlete_id, source=record.source, activity_id=record.activity_id).first()

    if existing:
        logger.debug(f"Activity {record.activity_id} already exists for athlete {record.athlete_id}, updating")
        # Update existing record
        existing.source = record.source
        existing.sport = record.sport
        existing.start_time = record.start_time
        existing.duration_s = record.duration_sec
        existing.distance_m = record.distance_m
        existing.elevation_m = record.elevation_m
        existing.avg_hr = record.avg_hr
        return existing

    # Create new activity
    activity = Activity(
        athlete_id=record.athlete_id,
        activity_id=record.activity_id,
        source=record.source,
        sport=record.sport,
        start_time=record.start_time,
        duration_s=record.duration_sec,
        distance_m=record.distance_m,
        elevation_m=record.elevation_m,
        avg_hr=record.avg_hr,
    )
    session.add(activity)
    logger.debug(f"Added new activity: {record.activity_id} for athlete {record.athlete_id}")
    return activity


def save_activity_records(session: Session, records: list[ActivityRecord]) -> int:
    """Save multiple ActivityRecords to the database.

    Args:
        session: Database session
        records: List of ActivityRecords to save

    Returns:
        Number of activities saved (including updates)
    """
    if not records:
        logger.debug("No activity records to save")
        return 0

    logger.info(f"Saving {len(records)} activity records to database")
    saved_count = 0

    for record in records:
        try:
            save_activity_record(session, record)
            saved_count += 1
        except Exception as e:
            logger.error(f"Error saving activity {record.activity_id}: {e}")
            # Continue with other activities even if one fails
            continue

    session.commit()
    logger.info(f"Successfully saved {saved_count} activities to database")
    return saved_count
