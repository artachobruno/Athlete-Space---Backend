"""Intent-specific UX patterns and canonical phrasing.

This module defines the canonical user-facing language for each intent type.
These patterns guide the Style LLM to produce consistent, intent-appropriate messaging.

Each intent has a distinct "voice" that users should recognize.
"""

from typing import Literal

IntentType = Literal[
    "clarify",
    "propose",
    "confirm",
    "recommend",
    "explain",
    "plan",
    "modify",
    "adjust",
    "log",
    "question",
    "general",
]


# Canonical phrasing patterns for each intent
INTENT_PHRASING: dict[IntentType, dict[str, str]] = {
    "clarify": {
        "pattern": "I just need one more detail before I can proceed…",
        "tone": "helpful, direct, minimal",
        "example": "I just need one more detail before I can proceed: What is the date of your race?",
    },
    "propose": {
        "pattern": "I'd suggest the following change — want me to apply it?",
        "tone": "collaborative, clear, non-committal",
        "example": "I'd suggest reducing your weekly volume by 10% to improve recovery. Want me to apply this change?",
    },
    "confirm": {
        "pattern": "Got it — I'll apply that change now.",
        "tone": "confident, action-oriented, brief",
        "example": "Got it — I'll apply that change now. Your plan has been updated.",
    },
    "recommend": {
        "pattern": "Here's what I'd do next…",
        "tone": "suggestive, supportive, actionable",
        "example": "Here's what I'd do next: a 45-minute easy run with 4x20-second strides at the end.",
    },
    "explain": {
        "pattern": "Here's what's happening with your training…",
        "tone": "informative, clear, contextual",
        "example": "Here's what's happening with your training: Your TSB is at 5.0, indicating good recovery.",
    },
    "plan": {
        "pattern": "I've created your training plan…",
        "tone": "accomplished, structured, complete",
        "example": "I've created your training plan for the next 16 weeks leading up to your marathon.",
    },
    "modify": {
        "pattern": "I've updated your plan with those changes…",
        "tone": "confirmatory, precise, respectful",
        "example": "I've updated your plan with those changes. The modifications are now active.",
    },
    "adjust": {
        "pattern": "I've adjusted your training load…",
        "tone": "calibrated, responsive, measured",
        "example": "I've adjusted your training load to better match your current fitness level.",
    },
    "log": {
        "pattern": "I've recorded that workout…",
        "tone": "acknowledging, factual, brief",
        "example": "I've recorded that workout. It's now part of your training history.",
    },
    "question": {
        "pattern": "Here's what I know about that…",
        "tone": "informative, helpful, conversational",
        "example": "Here's what I know about that: TSB measures your training stress balance.",
    },
    "general": {
        "pattern": "I'm here to help with your training…",
        "tone": "welcoming, supportive, open",
        "example": "I'm here to help with your training. What would you like to know?",
    },
}


def get_intent_phrasing(intent: IntentType) -> dict[str, str]:
    """Get canonical phrasing pattern for an intent.

    Args:
        intent: Intent type

    Returns:
        Dictionary with pattern, tone, and example
    """
    return INTENT_PHRASING.get(intent, INTENT_PHRASING["general"])


def get_intent_tone(intent: IntentType) -> str:
    """Get the tone description for an intent.

    Args:
        intent: Intent type

    Returns:
        Tone description string
    """
    phrasing = get_intent_phrasing(intent)
    return phrasing.get("tone", "conversational")


def get_intent_pattern(intent: IntentType) -> str:
    """Get the canonical pattern for an intent.

    Args:
        intent: Intent type

    Returns:
        Pattern string
    """
    phrasing = get_intent_phrasing(intent)
    return phrasing.get("pattern", "I'm here to help.")


def build_intent_context_for_style_llm(intent: IntentType, base_message: str) -> str:
    """Build context string for Style LLM based on intent.

    This adds intent-specific guidance to help the Style LLM produce
    appropriate phrasing for each intent type.

    Args:
        intent: Intent type
        base_message: Base message from executor

    Returns:
        Context string to include in Style LLM prompt
    """
    phrasing = get_intent_phrasing(intent)
    pattern = phrasing.get("pattern", "")
    tone = phrasing.get("tone", "")

    context = f"Intent: {intent}\n"
    context += f"Canonical pattern: {pattern}\n"
    context += f"Tone: {tone}\n"
    context += f"Base message: {base_message}\n"

    return context
