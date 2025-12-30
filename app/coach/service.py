from loguru import logger

from app.coach.agent import run_coach_chain
from app.coach.models import AthleteState
from app.coach.responses import CoachAgentResponse


def run_coach_agent(
    athlete_state: AthleteState,
) -> CoachAgentResponse:
    logger.info(
        "Running Coach Agent",
        tsb=athlete_state.tsb,
        load_trend=athlete_state.load_trend,
        flags=athlete_state.flags,
    )

    # Sync call wrapped in async boundary (OK)
    result = run_coach_chain(athlete_state)

    logger.info(
        "Coach Agent completed",
        risk_level=result.risk_level,
        intervention=result.intervention,
    )

    return result
