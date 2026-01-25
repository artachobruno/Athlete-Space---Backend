"""Examples: Using Canonical Coach Vocabulary in LLM Prompts.

This module demonstrates how to integrate the canonical coach vocabulary
system into LLM prompts and coach text generation.

Key Principle:
> "The LLM is a consumer of your language system, not the author of it."

The LLM should reference canonical workout names but never invent new ones.
This ensures tone consistency and prevents language drift.
"""

from typing import TYPE_CHECKING

from app.coach.vocabulary import (
    CoachVocabularyLevel,
    resolve_workout_display_name,
)

if TYPE_CHECKING:
    from app.db.models import UserSettings
    from app.planning.materialization.models import ConcreteSession


def get_user_vocabulary_level(settings: "UserSettings | None") -> CoachVocabularyLevel:
    """Get user's vocabulary level from settings, defaulting to intermediate.

    Args:
        settings: User settings object (may be None)

    Returns:
        Coach vocabulary level ('foundational', 'intermediate', or 'advanced')
    """
    if not settings:
        return "intermediate"

    level = settings.vocabulary_level
    if level in {"foundational", "intermediate", "advanced"}:
        return level  # type: ignore[return-value]

    return "intermediate"


def build_llm_prompt_with_canonical_name(
    session: "ConcreteSession",
    settings: "UserSettings | None",
) -> str:
    """Build LLM prompt that references canonical workout name.

    Example of correct LLM integration:
    - LLM prompt includes canonical name
    - LLM references the name but doesn't invent it
    - LLM provides context around the canonical name

    Args:
        session: Concrete session with sport and intent
        settings: User settings (for vocabulary level)

    Returns:
        Formatted prompt string with canonical workout name
    """
    vocabulary_level = get_user_vocabulary_level(settings)

    # Resolve canonical workout name
    canonical_name = resolve_workout_display_name(
        sport=session.sport,
        intent=session.intent,
        vocabulary_level=vocabulary_level,
        title=session.title,
    )

    # Build prompt that references canonical name
    return f"""Today's session: {canonical_name}

This is a {session.intent} session focusing on {_get_intent_purpose(session.intent)}.

Provide brief coaching instructions for this session.
Focus on:
- Session purpose and intent
- Pacing cues and feel
- Fatigue expectations
- Execution tips

Do NOT:
- Invent new workout names (use: {canonical_name})
- Include specific numbers, distances, or durations
- Modify the workout structure

Reference the session as: {canonical_name}
"""


def _get_intent_purpose(intent: str | None) -> str:
    """Get purpose description for intent (helper for prompts)."""
    if not intent:
        return "aerobic development"

    intent_lower = intent.lower()
    if "easy" in intent_lower or "recovery" in intent_lower:
        return "aerobic recovery and adaptation"
    if "tempo" in intent_lower or "threshold" in intent_lower:
        return "lactate threshold development"
    if "interval" in intent_lower or "vo2" in intent_lower:
        return "VO₂max development"
    if "long" in intent_lower or "endurance" in intent_lower:
        return "aerobic endurance development"

    return "aerobic development"


# Example: Weekly Report Integration
def build_weekly_report_context_with_canonical_names(
    sessions: list["ConcreteSession"],
    settings: "UserSettings | None",
) -> str:
    """Build weekly report context using canonical workout names.

    Example showing how to use canonical names in weekly narratives.

    Args:
        sessions: List of sessions for the week
        settings: User settings (for vocabulary level)

    Returns:
        Formatted context string with canonical names
    """
    vocabulary_level = get_user_vocabulary_level(settings)

    session_names = []
    for session in sessions:
        canonical_name = resolve_workout_display_name(
            sport=session.sport,
            intent=session.intent,
            vocabulary_level=vocabulary_level,
            title=session.title,
        )
        session_names.append(f"- {canonical_name}")

    return f"""Weekly Training Summary:

Planned sessions this week:
{chr(10).join(session_names)}

When generating the weekly report:
- Reference sessions using these exact names
- Do NOT invent new workout names
- Use the vocabulary level: {vocabulary_level}
- Maintain consistent coach voice
"""


# Example: Session Title Generation (Fallback)
def generate_session_title_with_canonical_name(
    sport: str | None,
    intent: str | None,
    settings: "UserSettings | None",
    title: str | None = None,
) -> str:
    """Generate session title using canonical vocabulary.

    Use this instead of generating titles from template_kind or inventing names.

    Args:
        sport: Backend sport type
        intent: Backend intent type
        settings: User settings (for vocabulary level)
        title: Optional title for sport normalization

    Returns:
        Canonical workout display name
    """
    vocabulary_level = get_user_vocabulary_level(settings)

    return resolve_workout_display_name(
        sport=sport,
        intent=intent,
        vocabulary_level=vocabulary_level,
        title=title,
    )


# Example: LLM Prompt Guardrails
LLM_VOCABULARY_GUARDRAILS = """
IMPORTANT: Coach Vocabulary Rules

1. You MUST use the provided canonical workout name exactly as given.
   Example: If provided "Aerobic Maintenance Run", use that exact phrase.

2. You MUST NOT invent new workout names.
   ❌ Bad: "Today's Zen Run focuses on..."
   ✅ Good: "Today's {canonical_name} focuses on..."

3. You MUST NOT modify the canonical name.
   ❌ Bad: "Aerobic Maintenance Running"
   ✅ Good: "Aerobic Maintenance Run"

4. You can provide context and explanation around the name, but the name itself
   must remain unchanged.

5. The vocabulary level ({vocabulary_level}) determines the technical depth
   of your explanations, but the workout name is fixed.
"""


def add_vocabulary_guardrails_to_prompt(
    base_prompt: str,
    canonical_name: str,
    vocabulary_level: CoachVocabularyLevel,
) -> str:
    """Add vocabulary guardrails to LLM prompt.

    Args:
        base_prompt: Base prompt text
        canonical_name: Canonical workout name to use
        vocabulary_level: User's vocabulary level

    Returns:
        Prompt with guardrails added
    """
    guardrails = LLM_VOCABULARY_GUARDRAILS.format(
        canonical_name=canonical_name,
        vocabulary_level=vocabulary_level,
    )

    return f"""{base_prompt}

{guardrails}

Canonical workout name to use: {canonical_name}
Vocabulary level: {vocabulary_level}
"""
