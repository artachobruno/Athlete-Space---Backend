from __future__ import annotations

import datetime as dt
import math
from collections import defaultdict
from collections.abc import Iterable
from statistics import mean, stdev
from typing import Literal

from models.activity import ActivityRecord
from models.training_state import TrainingState


def build_training_state(
    *,
    activities: Iterable[ActivityRecord],
    today: dt.date,
    prev_state: TrainingState | None = None,
) -> TrainingState:
    """Deterministically compute an athlete's TrainingState from recent activities.

    NO decision logic.
    NO recommendations beyond computed metrics.
    """
    activities = list(activities)

    # -----------------------------
    # Windowing
    # -----------------------------
    def in_window(days: int) -> list[ActivityRecord]:
        cutoff = today - dt.timedelta(days=days)
        return [a for a in activities if a.start_time.date() >= cutoff]

    last_7d = in_window(7)
    last_28d = in_window(28)

    acute_load_7d = sum(_activity_load(a) for a in last_7d)
    chronic_load_28d = sum(_activity_load(a) for a in last_28d) / 4 if last_28d else 0.0

    training_stress_balance = chronic_load_28d - acute_load_7d

    # -----------------------------
    # Load trend
    # -----------------------------
    load_trend_7d = _load_trend(last_7d)

    # -----------------------------
    # Monotony
    # -----------------------------
    monotony = _monotony(last_7d)

    # -----------------------------
    # Intensity distribution
    # -----------------------------
    intensity_distribution = _intensity_distribution(last_7d)

    # -----------------------------
    # Risk flags (pure signals)
    # -----------------------------
    risk_flags = []

    if monotony >= 2.0:
        risk_flags.append("HIGH_MONOTONY")

    if prev_state and acute_load_7d > 1.5 * prev_state.chronic_load_28d:
        risk_flags.append("ACUTE_SPIKE")

    if acute_load_7d > chronic_load_28d * 1.2:
        risk_flags.append("OVERREACHING")

    # -----------------------------
    # Recovery + readiness (neutral math)
    # -----------------------------
    recovery_status = _recovery_status(training_stress_balance)
    readiness_score = _readiness_score(training_stress_balance, monotony)

    return TrainingState(
        date=today,
        acute_load_7d=round(acute_load_7d, 1),
        chronic_load_28d=round(chronic_load_28d, 1),
        training_stress_balance=round(training_stress_balance, 1),
        load_trend_7d=load_trend_7d,
        monotony=round(monotony, 2),
        intensity_distribution=intensity_distribution,
        recovery_status=recovery_status,
        readiness_score=readiness_score,
        risk_flags=risk_flags,
        recommended_intent=_recommended_intent(training_stress_balance, risk_flags),
    )


# -------------------------------------------------------------------
# Helpers (PURE FUNCTIONS)
# -------------------------------------------------------------------


def _activity_load(activity: ActivityRecord) -> float:
    """Simple proxy load: duration x effort."""
    if activity.avg_hr:
        return activity.duration_sec * (activity.avg_hr / 100)
    return activity.duration_sec * 0.5


def _load_trend(
    activities: list[ActivityRecord],
) -> Literal["rising", "stable", "falling"]:
    if len(activities) < 4:
        return "stable"

    daily = defaultdict(float)
    for a in activities:
        daily[a.start_time.date()] += _activity_load(a)

    loads = list(daily.values())
    if loads[-1] > mean(loads):
        return "rising"
    if loads[-1] < mean(loads):
        return "falling"
    return "stable"


def _monotony(activities: list[ActivityRecord]) -> float:
    if len(activities) < 3:
        return 0.0

    daily = defaultdict(float)
    for a in activities:
        daily[a.start_time.date()] += _activity_load(a)

    values = list(daily.values())
    if len(values) < 2 or stdev(values) == 0:
        return float("inf")

    return mean(values) / stdev(values)


def _intensity_distribution(activities: list[ActivityRecord]) -> dict[str, float]:
    zones = defaultdict(int)

    for a in activities:
        if not a.avg_hr or a.avg_hr < 140:
            zones["easy"] += 1
        elif a.avg_hr < 165:
            zones["moderate"] += 1
        else:
            zones["hard"] += 1

    total = sum(zones.values()) or 1
    return {k: round(v / total, 2) for k, v in zones.items()}


def _recovery_status(tsb: float) -> Literal["under", "adequate", "over"]:
    if tsb < -20:
        return "over"
    if tsb > 20:
        return "under"
    return "adequate"


def _readiness_score(tsb: float, monotony: float) -> int:
    """Compute readiness score in [0, 100].

    Monotony is capped to prevent numeric overflow while preserving penalty.
    """
    effective_monotony = min(monotony, 5.0) if math.isfinite(monotony) else 5.0

    score = 75 + (tsb / 2) - (effective_monotony * 5)
    return max(0, min(100, int(score)))


def _recommended_intent(
    tsb: float,
    flags: list[str],
) -> Literal["RECOVER", "MAINTAIN", "BUILD"]:
    if "OVERREACHING" in flags or "ACUTE_SPIKE" in flags:
        return "RECOVER"
    if tsb > 10:
        return "BUILD"
    return "MAINTAIN"
