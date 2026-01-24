"""Canonical week-window helpers for Planning Model B+.

Week boundaries are Monday-Sunday (ISO week).
"""

from datetime import date, timedelta


def week_start(d: date) -> date:
    """Return Monday of the calendar week containing d."""
    return d - timedelta(days=d.weekday())


def week_end(d: date) -> date:
    """Return Sunday of the calendar week containing d."""
    return week_start(d) + timedelta(days=6)
