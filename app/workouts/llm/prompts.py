"""Prompt templates for LLM workout interpretation.

Bounded, factual prompts that instruct the LLM to:
- Never invent numbers
- Never contradict deterministic metrics
- Never give medical advice
- Focus on actionable coaching feedback
"""

from __future__ import annotations


def build_step_prompt(
    step_type: str,
    planned_target: str,
    time_in_range_pct: float,
    overshoot_pct: float,
    undershoot_pct: float,
    pause_seconds: int,
    weather: str | None = None,
    fatigue: str | None = None,
) -> str:
    """Build step-level interpretation prompt.

    Args:
        step_type: Step type (warmup, steady, interval, recovery, cooldown, free)
        planned_target: Human-readable planned target description
        time_in_range_pct: Percentage of time in target range (0-100)
        overshoot_pct: Percentage of time overshooting target (0-100)
        undershoot_pct: Percentage of time undershooting target (0-100)
        pause_seconds: Total pause time in seconds
        weather: Optional weather context
        fatigue: Optional fatigue context

    Returns:
        Formatted prompt string
    """
    context_parts: list[str] = []
    if weather:
        context_parts.append(f"- Weather: {weather}")
    if fatigue:
        context_parts.append(f"- Fatigue: {fatigue}")

    context_section = "\n".join(context_parts) if context_parts else "- No additional context available"

    return f"""You are a professional endurance coach.

You are given factual execution metrics for ONE workout step.
You MUST NOT invent numbers or contradict them.

Step type: {step_type}
Planned target: {planned_target}
Execution metrics:
- Time in range (%): {time_in_range_pct:.1f}
- Overshoot (%): {overshoot_pct:.1f}
- Undershoot (%): {undershoot_pct:.1f}
- Paused (sec): {pause_seconds}

Context:
{context_section}

Your task:
1. Rate execution (excellent/good/ok/needs_work)
2. Briefly explain why
3. Give ONE actionable coaching tip

Output JSON only."""


def build_workout_prompt(
    overall_compliance_pct: float,
    total_pause_seconds: int,
    completed: bool,
    step_summaries: list[str],
) -> str:
    """Build workout-level interpretation prompt.

    Args:
        overall_compliance_pct: Overall compliance percentage (0-100)
        total_pause_seconds: Total pause time across all steps
        completed: Whether workout was completed
        step_summaries: List of brief step-level summaries

    Returns:
        Formatted prompt string
    """
    step_summaries_text = "\n".join(f"- {summary}" for summary in step_summaries) if step_summaries else "- No step details available"

    return f"""You are reviewing a complete workout execution.

You are given:
- Step-level compliance results
- Overall compliance percentage: {overall_compliance_pct:.1f}%
- Total pause time: {total_pause_seconds} seconds
- Completed: {completed}

Step summaries:
{step_summaries_text}

Do NOT restate numbers unless necessary.
Do NOT give medical advice.

Decide whether the workout intent was:
- successful
- partially successful
- missed

Explain briefly in plain language.

Output JSON only."""
