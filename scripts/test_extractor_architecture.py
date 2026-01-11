#!/usr/bin/env python3
"""Test script for orchestrator-guided, extractor-authoritative architecture.

This script tests the new architecture where:
- Orchestrator declares required_attributes and optional_attributes
- Extractor extracts values with confidence, evidence, missing_fields, ambiguous_fields
- Slot gate validates and determines execution

Usage:
    python scripts/test_extractor_architecture.py
"""

import asyncio
import sys
from pathlib import Path

# Add project root to path
project_root = Path(__file__).parent.parent
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))

from app.coach.agents.orchestrator_agent import run_conversation
from app.coach.agents.orchestrator_deps import CoachDeps


async def test_race_planning():
    """Test race planning flow with new architecture."""
    print("=" * 80)
    print("Testing Orchestrator-Guided, Extractor-Authoritative Architecture")
    print("=" * 80)
    print()

    # Create test dependencies (minimal - you'll need to adjust based on your setup)
    deps = CoachDeps(
        athlete_id=1,
        user_id="test_user",
        athlete_state=None,
        athlete_profile=None,
        training_preferences=None,
        race_profile=None,
        days=60,
        days_to_race=None,
    )

    # Test 1: Initial race planning query (should trigger extraction)
    print("Test 1: Initial race planning query")
    print("-" * 80)
    query1 = "I'm training for a marathon"
    print(f"User: {query1}")
    print()

    result1 = await run_conversation(
        user_input=query1,
        deps=deps,
        conversation_id="test_race_plan_001",
    )

    print(f"Intent: {result1.intent}")
    print(f"Horizon: {result1.horizon}")
    print(f"Target Action: {result1.target_action}")
    print(f"Required Attributes: {result1.required_attributes}")
    print(f"Optional Attributes: {result1.optional_attributes}")
    print(f"Filled Slots: {result1.filled_slots}")
    print(f"Missing Slots: {result1.missing_slots}")
    print(f"Should Execute: {result1.should_execute}")
    print(f"Message: {result1.message}")
    print()
    print()

    # Test 2: Follow-up with date (should extract and merge)
    print("Test 2: Follow-up with race date")
    print("-" * 80)
    query2 = "April 25th"
    print(f"User: {query2}")
    print()

    result2 = await run_conversation(
        user_input=query2,
        deps=deps,
        conversation_id="test_race_plan_001",  # Same conversation ID
    )

    print(f"Intent: {result2.intent}")
    print(f"Horizon: {result2.horizon}")
    print(f"Target Action: {result2.target_action}")
    print(f"Required Attributes: {result2.required_attributes}")
    print(f"Optional Attributes: {result2.optional_attributes}")
    print(f"Filled Slots: {result2.filled_slots}")
    print(f"Missing Slots: {result2.missing_slots}")
    print(f"Should Execute: {result2.should_execute}")
    print(f"Message: {result2.message}")
    print()
    print()

    # Test 3: Complete query (all attributes in one message)
    print("Test 3: Complete query (all attributes at once)")
    print("-" * 80)
    query3 = "Marathon on April 25, aiming for sub-3. Running ~55 mpw."
    print(f"User: {query3}")
    print()

    result3 = await run_conversation(
        user_input=query3,
        deps=deps,
        conversation_id="test_race_plan_002",  # New conversation
    )

    print(f"Intent: {result3.intent}")
    print(f"Horizon: {result3.horizon}")
    print(f"Target Action: {result3.target_action}")
    print(f"Required Attributes: {result3.required_attributes}")
    print(f"Optional Attributes: {result3.optional_attributes}")
    print(f"Filled Slots: {result3.filled_slots}")
    print(f"Missing Slots: {result3.missing_slots}")
    print(f"Should Execute: {result3.should_execute}")
    print(f"Message: {result3.message}")
    print()
    print()

    print("=" * 80)
    print("Testing Complete!")
    print("=" * 80)


if __name__ == "__main__":
    asyncio.run(test_race_planning())
