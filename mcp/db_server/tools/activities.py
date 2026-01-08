"""Activity query tools for MCP DB server."""

import sys
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path

from loguru import logger
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError

# Add parent directory to path
sys.path.insert(0, str(Path(__file__).parent.parent.parent.parent))

from app.db.models import Activity
from app.db.session import get_session
from mcp.db_server.errors import MCPError


def get_recent_activities_tool(arguments: dict) -> dict:
    """Get recent activities for a user.

    Contract: get_recent_activities.json
    """
    user_id = arguments.get("user_id")
    days = arguments.get("days", 7)

    # Validate inputs
    if not user_id or not isinstance(user_id, str):
        raise MCPError("INVALID_INPUT", "Missing or invalid user_id")
    if not isinstance(days, int) or days <= 0:
        raise MCPError("INVALID_DAYS", "Days must be a positive integer")

    try:
        since = datetime.now(UTC) - timedelta(days=days)
        with get_session() as session:
            activities = (
                session.execute(
                    select(Activity)
                    .where(
                        Activity.user_id == user_id,
                        Activity.start_time >= since,
                    )
                    .order_by(Activity.start_time.desc())
                )
                .scalars()
                .all()
            )

            # Convert to dict format
            activity_list = [
                {
                    "id": activity.id,
                    "user_id": activity.user_id,
                    "athlete_id": activity.athlete_id,
                    "type": activity.type,
                    "start_time": activity.start_time.isoformat(),
                    "duration_seconds": activity.duration_seconds,
                    "distance_meters": activity.distance_meters,
                    "elevation_gain_meters": activity.elevation_gain_meters,
                }
                for activity in activities
            ]

            logger.info(f"Found {len(activity_list)} activities for user_id={user_id}, days={days}")

            return {"activities": activity_list}

    except SQLAlchemyError as e:
        logger.error(f"Database error getting recent activities: {e}", exc_info=True)
        raise MCPError("DB_ERROR", "Database query failed") from e
    except Exception as e:
        logger.error(f"Unexpected error getting recent activities: {e}", exc_info=True)
        raise MCPError("DB_ERROR", "Database query failed") from e


def get_yesterday_activities_tool(arguments: dict) -> dict:
    """Get activities from yesterday for a user.

    Contract: get_yesterday_activities.json
    """
    user_id = arguments.get("user_id")

    # Validate inputs
    if not user_id or not isinstance(user_id, str):
        raise MCPError("INVALID_INPUT", "Missing or invalid user_id")

    try:
        now = datetime.now(UTC)
        yesterday_start = (now - timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
        yesterday_end = yesterday_start + timedelta(days=1)

        with get_session() as session:
            activities = (
                session.execute(
                    select(Activity)
                    .where(
                        Activity.user_id == user_id,
                        Activity.start_time >= yesterday_start,
                        Activity.start_time < yesterday_end,
                    )
                    .order_by(Activity.start_time.desc())
                )
                .scalars()
                .all()
            )

            # Convert to dict format
            activity_list = [
                {
                    "id": activity.id,
                    "user_id": activity.user_id,
                    "athlete_id": activity.athlete_id,
                    "type": activity.type,
                    "start_time": activity.start_time.isoformat(),
                    "duration_seconds": activity.duration_seconds,
                    "distance_meters": activity.distance_meters,
                    "elevation_gain_meters": activity.elevation_gain_meters,
                }
                for activity in activities
            ]

            logger.info(f"Found {len(activity_list)} activities from yesterday for user_id={user_id}")

            return {"activities": activity_list}

    except SQLAlchemyError as e:
        logger.error(f"Database error getting yesterday activities: {e}", exc_info=True)
        raise MCPError("DB_ERROR", "Database query failed") from e
    except Exception as e:
        logger.error(f"Unexpected error getting yesterday activities: {e}", exc_info=True)
        raise MCPError("DB_ERROR", "Database query failed") from e
