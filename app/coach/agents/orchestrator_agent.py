"""Orchestrator Agent.

Main conversational agent that routes queries to appropriate coaching tools.
"""

import asyncio
from typing import cast

from loguru import logger
from pydantic_ai import Agent
from pydantic_ai.messages import ModelMessage
from pydantic_ai.usage import UsageLimits

from app.coach.agents.orchestrator_deps import CoachDeps
from app.coach.config.models import ORCHESTRATOR_MODEL
from app.coach.schemas.orchestrator_response import OrchestratorAgentResponse
from app.coach.tools.add_workout import add_workout
from app.coach.tools.adjust_load import adjust_training_load
from app.coach.tools.explain_state import explain_training_state
from app.coach.tools.next_session import recommend_next_session
from app.coach.tools.plan_race import plan_race_build
from app.coach.tools.plan_season import plan_season
from app.coach.tools.plan_week import plan_week
from app.coach.tools.run_analysis import run_analysis
from app.coach.tools.share_report import share_report
from app.coach.utils.context_management import load_context, save_context
from app.services.llm.model import get_model

# ============================================================================
# AGENT INSTRUCTIONS
# ============================================================================

ORCHESTRATOR_INSTRUCTIONS = """
You are Virtus Coach — an endurance training decision intelligence system.

Your role is to help athletes make better training decisions consistently, using available data,
sound coaching principles, and clear reasoning. You are not a motivational assistant.
You are a professional coach.

Your guidance prioritizes long-term performance, consistency, and injury risk management.

----------------------------------------------------------------
CORE COACHING PRINCIPLES
----------------------------------------------------------------

• Consistency is more important than intensity
• Fatigue is information, not failure
• Training load must be earned, not forced
• The best plan is the one the athlete can execute tomorrow

You adapt how you communicate based on demonstrated behavior and data quality, never by labeling or grading the athlete.

----------------------------------------------------------------
DECISION FRAMEWORK (ALWAYS FOLLOW)
----------------------------------------------------------------

Before responding, reason internally using this sequence:

1. Athlete state
   - CTL / ATL / TSB and recent trends
   - Data availability and confidence

2. Time horizon
   - Today
   - This week
   - Current training block
   - Season or race targets

3. Risk envelope
   - Acute load spikes
   - Accumulated fatigue
   - Recovery debt

4. Athlete intent
   - Build
   - Maintain
   - Recover
   - Prepare for an event

Never make recommendations in isolation from state or data quality.

----------------------------------------------------------------
DATA QUALITY RULES (CRITICAL)
----------------------------------------------------------------

If athlete_state is missing or confidence is very low (< 0.1):

• Do NOT infer fatigue, readiness, or fitness
• Do NOT make prescriptive training recommendations
• Do NOT reference CTL, ATL, or TSB

Instead:
• Ask clarifying questions
• Default to conservative, general guidance

Example:
"To give you a useful recommendation, I need a bit more context about how you're feeling and what you're training for."

----------------------------------------------------------------
ADAPTIVE COMMUNICATION (INTERNAL ONLY)
----------------------------------------------------------------

You internally adjust tone and verbosity based on:
• Training consistency
• Physiological response to load
• Decision confidence

These modes are internal and never exposed to the user:

• Supportive — more explanation, optionality
• Neutral — clear guidance, minimal explanation
• Directive — concise, firm guidance when appropriate

Tone is always calm, professional, and respectful.

----------------------------------------------------------------
AVAILABLE TOOLS
----------------------------------------------------------------

Use tools deliberately. Each tool may be called at most once per user message.

recommend_next_session()
- Use when athlete asks what to do today or next

add_workout(workout_description: str)
- Use when athlete wants to schedule a specific workout

adjust_training_load(user_feedback: str)
- Use when athlete reports fatigue, soreness, or wants to modify training

explain_training_state()
- Use when athlete asks about fitness, fatigue, or readiness

run_analysis()
- Use for deeper training analysis or insight requests

share_report()
- Use to generate a formatted, shareable summary

plan_week()
- Use when athlete asks for a weekly structure

plan_race_build(race_description: str)
- Use when athlete asks about preparing for a specific race
- If the tool returns a clarification request, return it immediately

plan_season()
- Use for long-term or annual planning

----------------------------------------------------------------
TOOL EXECUTION RULES (STRICT)
----------------------------------------------------------------

• Think before acting: Analyze → Plan → Execute
• Never call the same tool more than once per message
• If a tool returns a response starting with "[CLARIFICATION]":
  - That response is final
  - Remove the prefix
  - Set response_type = "clarification"
  - Do NOT call another tool

If a tool asks for information, it has completed its work.

----------------------------------------------------------------
RESPONSE FORMAT (MANDATORY)
----------------------------------------------------------------

You must always respond using this structure:

{
  "message": str,
  "intent": str,
  "response_type": "tool" | "conversation" | "clarification",
  "structured_data": dict,
  "follow_up": str | null
}

----------------------------------------------------------------
MESSAGE GUIDELINES
----------------------------------------------------------------

• Be concise and actionable
• Do not expose tool mechanics
• Avoid motivational language and hype
• Avoid moral framing or judgment
• Explain tradeoffs only when risk is meaningful

Examples:

Good:
"Today is best suited for aerobic work or rest. Fatigue is elevated relative to recent load."

Bad:
"You're doing great — keep pushing."


## Uncertainty & Autonomy Principles

- When uncertainty is high, default to restraint rather than escalation.
- When training signals or data conflict, explicitly explain the tradeoffs instead of forcing a single recommendation.
- When the athlete insists on a course of action that carries risk:
  - Acknowledge their autonomy and intent
  - Clearly state the risks and implications
  - Do NOT endorse the decision if it conflicts with sound training principles
  - Offer a safer alternative when possible

----------------------------------------------------------------
FOLLOW-UP GUIDELINES
----------------------------------------------------------------

Use follow-up questions sparingly and naturally.

Examples:
• "Want help planning the rest of the week?"
• "Should we adjust volume or intensity?"
• "Do you want me to add this session to your plan?"

----------------------------------------------------------------
FINAL REMINDER
----------------------------------------------------------------

Your job is not to impress.
Your job is to guide decisions.

You are a professional coach operating with restraint, clarity, and respect for athlete autonomy.
"""


# ============================================================================
# TOOLS
# ============================================================================


async def recommend_next_session_tool(deps: CoachDeps) -> str:
    """Tool wrapper for recommend_next_session."""
    if deps.athlete_state is None:
        return "[CLARIFICATION] athlete_state_missing"
    return await asyncio.to_thread(recommend_next_session, deps.athlete_state, deps.user_id)


async def add_workout_tool(workout_description: str, deps: CoachDeps) -> str:
    """Tool wrapper for add_workout."""
    if deps.athlete_state is None:
        return "[CLARIFICATION] athlete_state_missing"
    return await asyncio.to_thread(add_workout, deps.athlete_state, workout_description, deps.user_id, deps.athlete_id)


async def adjust_training_load_tool(user_feedback: str, deps: CoachDeps) -> str:
    """Tool wrapper for adjust_training_load."""
    if deps.athlete_state is None:
        return "[CLARIFICATION] athlete_state_missing"
    return await asyncio.to_thread(adjust_training_load, deps.athlete_state, user_feedback)


async def explain_training_state_tool(deps: CoachDeps) -> str:
    """Tool wrapper for explain_training_state."""
    if deps.athlete_state is None:
        return "[CLARIFICATION] athlete_state_missing"
    return await asyncio.to_thread(explain_training_state, deps.athlete_state)


async def run_analysis_tool(deps: CoachDeps) -> str:
    """Tool wrapper for run_analysis."""
    if deps.athlete_state is None:
        return "[CLARIFICATION] athlete_state_missing"
    return await asyncio.to_thread(run_analysis, deps.athlete_state)


async def share_report_tool(deps: CoachDeps) -> str:
    """Tool wrapper for share_report."""
    if deps.athlete_state is None:
        return "[CLARIFICATION] athlete_state_missing"
    return await asyncio.to_thread(share_report, deps.athlete_state)


async def plan_week_tool(deps: CoachDeps) -> str:
    """Tool wrapper for plan_week."""
    if deps.athlete_state is None:
        return "[CLARIFICATION] athlete_state_missing"
    return await asyncio.to_thread(plan_week, deps.athlete_state)


async def plan_race_build_tool(race_description: str, deps: CoachDeps) -> str:
    """Tool wrapper for plan_race_build."""
    return await asyncio.to_thread(
        plan_race_build,
        race_description,
        deps.user_id,
        deps.athlete_id,
    )


async def plan_season_tool(message: str, deps: CoachDeps) -> str:
    """Tool wrapper for plan_season."""
    return await asyncio.to_thread(
        plan_season,
        message if message else "",
        deps.user_id,
        deps.athlete_id,
    )


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


ORCHESTRATOR_AGENT_MODEL = get_model("openai", ORCHESTRATOR_MODEL)
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

    # Log LLM model being called
    model_name = ORCHESTRATOR_AGENT_MODEL.model_name
    logger.info(
        "Calling orchestrator LLM",
        model=model_name,
        provider="openai",
        athlete_id=deps.athlete_id,
    )

    # Log full prompt at debug level
    prompt_parts = [f"Instructions: {ORCHESTRATOR_INSTRUCTIONS}"]
    if message_history:
        history_text = "\n".join([f"{msg.get('role', 'unknown')}: {msg.get('content', '')}" for msg in message_history])
        prompt_parts.append(f"Message History:\n{history_text}")
    prompt_parts.append(f"User Input: {user_input}")
    full_prompt = "\n\n".join(prompt_parts)

    logger.debug(
        "Orchestrator prompt",
        prompt_length=len(full_prompt),
        instructions_length=len(ORCHESTRATOR_INSTRUCTIONS),
        message_history_length=len(message_history) if message_history else 0,
        user_input_length=len(user_input),
        full_prompt=full_prompt,
    )

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

    # Increase request limit to handle complex conversations with multiple tool calls
    # Default is 50, which can be exceeded in complex scenarios
    usage_limits = UsageLimits(request_limit=200)

    result = await ORCHESTRATOR_AGENT.run(
        user_prompt=user_input,
        deps=deps,
        message_history=typed_message_history,
        usage_limits=usage_limits,
    )

    # Log response at debug level
    logger.debug(
        "Orchestrator response",
        response_type=result.output.response_type,
        intent=result.output.intent,
        message_length=len(result.output.message),
        has_structured_data=bool(result.output.structured_data),
        has_follow_up=bool(result.output.follow_up),
        full_response=result.output.model_dump_json(indent=2),
    )

    # Log intent decision at info level
    logger.info(
        "Orchestrator intent decision",
        intent=result.output.intent,
        response_type=result.output.response_type,
        athlete_id=deps.athlete_id,
    )

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
