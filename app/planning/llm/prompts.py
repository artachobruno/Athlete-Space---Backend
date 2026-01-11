"""Phase 4 LLM Prompts for Template Selection.

Strict prompts that enforce bounded selection.
No math, no structure changes, no invention.
"""

SYSTEM_PROMPT = """You are selecting training session templates.

Rules:
- You may ONLY choose from the provided template IDs.
- You MUST choose exactly one template per day.
- You MUST NOT change durations.
- You MUST NOT invent templates.
- You MUST output valid JSON only.
- If unsure, choose the most conservative option.

Your output must be a JSON object with this structure:
{
  "week_index": <number>,
  "selections": {
    "<day>": "<template_id>",
    ...
  }
}

Each selected template_id MUST exist in that day's candidate list."""


def build_selection_prompt(
    week_index: int,
    race_type: str,
    phase: str,
    philosophy_id: str,
    *,
    philosophy_summary: str | None = None,
    days: list[dict[str, str | int | list[str]]],
) -> str:
    """Build user prompt for template selection.

    Args:
        week_index: Zero-based week index
        race_type: Race type
        phase: Training phase
        philosophy_id: Philosophy identifier
        philosophy_summary: Optional philosophy summary from RAG
        days: List of day dictionaries with candidates

    Returns:
        Formatted prompt string
    """
    prompt_parts = [
        f"Week {week_index + 1} Template Selection",
        "",
        f"Race Type: {race_type}",
        f"Phase: {phase}",
        f"Philosophy: {philosophy_id}",
    ]

    if philosophy_summary:
        prompt_parts.append("")
        prompt_parts.append("Philosophy Summary:")
        prompt_parts.append(philosophy_summary)

    prompt_parts.append("")
    prompt_parts.append("Select exactly one template for each day:")

    for day_data in days:
        day = day_data["day"]
        role = day_data["role"]
        duration = day_data["duration_minutes"]
        candidates = day_data["candidates"]

        # Ensure candidates is a list of strings
        if isinstance(candidates, list):
            candidates_list: list[str] = [str(c) for c in candidates]
        else:
            candidates_list = [str(candidates)]

        prompt_parts.append("")
        prompt_parts.append(f"Day: {day} ({role})")
        prompt_parts.append(f"Duration: {duration} minutes")
        prompt_parts.append(f"Candidates: {', '.join(candidates_list)}")

    prompt_parts.append("")
    prompt_parts.append("Output JSON with selections:")

    return "\n".join(prompt_parts)
