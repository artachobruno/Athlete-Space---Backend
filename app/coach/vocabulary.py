"""
Canonical Coach Vocabulary System

This module provides the shared language layer that sits between internal logic
and presentation. It provides deterministic, coach-written workout names that
are used consistently across:

- UI card titles
- Weekly narrative text
- Modal narrative blocks
- LLM coach responses (as a consumer, not generator)

The LLM coach consumes this vocabulary but never generates it.
This ensures tone consistency and prevents language drift.

Architecture:
1. Internal Intent / Logic (machine truth: 'easy', 'tempo', etc.)
2. Canonical Coach Language (this layer: 'Aerobic Maintenance Run', etc.)
3. Presentation Layer (UI cards, LLM responses, narratives)
"""

from enum import Enum
from typing import Literal

CoachVocabularyLevel = Literal["foundational", "intermediate", "advanced"]

# Calendar sport types (matches frontend CalendarSport)
CalendarSport = Literal["run", "ride", "swim", "strength", "race", "other"]

# Calendar intent types (matches frontend CalendarIntent)
CalendarIntent = Literal["easy", "steady", "tempo", "intervals", "long", "rest"]

# Canonical workout display names
# Structure: workout_display_names[sport][intent][vocabulary_level]
WORKOUT_DISPLAY_NAMES: dict[
    CalendarSport,
    dict[CalendarIntent, dict[CoachVocabularyLevel, str]],
] = {
    "run": {
        "easy": {
            "foundational": "Easy Aerobic Run",
            "intermediate": "Aerobic Maintenance Run",
            "advanced": "Aerobic Capacity Maintenance",
        },
        "steady": {
            "foundational": "Steady Endurance Run",
            "intermediate": "Aerobic Durability Run",
            "advanced": "Aerobic Base Development",
        },
        "tempo": {
            "foundational": "Steady Pace Run",
            "intermediate": "Controlled Tempo Session",
            "advanced": "Lactate Threshold Tempo",
        },
        "intervals": {
            "foundational": "Speed Intervals",
            "intermediate": "Interval Session",
            "advanced": "VO₂max Intervals",
        },
        "long": {
            "foundational": "Long Endurance Run",
            "intermediate": "Aerobic Durability Run",
            "advanced": "Marathon-Specific Endurance",
        },
        "rest": {
            "foundational": "Rest Day",
            "intermediate": "Recovery Day",
            "advanced": "Adaptation Day",
        },
    },
    "ride": {
        "easy": {
            "foundational": "Easy Aerobic Ride",
            "intermediate": "Aerobic Maintenance Ride",
            "advanced": "Aerobic Capacity Maintenance",
        },
        "steady": {
            "foundational": "Steady Endurance Ride",
            "intermediate": "Aerobic Durability Ride",
            "advanced": "Aerobic Base Development",
        },
        "tempo": {
            "foundational": "Steady Pace Ride",
            "intermediate": "Controlled Tempo Session",
            "advanced": "Lactate Threshold Tempo",
        },
        "intervals": {
            "foundational": "Speed Intervals",
            "intermediate": "Interval Session",
            "advanced": "VO₂max Intervals",
        },
        "long": {
            "foundational": "Long Endurance Ride",
            "intermediate": "Aerobic Durability Ride",
            "advanced": "Endurance Base Development",
        },
        "rest": {
            "foundational": "Rest Day",
            "intermediate": "Recovery Day",
            "advanced": "Adaptation Day",
        },
    },
    "swim": {
        "easy": {
            "foundational": "Easy Aerobic Swim",
            "intermediate": "Aerobic Maintenance Swim",
            "advanced": "Aerobic Capacity Maintenance",
        },
        "steady": {
            "foundational": "Steady Endurance Swim",
            "intermediate": "Aerobic Durability Swim",
            "advanced": "Aerobic Base Development",
        },
        "tempo": {
            "foundational": "Steady Pace Swim",
            "intermediate": "Controlled Tempo Session",
            "advanced": "Lactate Threshold Tempo",
        },
        "intervals": {
            "foundational": "Speed Intervals",
            "intermediate": "Interval Session",
            "advanced": "VO₂max Intervals",
        },
        "long": {
            "foundational": "Long Endurance Swim",
            "intermediate": "Aerobic Durability Swim",
            "advanced": "Endurance Base Development",
        },
        "rest": {
            "foundational": "Rest Day",
            "intermediate": "Recovery Day",
            "advanced": "Adaptation Day",
        },
    },
    "strength": {
        "easy": {
            "foundational": "Light Strength",
            "intermediate": "Maintenance Strength",
            "advanced": "Recovery Strength",
        },
        "steady": {
            "foundational": "Moderate Strength",
            "intermediate": "Base Strength",
            "advanced": "Foundation Strength",
        },
        "tempo": {
            "foundational": "Strength Session",
            "intermediate": "Strength Workout",
            "advanced": "Strength Development",
        },
        "intervals": {
            "foundational": "Circuit Training",
            "intermediate": "Interval Strength",
            "advanced": "Power Development",
        },
        "long": {
            "foundational": "Extended Strength",
            "intermediate": "Durability Strength",
            "advanced": "Volume Strength",
        },
        "rest": {
            "foundational": "Rest Day",
            "intermediate": "Recovery Day",
            "advanced": "Adaptation Day",
        },
    },
    "race": {
        "easy": {
            "foundational": "Easy Recovery",
            "intermediate": "Recovery Run",
            "advanced": "Active Recovery",
        },
        "steady": {
            "foundational": "Race Preparation",
            "intermediate": "Race Taper",
            "advanced": "Race-Specific Preparation",
        },
        "tempo": {
            "foundational": "Race Pace Practice",
            "intermediate": "Race Pace Session",
            "advanced": "Race-Specific Tempo",
        },
        "intervals": {
            "foundational": "Race Intervals",
            "intermediate": "Race-Specific Intervals",
            "advanced": "Competition Intervals",
        },
        "long": {
            "foundational": "Race Simulation",
            "intermediate": "Race-Specific Endurance",
            "advanced": "Competition Preparation",
        },
        "rest": {
            "foundational": "Rest Day",
            "intermediate": "Recovery Day",
            "advanced": "Adaptation Day",
        },
    },
    "other": {
        "easy": {
            "foundational": "Easy Activity",
            "intermediate": "Maintenance Activity",
            "advanced": "Recovery Activity",
        },
        "steady": {
            "foundational": "Steady Activity",
            "intermediate": "Base Activity",
            "advanced": "Foundation Activity",
        },
        "tempo": {
            "foundational": "Moderate Activity",
            "intermediate": "Tempo Activity",
            "advanced": "Threshold Activity",
        },
        "intervals": {
            "foundational": "Interval Activity",
            "intermediate": "Interval Session",
            "advanced": "High-Intensity Activity",
        },
        "long": {
            "foundational": "Long Activity",
            "intermediate": "Extended Activity",
            "advanced": "Endurance Activity",
        },
        "rest": {
            "foundational": "Rest Day",
            "intermediate": "Recovery Day",
            "advanced": "Adaptation Day",
        },
    },
}


def normalize_calendar_sport(sport: str | None, title: str | None = None) -> CalendarSport:
    """Normalize backend sport type to calendar sport type.
    
    Args:
        sport: Backend sport string (e.g., 'run', 'running', 'Run')
        title: Optional title to check for race keywords
        
    Returns:
        Normalized calendar sport type
    """
    if not sport:
        return "other"
    
    lower = sport.lower()
    title_lower = (title or "").lower()
    
    # Check title first for race/event keywords
    if any(
        keyword in title_lower
        for keyword in ["race", "marathon", "5k", "10k", "half marathon", "ironman", "triathlon", "event"]
    ):
        return "race"
    
    if "race" in lower or "event" in lower:
        return "race"
    if "run" in lower or lower == "running":
        return "run"
    if "ride" in lower or "cycling" in lower or "bike" in lower:
        return "ride"
    if "swim" in lower:
        return "swim"
    if "strength" in lower or "weight" in lower or "gym" in lower:
        return "strength"
    
    return "other"


def normalize_calendar_intent(intent: str | None) -> CalendarIntent:
    """Normalize backend intent type to calendar intent type.
    
    Args:
        intent: Backend intent string (e.g., 'easy', 'recovery', 'aerobic')
        
    Returns:
        Normalized calendar intent type
    """
    if not intent:
        return "easy"
    
    lower = intent.lower()
    
    if "easy" in lower or "recovery" in lower or "aerobic" in lower:
        return "easy"
    if "steady" in lower or "endurance" in lower:
        return "steady"
    if "tempo" in lower or "threshold" in lower:
        return "tempo"
    if "interval" in lower or "vo2" in lower or "speed" in lower:
        return "intervals"
    if "long" in lower:
        return "long"
    if "rest" in lower or "off" in lower:
        return "rest"
    
    return "easy"


def resolve_workout_display_name(
    sport: str | None,
    intent: str | None,
    vocabulary_level: CoachVocabularyLevel | None = None,
    title: str | None = None,
) -> str:
    """Resolve canonical coach vocabulary for workout display names.
    
    This is the shared language layer that provides deterministic workout
    names based on sport, intent, and vocabulary level. Used by:
    
    - UI card titles (via calendarAdapter)
    - Weekly narrative text (future)
    - Modal narrative blocks (future)
    - LLM coach responses (as a consumer, not generator)
    
    The LLM coach should reference these names but never invent new ones.
    This ensures consistent coach voice across all touchpoints.
    
    Args:
        sport: Backend sport type (e.g., 'run', 'ride', 'swim')
        intent: Backend intent type (e.g., 'easy', 'tempo', 'long')
        vocabulary_level: Coach vocabulary level (defaults to 'intermediate')
        title: Optional title for sport normalization (checks for race keywords)
        
    Returns:
        Canonical workout display name
        
    Examples:
        >>> resolve_workout_display_name('run', 'easy', 'intermediate')
        'Aerobic Maintenance Run'
        
        >>> resolve_workout_display_name('run', 'tempo', 'advanced')
        'Lactate Threshold Tempo'
        
        >>> resolve_workout_display_name('run', 'easy')  # defaults to intermediate
        'Aerobic Maintenance Run'
    """
    level: CoachVocabularyLevel = vocabulary_level or "intermediate"
    
    normalized_sport = normalize_calendar_sport(sport, title)
    normalized_intent = normalize_calendar_intent(intent)
    
    sport_names = WORKOUT_DISPLAY_NAMES.get(normalized_sport)
    if not sport_names:
        return intent or "Workout"
    
    intent_names = sport_names.get(normalized_intent)
    if not intent_names:
        return intent or "Workout"
    
    display_name = intent_names.get(level)
    if display_name:
        return display_name
    
    # Fallback to intermediate if level not found
    fallback_name = intent_names.get("intermediate")
    if fallback_name:
        return fallback_name
    
    # Final fallback to intent string
    return intent or "Workout"
