"""Read-only tools for coach visibility.

These tools provide read-only access to athlete data.
No modifications are possible through these tools.
"""

from app.tools.read.activities import get_completed_activities
from app.tools.read.calendar import get_calendar_events
from app.tools.read.metrics import get_training_metrics
from app.tools.read.plans import get_planned_activities
from app.tools.read.profile import get_athlete_profile

__all__ = [
    "get_athlete_profile",
    "get_calendar_events",
    "get_completed_activities",
    "get_planned_activities",
    "get_training_metrics",
]
