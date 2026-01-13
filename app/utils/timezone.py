"""Timezone utility functions for user timezone handling.

Central helper for timezone operations:
- Get user timezone from User model
- Convert between user local time and UTC
- Handle timezone-aware datetime operations
"""

from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from app.db.models import User


def get_user_timezone(user: User) -> ZoneInfo:
    """Get user timezone as ZoneInfo object.

    Args:
        user: User model instance

    Returns:
        ZoneInfo object for user's timezone, defaults to UTC if invalid/missing
    """
    try:
        timezone_str = getattr(user, "timezone", "UTC") or "UTC"
        return ZoneInfo(timezone_str)
    except Exception:
        return ZoneInfo("UTC")


def now_user(user: User) -> datetime:
    """Get current datetime in user's timezone.

    Args:
        user: User model instance

    Returns:
        Current datetime in user's timezone
    """
    tz = get_user_timezone(user)
    return datetime.now(tz)


def to_utc(dt: datetime) -> datetime:
    """Convert datetime to UTC.

    Args:
        dt: Datetime (timezone-aware or naive)

    Returns:
        Datetime in UTC timezone
    """
    if dt.tzinfo is None:
        # Assume UTC if naive
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)
