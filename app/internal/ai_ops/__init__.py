"""
AI Ops Metrics (READ-ONLY)

Rules:
- NO LLM calls
- NO MCP calls
- NO DB writes
- NO retries
- NO inference
- NO blocking behavior

This module observes the system.
It must never influence system behavior.
"""
