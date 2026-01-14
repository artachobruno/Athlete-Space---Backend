"""Orchestration layer for manual planned session workflow.

This module provides the orchestration service that follows the spec flow:
1. Persist PlannedSession (raw, uninterpreted)
2. Extract attributes (deterministic)
3. LLM → Structured Workout
4. Create Workout with structured steps
"""
