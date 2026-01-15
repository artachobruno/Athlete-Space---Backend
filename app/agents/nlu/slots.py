"""Slot definitions for NLU intents."""

# Schema definition for MODIFY intent slots
# Values indicate the expected type for each slot (all are optional)
MODIFY_SLOTS = {
    "target_date": str | None,  # day-level
    "change_type": str | None,  # reduce / replace / move / delete / add
    "delta": str | None,  # "20%", "shorter", "easier"
    "reason": str | None,  # injury, travel, fatigue
}
