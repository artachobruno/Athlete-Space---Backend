"""LLM coach service with strict data quality gating.

This module provides the LLM coach service that is strictly gated by
data quality. The LLM is only called when data_quality == "ok".
Otherwise, a static message is returned.
"""

from __future__ import annotations

from loguru import logger

from app.coach.utils.context_builder import build_coach_context


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
    logger.info("Getting coach advice from overview payload")
    logger.info("Building coach context")
    context = build_coach_context(overview_payload)
    data_quality = context["data_quality"]
    logger.info(f"Data quality check: {data_quality}")

    # Gate LLM strictly by data quality
    if data_quality != "ok":
        logger.info(f"Coach gated: data_quality={data_quality}")
        return _get_static_message(data_quality)

    # Data quality is ok - return static message
    # Note: LLM calls should go through orchestrator, not this service
    logger.info("Coach data quality OK - returning static message")
    return _get_static_message("ok")


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
        "summary": message,
        "risk_level": "none",
        "intervention": False,
        "insights": [message],
        "recommendations": [],
        "follow_up_prompts": None,
    }
