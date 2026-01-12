"""Constants aligned with RAG structure keys.

This module defines constants that must match RAG document keys
to ensure proper structure resolution.
"""

MAX_WEEKS = 52
MIN_WEEKS = 1

SUPPORTED_INTENTS = {
    "maintain",
    "build",
    "explore",
    "recover",
}

SUPPORTED_RACE_DISTANCES = {
    "5k",
    "10k",
    "10_mile",
    "half_marathon",
    "marathon",
    "50k",
    "50m",
    "100k",
    "100m",
}
