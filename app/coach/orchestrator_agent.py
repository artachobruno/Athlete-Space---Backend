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
- Used when: need more context to proceed OR when training data is insufficient
- message: Natural, conversational clarifying question(s)
- structured_data: missing_info list (what information you're seeking)
- Examples of good clarifying questions:
  * "How are you feeling today? Are you tired, fresh, or somewhere in between?"
  * "What's your training goal right now? Building base, race prep, or recovery?"
  * "How many days per week can you commit to training?"
  * "What's your current fitness level - just starting, maintaining, or in peak shape?"

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

2. **NO TRAINING DATA = ASK CLARIFYING QUESTIONS**:
   - If `athlete_state` is None, you have NO training data available
   - If `athlete_state.confidence` is very low (< 0.1), you have INSUFFICIENT data
   - **NEVER** make statements about current fatigue, training state, metrics, or how the athlete is feeling when:
     * `athlete_state` is None, OR
     * `athlete_state.confidence` is very low (< 0.1)
   - **NEVER** assume fatigue, tiredness, or recovery state without sufficient data
   - **NEVER** say things like "you're feeling fatigued", "your training load is...",
     "you're starting out strong", etc. when data is insufficient
   - **ASK CLARIFYING QUESTIONS** when you need more information to provide a good recommendation:
     * "How are you feeling today? Are you tired, fresh, or somewhere in between?"
     * "What's your training goal? Are you building base fitness, preparing for a race, or recovering?"
     * "How many days per week can you train? What's your typical schedule?"
     * "What's your current fitness level? Are you just starting, maintaining, or in peak shape?"
   - Use "clarification" response_type when asking questions
   - After gathering information, provide helpful general advice based on their answers
   - Example GOOD: "I'd love to help you plan for your marathon in April! To give you the best guidance, can you tell me: "
     "How many days per week can you train? And what's your current fitness level - are you just starting "
     "or do you have a solid base already?"
   - Example BAD: "Looks like you're feeling pretty fatigued right now" (when there's no data)
   - **CRITICAL**: Always check `athlete_state.confidence` before using tools
     that assess fatigue or training state

3. **Context awareness**: Always consider conversation history and current training state
   - Are they refining previous request?
   - Did they mention constraints earlier?
   - Are they pivoting to something new?
   - **BUT**: Only reference training state if `athlete_state` is available

4. **Quality over quantity**: Better to use the right tool once than multiple tools unnecessarily
   - **CRITICAL**: NEVER call the same tool repeatedly with the same or similar input
   - If a tool returns a response, use that response - don't call it again expecting different results
   - If a tool response isn't what you need, synthesize what you have and respond to the user instead of calling the tool again
   - Each tool call should provide new information - if you're not getting new information, stop calling tools

5. **Natural language**: Write like a helpful coach, not a robotic system

6. **Always provide value**: Even if training data is limited, provide helpful guidance

7. **Tool selection**: Choose the most appropriate tool for the request
   - Don't use multiple tools when one is sufficient
   - Don't use tools for simple conversational responses
   - If `athlete_state` is None, most tools will return clarifying questions - use that information appropriately
   - **NEVER call the same tool more than once per conversation turn** - if you need more info, ask the user instead

8. **Ask clarifying questions proactively**:
   - When you need more context to provide a good recommendation, ask questions
   - Use "clarification" response_type when asking questions
   - Ask specific, actionable questions that help you provide better guidance
   - Examples:
     * For workout recommendations: "How are you feeling today? What did you do yesterday?"
     * For training plans: "What's your goal? How many days per week can you train?"
     * For load adjustments: "How are you feeling? What's your current volume?"
   - After getting answers, provide helpful recommendations based on their responses

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
        return (
            "I'd love to recommend today's session! To give you the best guidance, could you tell me:\n\n"
            "• How are you feeling today? (tired, fresh, or somewhere in between?)\n"
            "• What did you do yesterday? (rest day, easy run, or harder workout?)\n"
            "• What's your goal for today? (easy recovery, moderate effort, or quality session?)\n\n"
            "Once I know this, I can recommend the perfect session for you. "
            "Also, syncing your Strava activities will help me provide even more personalized recommendations!"
        )
    return await asyncio.to_thread(recommend_next_session, deps.athlete_state)


async def add_workout_tool(workout_description: str, deps: CoachDeps) -> str:
    """Tool wrapper for add_workout."""
    if deps.athlete_state is None:
        return (
            "I'd love to help you add a workout! To give you the best guidance, could you tell me:\n\n"
            "• How are you feeling today? (tired, fresh, or somewhere in between?)\n"
            "• What type of workout are you thinking about? (easy run, tempo, intervals, etc.?)\n"
            "• What's your goal for this session? (recovery, building fitness, or race prep?)\n\n"
            "Based on your answers, I can help you plan the perfect workout. "
            "Syncing your Strava activities will help me provide even more personalized recommendations!"
        )
    return await asyncio.to_thread(add_workout, deps.athlete_state, workout_description)


async def adjust_training_load_tool(user_feedback: str, deps: CoachDeps) -> str:
    """Tool wrapper for adjust_training_load."""
    if deps.athlete_state is None:
        return (
            "I'd like to help adjust your training load! To give you the best recommendations, could you tell me:\n\n"
            "• How are you feeling? (tired, strong, or somewhere in between?)\n"
            "• What's your current training volume? (hours per week or sessions per week?)\n"
            "• What's your goal? (building fitness, maintaining, or recovering?)\n"
            "• Any specific concerns? (overtraining, undertraining, or just fine-tuning?)\n\n"
            "Based on your answers, I can suggest specific adjustments. "
            "Syncing your Strava activities will help me provide even more precise recommendations!"
        )
    return await asyncio.to_thread(adjust_training_load, deps.athlete_state, user_feedback)


async def explain_training_state_tool(deps: CoachDeps) -> str:
    """Tool wrapper for explain_training_state."""
    if deps.athlete_state is None:
        return (
            "I'd love to explain your training state! To give you accurate insights, could you tell me:\n\n"
            "• How consistent has your training been? (daily, a few times per week, or irregular?)\n"
            "• What's your typical training volume? (hours per week?)\n"
            "• How are you feeling? (energetic, tired, or somewhere in between?)\n"
            "• What's your training goal right now? (building base, race prep, or maintaining?)\n\n"
            "Based on your answers, I can explain your current state and provide guidance. "
            "Syncing your Strava activities will help me provide even more detailed analysis!"
        )
    return await asyncio.to_thread(explain_training_state, deps.athlete_state)


async def run_analysis_tool(deps: CoachDeps) -> str:
    """Tool wrapper for run_analysis."""
    if deps.athlete_state is None:
        return (
            "I'd love to analyze your training! To provide comprehensive insights, could you tell me:\n\n"
            "• How consistent has your training been? (daily, a few times per week, or irregular?)\n"
            "• What's your typical training volume? (hours per week?)\n"
            "• How are you feeling overall? (energetic, tired, or somewhere in between?)\n"
            "• What's your training goal? (building base, race prep, or maintaining?)\n\n"
            "Based on your answers, I can provide detailed analysis. "
            "Syncing your Strava activities will help me provide even more comprehensive insights!"
        )
    return await asyncio.to_thread(run_analysis, deps.athlete_state)


async def share_report_tool(deps: CoachDeps) -> str:
    """Tool wrapper for share_report."""
    if deps.athlete_state is None:
        return (
            "I'd love to create a training report for you! To provide accurate insights, could you tell me:\n\n"
            "• How consistent has your training been? (daily, a few times per week, or irregular?)\n"
            "• What's your typical training volume? (hours per week?)\n"
            "• How are you feeling overall? (energetic, tired, or somewhere in between?)\n"
            "• What's your training goal? (building base, race prep, or maintaining?)\n\n"
            "Based on your answers, I can create a helpful report. "
            "Syncing your Strava activities will help me provide even more detailed reports!"
        )
    return await asyncio.to_thread(share_report, deps.athlete_state)


async def plan_week_tool(deps: CoachDeps) -> str:
    """Tool wrapper for plan_week."""
    if deps.athlete_state is None:
        return (
            "I'd love to help you plan your week! To create the best plan, could you tell me:\n\n"
            "• What's your training goal this week? (building base, race prep, or maintaining?)\n"
            "• How many days can you train? (which days of the week?)\n"
            "• What's your current fitness level? (just starting, maintaining, or in peak shape?)\n"
            "• Any constraints? (time limits, injury concerns, or other commitments?)\n\n"
            "Based on your answers, I can create a personalized weekly plan. "
            "Syncing your Strava activities will help me provide even more tailored recommendations!"
        )
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

    result = await ORCHESTRATOR_AGENT.run(
        user_prompt=user_input,
        deps=deps,
        message_history=typed_message_history,
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
