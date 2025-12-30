def plan_race_build(message: str) -> str:
    """Plan a race build from short to ultra distances."""
    distance = message.lower()

    if "5k" in distance:
        focus = "speed, VO₂max, neuromuscular efficiency"
    elif "10k" in distance:
        focus = "threshold and aerobic power"
    elif "half" in distance:
        focus = "threshold durability and aerobic volume"
    elif "marathon" in distance:
        focus = "aerobic volume and long-run specificity"
    elif "100" in distance or "ultra" in distance:
        focus = "fat oxidation, durability, time-on-feet"
    else:
        focus = "general endurance"

    return (
        f"Race build focus: {focus}\n\n"
        "📅 Typical structure:\n"
        "- Base → Build → Peak → Taper\n"
        "- 2 quality days/week max\n"
        "- Long run progression aligned with race distance\n\n"
        "Tell me the race date to generate a detailed build."
    )
