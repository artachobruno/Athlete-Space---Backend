"""Summarization trigger configuration (B33).

This module defines explicit thresholds for triggering conversation summarization.
All thresholds are objective constants with no heuristics.

Summarization is triggered when:
1. Conversation exceeds a hard size threshold (token or message count)
2. A summary does not already cover the older history
3. Minimum messages have elapsed since last summary (prevents spam)
"""

# Maximum history tokens before summarization is triggered
# This protects model safety by preventing prompts from exceeding token limits
MAX_HISTORY_TOKENS_BEFORE_SUMMARY = 120_000

# Maximum history message count before summarization is triggered
# This protects latency + determinism by limiting message count
MAX_HISTORY_MESSAGES_BEFORE_SUMMARY = 30

# Minimum messages that must have elapsed since last summary
# This prevents summary spam by ensuring summaries are only triggered when enough
# new messages have accumulated
MIN_MESSAGES_SINCE_LAST_SUMMARY = 10
