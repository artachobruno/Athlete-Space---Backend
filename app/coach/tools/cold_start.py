from langchain_core.prompts import ChatPromptTemplate
from langchain_openai import ChatOpenAI
from loguru import logger
from pydantic import SecretStr

from app.coach.schemas.athlete_state import AthleteState
from app.config.settings import settings

COLD_START_INSTRUCTIONS = """
You are AI Coach — an elite endurance training intelligence system.

This is the first time you're speaking with this athlete. Talk like a human coach having a casual conversation.

Rules:
- Keep it SHORT: 1-2 sentences maximum, not paragraphs
- Be conversational and natural, like a real person
- If data is missing or limited, ASK QUESTIONS to understand their goals
- Don't list all your capabilities — just be friendly and helpful
- No formal introductions — just start chatting naturally

CRITICAL RULES FOR TRAINING STATE:
- If athlete state confidence is very low (< 0.1), you have INSUFFICIENT data
- NEVER make statements about current fatigue, training state, metrics, or how the athlete is feeling when:
  * athlete_state is None, OR
  * athlete_state.confidence is very low (< 0.1)
- NEVER say things like "you're pushing hard", "you're feeling fatigued", "your training load is...",
  "you're starting out strong", "looks like you need recovery", etc. when data is insufficient
- ONLY make observations about training state if confidence >= 0.1 AND you have clear, reliable data

If athlete state is provided WITH sufficient confidence (>= 0.3):
- Give ONE brief, positive observation about their training state
- Keep it casual and human-sounding
- No metrics or technical terms — just natural language
- Focus on positive aspects, not assumptions about fatigue or recovery needs

If athlete state is provided BUT confidence is LOW (0.1 <= confidence < 0.3):
- Acknowledge that you have some training data but it's limited
- Ask what they're training for or what they need help with
- Don't make specific observations about fatigue, recovery, or training state
- Be encouraging and ready to help as more data comes in

If athlete state is provided BUT confidence is low (< 0.1) OR no athlete state:
- Ask them what they're training for or what they need help with
- Don't make any statements about their current training state
- Don't explain what you can do — just be ready to help

Examples:
BAD: "Hello and welcome! I'm your Coach, your dedicated AI training coach..."

BAD (insufficient data): "Hey! Looks like you're pushing hard but might need a little recovery time."

GOOD (with sufficient data): "Hey! I see your training looks solid — how can I help you today?"

GOOD (insufficient data): "Hi! What are you training for? I'd love to help you reach your goals."

Return ONLY the message text. No metadata or structure. Keep it under 50 words.
"""

if not settings.openai_api_key:
    _cold_start_llm = None
    logger.warning("OPENAI_API_KEY is not set. Cold start LLM features will not work.")
else:
    _cold_start_llm = ChatOpenAI(
        model="gpt-4o-mini",
        temperature=0.3,
        api_key=SecretStr(settings.openai_api_key),
    )

_cold_start_prompt = ChatPromptTemplate.from_messages([
    ("system", COLD_START_INSTRUCTIONS),
    ("human", "{context}"),
])

if _cold_start_llm is not None:
    _cold_start_chain = _cold_start_prompt | _cold_start_llm
else:
    _cold_start_chain = None


def _build_cold_start_context(state: AthleteState | None) -> str:
    """Build context string for cold start message generation.

    Args:
        state: Optional athlete state.

    Returns:
        Context string for LLM prompt.
    """
    if state is None:
        return (
            "Generate a short, casual welcome message (1-2 sentences max). "
            "No training data available yet. Ask them what they're training for or what they need help with. "
            "Be conversational and natural — like a real person, not a formal introduction."
        )

    has_sufficient_data = state.confidence >= 0.3
    has_some_data = state.confidence >= 0.1

    if has_sufficient_data:
        return f"""Generate a short, casual welcome message (1-2 sentences max).

Athlete's training state (data confidence: {state.confidence:.2f}):
- Fitness level: {state.ctl:.1f}
- Fatigue: {state.atl:.1f}
- Form: {state.tsb:.1f}
- Trend: {state.load_trend}
- Flags: {", ".join(state.flags) if state.flags else "none"}

Give ONE brief, positive observation about their training. Then ask how you can help.
Keep it conversational, like a human coach would talk.
Focus on positive aspects - don't assume fatigue or recovery needs."""

    if has_some_data:
        return f"""Generate a short, casual welcome message (1-2 sentences max).

Training data confidence is MODERATE ({state.confidence:.2f} - between 0.1 and 0.3). You have SOME data but it's limited.

ABSOLUTE RULES:
- Acknowledge that you have some training data but it's limited
- Ask what they're training for or what they need help with
- NEVER make specific observations about fatigue, recovery, or training state
- NEVER say things like "you're pushing hard", "you're feeling fatigued", "looks like you need recovery", etc.
- Be encouraging and ready to help as more data comes in
- Be conversational and natural — like a real person, not a formal introduction."""

    return f"""Generate a short, casual welcome message (1-2 sentences max).

CRITICAL: Training data confidence is VERY LOW ({state.confidence:.2f} < 0.1). You have INSUFFICIENT data.

ABSOLUTE RULES:
- NEVER make statements about current fatigue, training state, or how the athlete is feeling
- NEVER say things like "you're pushing hard", "you're feeling fatigued", "looks like you need recovery", etc.
- ONLY ask what they're training for or what they need help with
- Be conversational and natural — like a real person, not a formal introduction."""


def welcome_new_user(state: AthleteState | None = None) -> str:
    """Generate a welcome message using the LLM agent for new users.

    Args:
        state: Optional athlete state. If provided, includes personalized information.

    Returns:
        A welcoming message generated by the LLM introducing the coach and its capabilities.
    """
    if _cold_start_chain is None or not settings.openai_api_key:
        logger.warning("LLM not available for cold start, using fallback message")
        if state is not None and state.confidence >= 0.3:
            fallback = "Hey! I see your training looks good. What can I help you with today?"
        else:
            fallback = "Hi! What are you training for? I'd love to help you reach your goals."
        return fallback

    context = _build_cold_start_context(state)

    try:
        logger.info("Generating cold start welcome message with LLM")
        response = _cold_start_chain.invoke({"context": context})
        content = response.content
        if isinstance(content, str):
            message = content
        elif isinstance(content, list):
            message = " ".join(str(item) for item in content if isinstance(item, str))
        else:
            message = str(content)
        logger.info("Cold start message generated successfully")
    except Exception as e:
        logger.error(f"Error generating cold start message: {e}", exc_info=True)
        if state is not None and state.confidence >= 0.3:
            return "Hey! I see your training looks good. What can I help you with today?"
        return "Hi! What are you training for? I'd love to help you reach your goals."
    else:
        return message
