"""LLM coach service with strict data quality gating.

This module provides the LLM coach service that is strictly gated by
data quality. The LLM is only called when data_quality == "ok".
Otherwise, a static message is returned.
"""

from __future__ import annotations

from loguru import logger

from app.coach.agent import run_coach_chain
from app.coach.context_builder import build_coach_context
from app.coach.state_builder import build_athlete_state


def get_coach_advice(overview_payload: dict) -> dict:
    """Get coach advice from overview payload.

    Args:
        overview_payload: Response from /me/overview endpoint

    Returns:
        Dictionary with coach advice (CoachAgentResponse format)

    Rules:
        - If data_quality != "ok": Return static message, do NOT call LLM
        - If data_quality == "ok": Call LLM with structured context
        - No hallucinations
        - No advice without data
    """
    context = build_coach_context(overview_payload)
    data_quality = context["data_quality"]

    # Gate LLM strictly by data quality
    if data_quality != "ok":
        logger.info(f"Coach gated: data_quality={data_quality}")
        return _get_static_message(data_quality)

    # Data quality is ok - call LLM
    logger.info("Coach calling LLM: data_quality=ok")

    # Build athlete state from context
    metrics = context["metrics"]

    # Build minimal daily_load for state_builder (it needs some data)
    # Use a simple pattern based on current metrics
    # Note: build_athlete_state will calculate load_trend internally from daily_load
    daily_load = [metrics["ctl_today"] / 7.0] * 14  # Approximate daily load

    athlete_state = build_athlete_state(
        ctl=metrics["ctl_today"],
        atl=metrics["atl_today"],
        tsb=metrics["tsb_today"],
        daily_load=daily_load,
        days_to_race=None,
    )

    try:
        coach_response = run_coach_chain(athlete_state)
        logger.info(
            "Coach LLM response generated",
            risk_level=coach_response.risk_level,
            intervention=coach_response.intervention,
        )
        return coach_response.model_dump()
    except Exception as e:
        logger.error(f"Error calling LLM coach: {e}", exc_info=True)
        # Fallback to static message on LLM error
        return _get_static_message("insufficient")


def _get_static_message(data_quality: str) -> dict:
    """Get static message when data quality is insufficient.

    Args:
        data_quality: Data quality status

    Returns:
        Dictionary in CoachAgentResponse format
    """
    if data_quality == "insufficient":
        message = (
            "I need at least 14 days of training data to provide meaningful insights. "
            "Please sync your activities and check back in a few days."
        )
    elif data_quality == "limited":
        message = (
            "Your training data has some gaps. I can provide limited insights, but more consistent data will improve my recommendations."
        )
    else:
        message = "I'm unable to provide coaching advice at this time due to data quality issues."

    # Return in CoachAgentResponse format
    return {
        "risk_level": "unknown",
        "intervention": "none",
        "insights": [message],
        "recommendations": [],
        "follow_ups": [],
    }
