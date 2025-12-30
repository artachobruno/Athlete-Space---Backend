from app.coach.models import AthleteState


def explain_training_state(state: AthleteState) -> str:
    """Explain current fitness, fatigue, and readiness in plain language."""
    explanation = [
        f"Current CTL (fitness): {state.ctl:.1f}",
        f"Current ATL (fatigue): {state.atl:.1f}",
        f"Training Stress Balance (TSB): {state.tsb:.1f}",
        "",
    ]

    if state.tsb < -10:
        explanation.append(
            "You are carrying significant fatigue. Fitness is improving, but recovery needs attention to avoid overreaching."
        )
    elif state.tsb < 0:
        explanation.append("You are in a productive training zone. Fatigue is present but manageable.")
    else:
        explanation.append("You are well recovered. This is a good window for harder sessions or racing.")

    return "\n".join(explanation)
