"""Workout notes parsing module.

This module handles parsing of free-form workout notes into structured steps.
It is designed to be completely non-mutating and side-effect-free.

SAFETY GUARDS:
- NO database imports (db.session, models.Workout, etc.)
- NO reconciliation imports
- NO persistence logic
- All functions are pure (no side effects)

This module is safe to call from API endpoints without risk of accidental
workout creation, database writes, or reconciliation triggers.
"""

from __future__ import annotations

from app.workouts.schemas import ParsedStepSchema, ParseNotesRequest, ParseNotesResponse


def parse_notes_stub(_request: ParseNotesRequest) -> ParseNotesResponse:
    """Stub implementation of notes parsing.

    Returns an "unavailable" response indicating the feature is disabled.
    This endpoint always succeeds (returns 200) and never mutates state.

    Args:
        request: Parse notes request

    Returns:
        ParseNotesResponse with status "unavailable"
    """
    return ParseNotesResponse(
        status="unavailable",
        steps=None,
        confidence=0.0,
        warnings=["Structured parsing not enabled yet"],
    )
