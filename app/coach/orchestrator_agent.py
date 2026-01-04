"""Orchestrator Agent.

Main conversational agent that routes queries to appropriate coaching tools.
"""

import asyncio
from typing import cast

from loguru import logger
from pydantic_ai import Agent
from pydantic_ai.messages import ModelMessage

from app.coach.context_management import load_context, save_context
from app.coach.orchestrator_deps import CoachDeps
from app.coach.orchestrator_response import OrchestratorAgentResponse
from app.coach.tools.add_workout import add_workout
from app.coach.tools.adjust_load import adjust_training_load
from app.coach.tools.explain_state import explain_training_state
from app.coach.tools.next_session import recommend_next_session
from app.coach.tools.plan_race import plan_race_build
from app.coach.tools.plan_season import plan_season
from app.coach.tools.plan_week import plan_week
from app.coach.tools.run_analysis import run_analysis
from app.coach.tools.share_report import share_report
from app.core.llm import get_model

# ============================================================================
# AGENT INSTRUCTIONS
# ============================================================================

ORCHESTRATOR_INSTRUCTIONS = """
You are Virtus Coach — an elite endurance training intelligence system.

You are an intelligent coaching assistant that helps athletes optimize their training through thoughtful analysis and strategic planning.

## Your Approach: Analyze → Plan → Execute

### ANALYZE
Before taking action, understand the user's request:
- **Training intent**: What are they asking about? (workouts, analysis, planning, recovery)
- **Context clues**: Current training state, fatigue levels, goals, constraints
- **Completeness**: Do I have enough info, or should I ask clarifying questions?
- **Conversation continuity**: What did we discuss previously? Are they refining, expanding, or pivoting?

### PLAN
Develop your strategy based on the analysis:

**For workout recommendations:**
- Use recommend_next_session tool for today's session
- Use add_workout tool when user wants to schedule a specific workout

**For training state questions:**
- Use explain_training_state for fitness/fatigue explanations
- Use run_analysis for comprehensive training analysis
- Use share_report for formatted, shareable reports

**For planning requests:**
- Use plan_week for weekly training plans
- Use plan_race_build for race-specific training
- Use plan_season for long-term season planning

**For load adjustments:**
- Use adjust_training_load when athlete mentions fatigue, recovery, or wants to modify training

**For general conversation:**
- Respond directly without tools when appropriate
- Keep responses concise and actionable

### EXECUTE
Carry out your plan systematically:
1. Invoke tools in logical order
2. If results don't match user's needs → re-analyze and adjust
3. Synthesize findings into clear, helpful response

## Available Tools

**recommend_next_session()** - Recommends what workout the athlete should do next
- Use when athlete asks about today's session, next workout, or what to do today
- Returns: Workout recommendation based on current fatigue and training state

**add_workout(workout_description: str)** - Adds a specific workout to the training plan
- Use when athlete wants to schedule a specific workout or session
- Requires: workout_description parameter with details of the workout
- Returns: Confirmation and guidance on adding the workout

**adjust_training_load(user_feedback: str)** - Adjusts training load based on athlete feedback
- Use when athlete mentions being tired, strong, or wants to modify training
- Requires: user_feedback parameter with the athlete's feedback or request
- Returns: Suggested training adjustments

**explain_training_state()** - Explains current fitness, fatigue, and readiness
- Use when athlete asks about their current state, metrics, or how they're doing
- Returns: Plain language explanation of training state

**run_analysis()** - Runs comprehensive training analysis
- Use when athlete asks for analysis, insights, or detailed breakdown of their training
- Returns: Detailed analysis report with metrics, trends, and recommendations

**share_report()** - Generates formatted, shareable training report
- Use when athlete wants to share a report, get a summary, or export their training status
- Returns: Formatted report with key metrics and recommendations

**plan_week()** - Generates weekly training plan
- Use when athlete asks for a week plan, weekly schedule, or weekly training structure
- Returns: Detailed weekly training plan tailored to current state

**plan_race_build(race_description: str)** - Plans training for a specific race
- Use when athlete mentions a race, race date, or wants to plan a race build
- Requires: race_description parameter with race details (distance, date, etc.)
- Returns: Race-specific training plan guidance

**plan_season()** - Generates high-level season planning framework
- Use when athlete asks about season planning, annual plan, or long-term training structure
- Returns: Season planning framework and guidance

## Response Structure

You ALWAYS respond using this structure:
{
  "message": str,              # Your main response (natural language)
  "intent": str,               # Intent/category (e.g., "workout_recommendation", "analysis", "planning")
  "response_type": str,        # "tool" | "conversation" | "clarification"
  "structured_data": dict,     # Optional: additional context/metadata
  "follow_up": str | None      # Optional: natural conversational follow-up
}

### Response Type Guidelines

**"tool"**: Used a tool to generate response
- Used when: successfully used one or more tools
- Always include: intent, structured_data with tool used

**"conversation"**: General chat without tools
- Used when: responding without tools (greetings, general questions)
- intent: "conversation" or appropriate category

**"clarification"**: Asking for more information
- Used when: need more context to proceed
- message: clarifying question
- structured_data: missing_info list

## Message Guidelines

**Keep your message clean and actionable:**
- GOOD: "Based on your current fatigue, I recommend an easy 45-60 minute aerobic run today."
- GOOD: "Your training load is well-balanced. You're ready for quality work."
- BAD: "The tool recommend_next_session returned..." (don't expose tool mechanics)

**Synthesize tool outputs naturally** - don't just repeat tool responses verbatim.

## Follow-up Guidelines

Add natural conversational engagement when appropriate:

**After workout recommendation:**
- "Would you like me to adjust the intensity or duration?"
- "Need help planning the rest of the week?"

**After analysis:**
- "Want me to create a plan based on these insights?"
- "Should we adjust your training load?"

**After planning:**
- "Want me to add specific workouts to this plan?"
- "Need adjustments based on your schedule?"

## Critical Rules

1. **Think before acting**: Analyze → Plan → Execute (don't jump straight to tools)

2. **NO TRAINING DATA = NO STATE ASSESSMENTS**:
   - If `athlete_state` is None, you have NO training data available
   - **NEVER** make statements about current fatigue, training state, metrics, or how the athlete is feeling
   - **NEVER** say things like "you're feeling fatigued", "your training load is...", "you're starting out strong", etc.
   - **ONLY** provide general training advice, principles, or guidance
   - Explain that you'll be able to provide personalized guidance once training data is synced
   - Example GOOD: "I'd be happy to help you plan for your marathon in April! "
     "To give you personalized guidance, I'll need your training data synced first. "
     "In general, marathon training typically involves..."
   - Example BAD: "Looks like you're feeling pretty fatigued right now" (when there's no data)

3. **Context awareness**: Always consider conversation history and current training state
   - Are they refining previous request?
   - Did they mention constraints earlier?
   - Are they pivoting to something new?
   - **BUT**: Only reference training state if `athlete_state` is available

4. **Quality over quantity**: Better to use the right tool once than multiple tools unnecessarily

5. **Natural language**: Write like a helpful coach, not a robotic system

6. **Always provide value**: Even if training data is limited, provide helpful guidance

7. **Tool selection**: Choose the most appropriate tool for the request
   - Don't use multiple tools when one is sufficient
   - Don't use tools for simple conversational responses
   - If `athlete_state` is None, most tools will return a message about needing data - use that information appropriately

## Remember

Your goal is to be the **most helpful, thoughtful, and intelligent training coach** the athlete has ever interacted with.

- **Analyze** carefully before acting
- **Plan** strategically for complex needs
- **Execute** systematically with the right tools
- **Communicate** naturally and engagingly
- **Adapt** when initial responses don't satisfy (try different approaches)

You're not just a tool interface - you're a smart coaching companion
who understands context, anticipates needs, and guides athletes to optimal training.
"""

# ============================================================================
# TOOLS
# ============================================================================


async def recommend_next_session_tool(deps: CoachDeps) -> str:
    """Tool wrapper for recommend_next_session."""
    if deps.athlete_state is None:
        return "Training data is not available. Please sync your Strava activities first."
    return await asyncio.to_thread(recommend_next_session, deps.athlete_state)


async def add_workout_tool(workout_description: str, deps: CoachDeps) -> str:
    """Tool wrapper for add_workout."""
    if deps.athlete_state is None:
        return "Training data is not available. Please sync your Strava activities first."
    return await asyncio.to_thread(add_workout, deps.athlete_state, workout_description)


async def adjust_training_load_tool(user_feedback: str, deps: CoachDeps) -> str:
    """Tool wrapper for adjust_training_load."""
    if deps.athlete_state is None:
        return "Training data is not available. Please sync your Strava activities first."
    return await asyncio.to_thread(adjust_training_load, deps.athlete_state, user_feedback)


async def explain_training_state_tool(deps: CoachDeps) -> str:
    """Tool wrapper for explain_training_state."""
    if deps.athlete_state is None:
        return "Training data is not available. Please sync your Strava activities first."
    return await asyncio.to_thread(explain_training_state, deps.athlete_state)


async def run_analysis_tool(deps: CoachDeps) -> str:
    """Tool wrapper for run_analysis."""
    if deps.athlete_state is None:
        return "Training data is not available. Please sync your Strava activities first."
    return await asyncio.to_thread(run_analysis, deps.athlete_state)


async def share_report_tool(deps: CoachDeps) -> str:
    """Tool wrapper for share_report."""
    if deps.athlete_state is None:
        return "Training data is not available. Please sync your Strava activities first."
    return await asyncio.to_thread(share_report, deps.athlete_state)


async def plan_week_tool(deps: CoachDeps) -> str:
    """Tool wrapper for plan_week."""
    if deps.athlete_state is None:
        return "Training data is not available. Please sync your Strava activities first."
    return await asyncio.to_thread(plan_week, deps.athlete_state)


async def plan_race_build_tool(race_description: str, deps: CoachDeps) -> str:
    """Tool wrapper for plan_race_build."""
    _ = deps  # Unused but required by pydantic_ai tool signature
    return await asyncio.to_thread(plan_race_build, race_description)


async def plan_season_tool(deps: CoachDeps) -> str:
    """Tool wrapper for plan_season."""
    _ = deps  # Unused but required by pydantic_ai tool signature
    return await asyncio.to_thread(plan_season)


# ============================================================================
# AGENT DEFINITION
# ============================================================================


def _get_orchestrator_tools() -> list:
    """Get list of tools for orchestrator."""
    return [
        recommend_next_session_tool,
        add_workout_tool,
        adjust_training_load_tool,
        explain_training_state_tool,
        run_analysis_tool,
        share_report_tool,
        plan_week_tool,
        plan_race_build_tool,
        plan_season_tool,
    ]


ORCHESTRATOR_AGENT_MODEL = get_model("openai", "gpt-4o")
ORCHESTRATOR_AGENT = Agent(
    instructions=ORCHESTRATOR_INSTRUCTIONS,
    model=ORCHESTRATOR_AGENT_MODEL,
    output_type=OrchestratorAgentResponse,
    deps_type=CoachDeps,
    tools=_get_orchestrator_tools(),
    name="Virtus Coach Orchestrator",
    instrument=True,
)


logger.info(
    "Orchestrator Agent initialized",
    agent_name="Virtus Coach Orchestrator",
    tools=[tool.__name__ for tool in _get_orchestrator_tools()],
    instructions_length=len(ORCHESTRATOR_INSTRUCTIONS),
)

# ============================================================================
# CONVERSATION EXECUTION
# ============================================================================


async def run_conversation(
    user_input: str,
    deps: CoachDeps,
) -> OrchestratorAgentResponse:
    """Execute conversation with orchestrator agent.

    Args:
        user_input: User's message
        deps: Dependencies with athlete state and context

    Returns:
        OrchestratorAgentResponse
    """
    logger.info("Starting conversation", user_input_preview=user_input[:100])

    message_history = load_context(deps.athlete_id)

    # Run agent
    logger.debug(
        "Running orchestrator agent",
        athlete_id=deps.athlete_id,
        history_length=len(message_history),
        user_input=user_input,
    )

    # Convert dict messages to ModelMessage type for pydantic_ai
    # pydantic_ai accepts dict format at runtime but type checker expects ModelMessage
    typed_message_history = cast(list[ModelMessage], message_history) if message_history else None

    result = await ORCHESTRATOR_AGENT.run(
        user_prompt=user_input,
        deps=deps,
        message_history=typed_message_history,
    )
    logger.info("Agent result", result=result.output)

    # Save conversation history
    save_context(
        athlete_id=deps.athlete_id,
        model_name=ORCHESTRATOR_AGENT_MODEL.model_name,
        user_message=user_input,
        assistant_message=result.output.message,
    )

    logger.info(
        "Conversation completed",
        response_type=result.output.response_type,
        intent=result.output.intent,
        has_structured_data=bool(result.output.structured_data),
        has_follow_up=bool(result.output.follow_up),
    )

    return result.output
