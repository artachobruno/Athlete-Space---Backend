"""Frontend contracts for training intent APIs.

This module defines the JSON schemas and example payloads
that the frontend can expect from the backend.
These contracts are frozen and should not change without
coordinating with the frontend team.
"""

from datetime import datetime

from pydantic import BaseModel

from app.coach.schemas.intent_schemas import DailyDecision, SeasonPlan, WeeklyIntent

# Example payloads for frontend reference

EXAMPLE_SEASON_PLAN = {
    "focus": "Base building and aerobic development",
    "volume_range": "8-12 hours/week",
    "intensity_density": "80/20 polarized training",
    "adaptation_goal": "Aerobic capacity and fat oxidation",
    "risk_notes": (
        "Main risk is overreaching. We'll monitor TSB weekly and adjust volume if fatigue accumulates. Recovery weeks every 4th week."
    ),
    "confidence": 0.85,
    "explanation": (
        "Your current fitness base is solid. We'll build volume gradually over the next 12 weeks, "
        "targeting 8-12 hours per week. The focus is aerobic development with careful attention to recovery. "
        "This conservative approach minimizes injury risk while building sustainable fitness. "
        "We'll use an 80/20 polarized approach, meaning 80% easy aerobic work and 20% higher intensity. "
        "Recovery weeks every 4th week will ensure adaptation without burnout."
    ),
    "season_start": "2024-01-01",
    "season_end": "2024-03-31",
    "target_races": ["Spring Marathon - April 15", "Half Marathon - March 10"],
}

EXAMPLE_WEEKLY_INTENT = {
    "focus": "Volume accumulation with recovery emphasis",
    "volume_target_hours": 10.0,
    "intensity_distribution": "2 moderate sessions, 4 easy sessions, 1 rest day",
    "adaptation_goal": "Aerobic base building",
    "risk_notes": (
        "Fatigue is slightly elevated. We'll prioritize easy aerobic work and ensure one full rest day. Monitor recovery closely."
    ),
    "confidence": 0.80,
    "explanation": (
        "This week focuses on steady volume accumulation. We'll target 10 hours of training, "
        "with most work in the easy aerobic zone. Two moderate sessions will provide some intensity stimulus "
        "without excessive stress. The rest day mid-week ensures adequate recovery. "
        "This conservative approach supports long-term adaptation."
    ),
    "week_start": "2024-01-08",
    "week_number": 2,
    "season_plan_id": "abc123-season-plan-id",
}

EXAMPLE_DAILY_DECISION = {
    "recommendation": "easy",
    "volume_hours": 1.5,
    "intensity_focus": "Zone 2 aerobic",
    "session_type": "Easy aerobic run",
    "risk_level": "low",
    "risk_notes": "Fatigue is slightly elevated. Keep intensity low and focus on aerobic work.",
    "confidence": 0.85,
    "explanation": (
        "Today is an easy aerobic day. Aim for 1.5 hours of Zone 2 running. "
        "Keep the effort conversational and relaxed. This supports aerobic development without adding stress. "
        "If you feel tired, reduce the duration or take a rest day instead."
    ),
    "decision_date": "2024-01-10",
    "weekly_intent_id": "xyz789-weekly-intent-id",
}

# API Response schemas (what frontend receives)


class SeasonPlanResponse(BaseModel):
    """API response for season plan."""

    id: str
    user_id: str
    athlete_id: int
    plan: SeasonPlan
    version: int
    is_active: bool
    created_at: datetime
    updated_at: datetime


class WeeklyIntentResponse(BaseModel):
    """API response for weekly intent."""

    id: str
    user_id: str
    athlete_id: int
    intent: WeeklyIntent
    season_plan_id: str | None
    version: int
    is_active: bool
    created_at: datetime
    updated_at: datetime


class DailyDecisionResponse(BaseModel):
    """API response for daily decision."""

    id: str
    user_id: str
    athlete_id: int
    decision: DailyDecision
    weekly_intent_id: str | None
    version: int
    is_active: bool
    created_at: datetime
    updated_at: datetime
