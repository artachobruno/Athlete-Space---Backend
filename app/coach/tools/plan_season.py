from loguru import logger


def plan_season() -> str:
    """High-level season planning."""
    logger.info("Tool plan_season called")
    return (
        "📆 Season planning framework:\n\n"
        "1 Base Phase (8-12 weeks)\n"
        "- Aerobic volume\n"
        "- Strength and durability\n\n"
        "2 Build Phase (6-8 weeks)\n"
        "- Race-specific intensity\n"
        "- Structured workouts\n\n"
        "3 Peak & Race Block\n"
        "- Specificity\n"
        "- Reduced volume\n\n"
        "4 Recovery / Reset\n"
        "- 1-3 weeks easy\n\n"
        "If you share target races and dates, I can generate a full season plan."
    )
