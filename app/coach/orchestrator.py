"""LangChain agent orchestrator for Virtus Coach.

This orchestrator uses LangChain's agent framework to intelligently
route requests to the appropriate coaching tools based on user intent.
"""

from loguru import logger
from pydantic import SecretStr

from app.coach.models import AthleteState
from app.coach.tools.add_workout import add_workout
from app.coach.tools.adjust_load import adjust_training_load
from app.coach.tools.explain_state import explain_training_state
from app.coach.tools.next_session import recommend_next_session
from app.coach.tools.plan_race import plan_race_build
from app.coach.tools.plan_season import plan_season
from app.coach.tools.plan_week import plan_week
from app.coach.tools.run_analysis import run_analysis
from app.coach.tools.share_report import share_report
from app.core.settings import settings

try:
    from langchain.agents import AgentExecutor, create_openai_tools_agent  # type: ignore[reportAttributeAccessIssue]
    from langchain_core.messages import HumanMessage
    from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder
    from langchain_core.tools import StructuredTool
    from langchain_openai import ChatOpenAI

    LANGCHAIN_AVAILABLE = True
except ImportError:
    LANGCHAIN_AVAILABLE = False
    StructuredTool = None
    ChatOpenAI = None
    AgentExecutor = None
    create_openai_tools_agent = None
    ChatPromptTemplate = None
    MessagesPlaceholder = None
    HumanMessage = None
    logger.warning("LangChain agent features not available - install langchain>=0.1.16")


if not LANGCHAIN_AVAILABLE:
    _orchestrator_agent = None
    _llm = None
else:
    # Initialize LLM
    if not settings.openai_api_key:
        _llm = None
        logger.warning("OPENAI_API_KEY not set. Orchestrator will not work.")
    elif ChatOpenAI is None:
        _llm = None
        logger.warning("ChatOpenAI not available. Orchestrator will not work.")
    else:
        _llm = ChatOpenAI(
            model="gpt-4o-mini",
            temperature=0.3,
            api_key=SecretStr(settings.openai_api_key),
        )

    # Define coaching tools as LangChain tools
    def _make_tool(func, name: str, description: str):
        """Helper to create LangChain tool from function."""
        if StructuredTool is None:
            raise RuntimeError("StructuredTool not available")
        return StructuredTool.from_function(
            func=func,
            name=name,
            description=description,
        )

    # Store athlete state in a way tools can access it
    _current_athlete_state: AthleteState | None = None

    def _recommend_next_session_wrapper() -> str:
        """Wrapper to get athlete state from context."""
        if _current_athlete_state is None:
            return "Error: Athlete state not available"
        return recommend_next_session(_current_athlete_state)

    def _adjust_load_wrapper(message: str) -> str:
        """Wrapper for adjust_load that includes athlete state."""
        if _current_athlete_state is None:
            return "Error: Athlete state not available"
        return adjust_training_load(_current_athlete_state, message)

    def _explain_state_wrapper() -> str:
        """Wrapper for explain_state."""
        if _current_athlete_state is None:
            return "Error: Athlete state not available"
        return explain_training_state(_current_athlete_state)

    def _plan_week_wrapper() -> str:
        """Wrapper for plan_week."""
        if _current_athlete_state is None:
            return "Error: Athlete state not available"
        return plan_week(_current_athlete_state)

    def _add_workout_wrapper(workout_description: str) -> str:
        """Wrapper for add_workout."""
        if _current_athlete_state is None:
            return "Error: Athlete state not available"
        return add_workout(_current_athlete_state, workout_description)

    def _run_analysis_wrapper() -> str:
        """Wrapper for run_analysis."""
        if _current_athlete_state is None:
            return "Error: Athlete state not available"
        return run_analysis(_current_athlete_state)

    def _share_report_wrapper() -> str:
        """Wrapper for share_report."""
        if _current_athlete_state is None:
            return "Error: Athlete state not available"
        return share_report(_current_athlete_state)

    def _plan_race_wrapper(race_description: str) -> str:
        """Wrapper for plan_race."""
        return plan_race_build(race_description)

    def _plan_season_wrapper() -> str:
        """Wrapper for plan_season."""
        return plan_season()

    def _handle_open_question(question: str) -> str:
        """Handle general training questions using LLM knowledge.

        This tool should be used when the question doesn't fit into any
        specific coaching tool category, such as general training advice,
        technique questions, nutrition, or other open-ended inquiries.
        """
        error_message = (
            "I'm unable to answer general questions right now. "
            "Please try asking about your specific training state "
            "or use one of the coaching tools."
        )

        if _llm is None or HumanMessage is None:
            return error_message

        # Build context with athlete state if available
        context_parts = []
        if _current_athlete_state is not None:
            context_parts.append(
                f"""Current athlete training state:
- Fitness (CTL): {_current_athlete_state.ctl:.1f}
- Fatigue (ATL): {_current_athlete_state.atl:.1f}
- Form (TSB): {_current_athlete_state.tsb:.1f}
- Load Trend: {_current_athlete_state.load_trend}
- Flags: {", ".join(_current_athlete_state.flags) if _current_athlete_state.flags else "none"}
- Days since rest: {_current_athlete_state.days_since_rest}
- 7-day volume: {_current_athlete_state.seven_day_volume_hours:.1f} hours
"""
            )

        context = "\n".join(context_parts) if context_parts else ""

        prompt_text = f"""You are Virtus Coach, an elite endurance training intelligence system.
You provide expert, personalized coaching advice based on training science.

{context}

User question: {question}

Provide a helpful, knowledgeable answer. If the question relates to the athlete's current training state, reference it.
Keep responses concise (2-3 paragraphs max) and actionable. Focus on practical training advice."""

        try:
            response = _llm.invoke([HumanMessage(content=prompt_text)])
            if not hasattr(response, "content"):
                return str(response)
            content = response.content
            if isinstance(content, str):
                return content
            if isinstance(content, list):
                return " ".join(str(item) for item in content if isinstance(item, str))
            return str(content)
        except Exception as e:
            logger.error(f"Error answering open question: {e}")
            return "I encountered an error answering your question. Could you try rephrasing it or use a specific coaching tool?"

    # Create tool list
    _coaching_tools = [
        _make_tool(
            _recommend_next_session_wrapper,
            "recommend_next_session",
            ("Recommends what workout the athlete should do next based on their current training state, fatigue, and load balance."),
        ),
        _make_tool(
            _adjust_load_wrapper,
            "adjust_training_load",
            (
                "Adjusts training load based on athlete feedback about fatigue, "
                "recovery, or how they're feeling. Use when athlete mentions being "
                "tired, strong, or wants to modify training."
            ),
        ),
        _make_tool(
            _explain_state_wrapper,
            "explain_training_state",
            (
                "Explains the athlete's current fitness, fatigue, and readiness "
                "state. Use when athlete asks about their current state, metrics, "
                "or how they're doing."
            ),
        ),
        _make_tool(
            _plan_week_wrapper,
            "plan_week",
            (
                "Generates a detailed weekly training plan tailored to the "
                "athlete's current state. Use when athlete asks for a week plan, "
                "weekly schedule, or weekly training structure."
            ),
        ),
        _make_tool(
            _add_workout_wrapper,
            "add_workout",
            (
                "Adds a specific workout to the training plan. Use when athlete "
                "wants to add a workout, schedule a session, or plan a specific "
                "training session. Requires workout_description parameter with "
                "details of the workout."
            ),
        ),
        _make_tool(
            _run_analysis_wrapper,
            "run_analysis",
            (
                "Runs comprehensive training analysis on the athlete's current "
                "state, including load metrics, trends, volume analysis, and "
                "recommendations. Use when athlete asks for analysis, insights, "
                "or detailed breakdown of their training."
            ),
        ),
        _make_tool(
            _share_report_wrapper,
            "share_report",
            (
                "Generates a formatted, shareable training report with key "
                "metrics, status, and recommendations. Use when athlete wants to "
                "share a report, get a summary, or export their training status."
            ),
        ),
        _make_tool(
            _plan_race_wrapper,
            "plan_race",
            (
                "Plans training for a specific race. Use when athlete mentions "
                "a race, race date, or wants to plan a race build. Requires "
                "race_description parameter with race details (distance, date, etc.)."
            ),
        ),
        _make_tool(
            _plan_season_wrapper,
            "plan_season",
            (
                "Generates a high-level season planning framework. Use when "
                "athlete asks about season planning, annual plan, or long-term "
                "training structure."
            ),
        ),
        _make_tool(
            _handle_open_question,
            "answer_general_question",
            (
                "Answers general training questions, technique questions, "
                "nutrition advice, or any open-ended inquiries about training. "
                "Use this for questions that don't fit into specific tool "
                "categories. Requires question parameter with the user's question."
            ),
        ),
    ]

    # Create agent prompt
    if ChatPromptTemplate is None or MessagesPlaceholder is None:
        _orchestrator_prompt = None
    else:
        _orchestrator_prompt = ChatPromptTemplate.from_messages([
            (
                "system",
                """You are Virtus Coach Orchestrator - an intelligent coaching assistant that helps athletes optimize their training.

You have access to various coaching tools that can:
- Recommend next sessions
- Adjust training load
- Explain training state
- Plan weekly training
- Add specific workouts
- Run training analysis
- Generate shareable reports
- Plan for races
- Plan entire seasons
- Answer general training questions (open questions, technique, nutrition, etc.)

Your role is to understand what the athlete needs and use the appropriate tools to help them.

Guidelines:
- Always consider the athlete's current training state when making recommendations
- If multiple tools could be useful, use them in sequence
- Be conversational and helpful
- For general questions about training, technique, nutrition, or any "
                "open-ended inquiries, use answer_general_question\n"
                "- If the request is unclear, use explain_state or run_analysis "
                "first to understand the situation better\n"
                "- When answering open questions, you can combine tools "
                "(e.g., first check their state with run_analysis, then answer "
                "their question with context)

Available tools are described below. Choose the most appropriate tool(s) for each request.
""",
            ),
            ("human", "{input}"),
            MessagesPlaceholder(variable_name="agent_scratchpad"),
        ])

    # Create agent executor
    if _llm is not None and _orchestrator_prompt is not None and create_openai_tools_agent is not None and AgentExecutor is not None:
        try:
            agent = create_openai_tools_agent(_llm, _coaching_tools, _orchestrator_prompt)
            _orchestrator_agent = AgentExecutor(
                agent=agent,
                tools=_coaching_tools,
                verbose=True,
                max_iterations=5,
                handle_parsing_errors=True,
            )
        except Exception as e:
            logger.error(f"Failed to create orchestrator agent: {e}")
            _orchestrator_agent = None
    else:
        _orchestrator_agent = None


def run_orchestrator(user_message: str, athlete_state: AthleteState) -> str:
    """Run the orchestrator agent to handle user requests.

    Args:
        user_message: User's message/request to the coach
        athlete_state: Current athlete training state

    Returns:
        Response from the orchestrator after tool execution
    """
    if not LANGCHAIN_AVAILABLE:
        logger.error("LangChain not available, cannot use orchestrator")
        return "Orchestrator not available. Please use the standard dispatcher."

    if _orchestrator_agent is None:
        logger.error("Orchestrator agent not initialized")
        return "Orchestrator agent not available. Check OpenAI API key configuration."

    # Store athlete state for tool access
    # Note: Using module-level variable is necessary because LangChain tools
    # don't support context passing. This is a known limitation.
    global _current_athlete_state  # noqa: PLW0603
    _current_athlete_state = athlete_state

    try:
        logger.info(f"Running orchestrator for message: {user_message[:100]}")
        result = _orchestrator_agent.invoke({"input": user_message})

        # Extract output from agent result
        output = result.get(
            "output",
            "I'm here to help with your training. Could you rephrase your question?",
        )
        logger.info("Orchestrator completed successfully")
    except Exception as e:
        logger.error(f"Error running orchestrator: {e}", exc_info=True)
        output = f"I encountered an error processing your request: {e!s}. Please try rephrasing or use a specific coaching tool."
    finally:
        _current_athlete_state = None

    return output
