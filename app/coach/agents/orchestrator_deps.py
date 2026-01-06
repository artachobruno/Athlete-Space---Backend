"""Dependencies for the Coach Orchestrator Agent.

Provides context and dependencies needed by the pydantic_ai agent.
"""

from pydantic import BaseModel

from app.coach.schemas.athlete_state import AthleteState


class CoachDeps(BaseModel):
    """Dependencies for the Coach Orchestrator Agent.

    Provides context that tools and the agent can access.
    """

    athlete_id: int
    user_id: str | None = None  # User ID (Clerk) - used for storing planned sessions
    athlete_state: AthleteState | None = None
    days: int = 60
    days_to_race: int | None = None
