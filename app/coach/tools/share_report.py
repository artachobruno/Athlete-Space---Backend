from datetime import datetime, timezone

from app.coach.models import AthleteState


def _get_status_from_tsb(tsb: float) -> str:
    """Get status string based on TSB value."""
    if tsb > 10:
        return "EXCELLENT - Very fresh, ready for quality work"
    if tsb > 5:
        return "GOOD - Well-balanced, training effectively"
    if tsb > -5:
        return "NORMAL - Standard training fatigue"
    if tsb > -12:
        return "CAUTION - Elevated fatigue, monitor closely"
    return "ALERT - High fatigue, recovery recommended"


def _format_training_status(tsb: float) -> list[str]:
    """Format training status section."""
    if tsb > 5:
        return [
            "You are in a fresh state with good recovery.\n",
            "This is an optimal time for quality workouts and\n",
            "intensity-focused training blocks.\n\n",
        ]
    if tsb > -5:
        return [
            "Your training load is balanced and sustainable.\n",
            "Continue with your current training structure while\n",
            "monitoring daily feedback.\n\n",
        ]
    return [
        "Fatigue levels are elevated. Consider reducing training\n",
        "load or adding additional recovery time. Prioritize\n",
        "sleep, nutrition, and easy aerobic work.\n\n",
    ]


def _format_risk_assessment(flags: list[str]) -> list[str]:
    """Format risk assessment section."""
    if not flags:
        return []

    parts = ["⚠️  RISK ASSESSMENT\n"]
    flag_messages = {
        "OVERREACHING": "  • Overreaching detected - high fatigue accumulation\n",
        "HIGH_MONOTONY": "  • Training lacks variation\n",
        "ACUTE_SPIKE": "  • Recent sharp increase in training load\n",
        "INSUFFICIENT_RECOVERY": "  • Recovery time may be inadequate\n",
    }

    parts.extend(flag_messages.get(flag, f"  • {flag}\n") for flag in flags)

    parts.append("\n")
    return parts


def _format_recommendations(tsb: float) -> list[str]:
    """Format recommendations section."""
    parts = ["💡 RECOMMENDATIONS\n"]

    if tsb < -12:
        parts.extend([
            "  1. Reduce training volume by 30-40%\n",
            "  2. Replace intensity sessions with easy aerobic work\n",
            "  3. Ensure 1-2 full rest days this week\n",
            "  4. Focus on sleep (8+ hours) and recovery nutrition\n",
            "  5. Resume quality work when TSB improves above -8\n",
        ])
    elif tsb > 5:
        parts.extend([
            "  1. This is an optimal window for quality workouts\n",
            "  2. Can safely increase intensity or volume\n",
            "  3. Consider targeting key training sessions\n",
            "  4. Monitor adaptation to increased load\n",
        ])
    else:
        parts.extend([
            "  1. Maintain current training structure\n",
            "  2. Continue monitoring fatigue markers daily\n",
            "  3. Adjust based on how you feel\n",
            "  4. Ensure regular rest days (every 7-10 days)\n",
        ])

    return parts


def _format_load_trend_analysis(load_trend: str) -> list[str]:
    """Format load trend analysis section."""
    parts = ["📈 LOAD TREND ANALYSIS\n"]

    if load_trend == "rising":
        parts.extend([
            "Training load is increasing, which is appropriate for\n",
            "building fitness. Monitor fatigue closely to ensure\n",
            "sustainable adaptation.\n",
        ])
    elif load_trend == "stable":
        parts.extend([
            "Training load is stable, indicating good consistency.\n",
            "This pattern supports long-term adaptation and\n",
            "reduces injury risk.\n",
        ])
    else:
        parts.extend([
            "Training load is decreasing. This may indicate a\n",
            "planned taper, recovery period, or need to rebuild\n",
            "after high load.\n",
        ])

    parts.append("\n")
    return parts


def share_report(state: AthleteState) -> str:
    """Generate a shareable training report.

    Args:
        state: Current athlete state with all metrics.

    Returns:
        A formatted, shareable training report.
    """
    report_date = datetime.now(timezone.utc).strftime("%B %d, %Y")

    report = [
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
        "   VIRTUS AI TRAINING REPORT",
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n",
        f"Date: {report_date}\n",
        "📋 EXECUTIVE SUMMARY\n",
        f"Overall Status: {_get_status_from_tsb(state.tsb)}\n",
        f"Load Trend: {state.load_trend.upper()}\n\n",
        "📊 KEY METRICS\n",
        f"  Fitness (CTL):        {state.ctl:.1f}\n",
        f"  Fatigue (ATL):        {state.atl:.1f}\n",
        f"  Form (TSB):           {state.tsb:.1f}\n",
        f"  7-Day Volume:         {state.seven_day_volume_hours:.1f} hours\n",
        f"  14-Day Volume:        {state.fourteen_day_volume_hours:.1f} hours\n\n",
        "🎯 TRAINING STATUS\n",
    ]

    report.extend(_format_training_status(state.tsb))
    report.extend(_format_risk_assessment(state.flags))
    report.extend(_format_recommendations(state.tsb))
    report.append("\n")
    report.extend(_format_load_trend_analysis(state.load_trend))

    report.extend([
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n",
        "Generated by Virtus AI Training Intelligence\n",
        "For questions, consult with your coach\n",
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
    ])

    return "\n".join(report)
