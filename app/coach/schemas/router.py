"""Router response schema for intent classification."""

from pydantic import BaseModel, Field

from app.coach.runtime.intents import CoachIntent


class IntentRouterResponse(BaseModel):
    """Response schema for intent router."""

    intent: CoachIntent = Field(description="One of the allowed coaching intents")
