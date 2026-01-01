from loguru import logger

from app.coach.models import AthleteState


def _format_load_metrics(ctl: float, atl: float, tsb: float, load_trend: str, volatility: str) -> list[str]:
    """Format load metrics section."""
    return [
        "📈 Load Metrics:\n",
        f"  CTL (Fitness): {ctl:.1f}\n",
        f"  ATL (Fatigue): {atl:.1f}\n",
        f"  TSB (Form): {tsb:.1f}\n",
        f"  Load Trend: {load_trend.upper()}\n",
        f"  Volatility: {volatility.upper()}\n\n",
    ]


def _format_volume_analysis(seven_day_volume: float, fourteen_day_volume: float) -> list[str]:
    """Format volume analysis section."""
    parts = [
        "⏱️ Volume Analysis:\n",
        f"  7-day volume: {seven_day_volume:.1f} hours\n",
        f"  14-day volume: {fourteen_day_volume:.1f} hours\n",
    ]

    if fourteen_day_volume > 0:
        volume_ratio = (seven_day_volume * 2) / fourteen_day_volume
        if volume_ratio > 1.15:
            parts.append("  ⚠️ Recent volume is high relative to 14-day average\n")
        elif volume_ratio < 0.85:
            parts.append("  📉 Recent volume is low - may be tapering or recovering\n")
        else:
            parts.append("  ✅ Volume is consistent\n")

    parts.append("\n")
    return parts


def _format_readiness(tsb: float, days_since_rest: int) -> list[str]:
    """Format readiness and fatigue section."""
    parts = ["💪 Readiness & Fatigue:\n"]

    if tsb > 10:
        parts.extend([
            f"  Status: VERY FRESH (TSB: {tsb:.1f})\n",
            "  Interpretation: Well-recovered, ready for quality work\n",
        ])
    elif tsb > 5:
        parts.extend([
            f"  Status: FRESH (TSB: {tsb:.1f})\n",
            "  Interpretation: Good balance, can handle intensity\n",
        ])
    elif tsb > -5:
        parts.extend([
            f"  Status: BALANCED (TSB: {tsb:.1f})\n",
            "  Interpretation: Normal training fatigue, sustainable\n",
        ])
    elif tsb > -12:
        parts.extend([
            f"  Status: ELEVATED FATIGUE (TSB: {tsb:.1f})\n",
            "  Interpretation: Consider reducing load or adding recovery\n",
        ])
    else:
        parts.extend([
            f"  Status: HIGH FATIGUE (TSB: {tsb:.1f})\n",
            "  Interpretation: Recovery recommended, reduce intensity\n",
        ])

    if days_since_rest >= 7:
        parts.append(f"  ⚠️ No rest day in {days_since_rest} days - consider recovery\n")
    parts.append("\n")
    return parts


def _format_flags(flags: list[str]) -> list[str]:
    """Format risk flags section."""
    if not flags:
        return ["✅ No major risk flags detected\n\n"]

    parts = ["🚩 Risk Flags:\n"]
    flag_messages = {
        "OVERREACHING": "  ⚠️ OVERREACHING: High fatigue accumulation detected\n",
        "HIGH_MONOTONY": "  ⚠️ HIGH_MONOTONY: Training lacks variation\n",
        "ACUTE_SPIKE": "  ⚠️ ACUTE_SPIKE: Sudden load increase detected\n",
        "INSUFFICIENT_RECOVERY": "  ⚠️ INSUFFICIENT_RECOVERY: Recovery time may be inadequate\n",
    }

    parts.extend(flag_messages.get(flag, f"  ⚠️ {flag}\n") for flag in flags)

    parts.append("\n")
    return parts


def _format_volatility(volatility: str) -> list[str]:
    """Format volatility assessment section."""
    parts = ["📊 Training Consistency:\n"]

    if volatility == "high":
        parts.extend([
            "  ⚠️ High volatility: Training load varies significantly\n",
            "  Recommendation: Aim for more consistent weekly patterns\n",
        ])
    elif volatility == "medium":
        parts.extend([
            "  ⚠️ Moderate volatility: Some variation in training load\n",
            "  Recommendation: Monitor and stabilize if fatigue increases\n",
        ])
    else:
        parts.extend([
            "  ✅ Low volatility: Consistent training pattern\n",
            "  Status: Good for long-term adaptation\n",
        ])

    parts.append("\n")
    return parts


def _format_load_trend(load_trend: str, tsb: float) -> list[str]:
    """Format load trend analysis section."""
    parts = ["📈 Load Trend Analysis:\n"]

    if load_trend == "rising":
        if tsb > -5:
            parts.extend([
                "  ✅ Building fitness: Load increasing sustainably\n",
                "  Status: Good adaptation potential\n",
            ])
        else:
            parts.extend([
                "  ⚠️ Rising load with elevated fatigue\n",
                "  Recommendation: Monitor closely, may need to flatten\n",
            ])
    elif load_trend == "stable":
        parts.extend([
            "  ✅ Stable load: Good maintenance phase\n",
            "  Status: Sustainable pattern\n",
        ])
    elif tsb > 0:
        parts.extend([
            "  📉 Decreasing load with positive form\n",
            "  Interpretation: Possible taper or recovery period\n",
        ])
    else:
        parts.extend([
            "  📉 Decreasing load but fatigue persists\n",
            "  Recommendation: Continue recovery focus\n",
        ])

    parts.append("\n")
    return parts


def _format_recommendations(tsb: float, volatility: str) -> list[str]:
    """Format key recommendations section."""
    parts = ["💡 Key Recommendations:\n"]

    if tsb < -12:
        parts.extend([
            "  1. Prioritize recovery - reduce volume/intensity\n",
            "  2. Ensure adequate sleep and nutrition\n",
            "  3. Resume quality work when TSB improves above -8\n",
        ])
    elif tsb > 5:
        parts.extend([
            "  1. Good time for quality workouts\n",
            "  2. Can safely add intensity or volume\n",
            "  3. Monitor response to increased load\n",
        ])
    else:
        parts.extend([
            "  1. Maintain current training structure\n",
            "  2. Monitor fatigue markers\n",
            "  3. Adjust based on daily feedback\n",
        ])

    if volatility == "high":
        parts.append("  4. Work toward more consistent weekly patterns\n")

    return parts


def run_analysis(state: AthleteState) -> str:
    """Run comprehensive training analysis on current state.

    Args:
        state: Current athlete state with all metrics.

    Returns:
        Detailed analysis of training state, trends, and insights.
    """
    logger.info(f"Tool run_analysis called (CTL={state.ctl:.1f}, ATL={state.atl:.1f}, TSB={state.tsb:.1f}, flags={state.flags})")
    analysis_parts = [
        "📊 Training Analysis Report\n",
        "=" * 50 + "\n",
    ]

    analysis_parts.extend(_format_load_metrics(state.ctl, state.atl, state.tsb, state.load_trend, state.volatility))
    analysis_parts.extend(_format_volume_analysis(state.seven_day_volume_hours, state.fourteen_day_volume_hours))
    analysis_parts.extend(_format_readiness(state.tsb, state.days_since_rest))
    analysis_parts.extend(_format_flags(state.flags))
    analysis_parts.extend(_format_volatility(state.volatility))
    analysis_parts.extend(_format_load_trend(state.load_trend, state.tsb))
    analysis_parts.extend(_format_recommendations(state.tsb, state.volatility))
    analysis_parts.append(f"\n📊 Analysis Confidence: {state.confidence * 100:.0f}%\n")

    return "".join(analysis_parts)
