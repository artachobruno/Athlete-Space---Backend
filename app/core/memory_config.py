"""Memory compaction configuration constants.

This module defines centralized constants for memory compaction behavior.
"""

# Number of turns (user + assistant pairs) to keep after summary
# A "turn" = one user message + one assistant message
# So SUMMARY_CONTEXT_TURNS=6 means approximately 12 messages max
SUMMARY_CONTEXT_TURNS = 6

# Role used for system summary messages injected into Redis
SUMMARY_SYSTEM_ROLE = "system"
