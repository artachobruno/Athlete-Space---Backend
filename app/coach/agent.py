from langchain_core.output_parsers import PydanticOutputParser
from langchain_core.prompts import ChatPromptTemplate
from langchain_openai import ChatOpenAI
from loguru import logger
from pydantic import SecretStr

from app.coach.instructions import COACH_AGENT_INSTRUCTIONS
from app.coach.models import AthleteState
from app.coach.responses import CoachAgentResponse
from app.core.settings import settings

if not settings.openai_api_key:
    logger.warning("OPENAI_API_KEY is not set. Coach LLM features will not work.")
    # Create LLM without API key - it will fail with a clear error when used
    llm = None
else:
    llm = ChatOpenAI(
        model="gpt-4o-mini",
        temperature=0.2,
        api_key=SecretStr(settings.openai_api_key),
    )

output_parser = PydanticOutputParser(pydantic_object=CoachAgentResponse)

prompt = ChatPromptTemplate.from_messages([
    ("system", COACH_AGENT_INSTRUCTIONS),
    (
        "human",
        "Athlete state:\n{athlete_state}\n\n{format_instructions}",
    ),
])

# Create chain only if LLM is configured
if llm is not None:
    coach_chain = prompt | llm | output_parser
else:
    coach_chain = None


def run_coach_chain(state: AthleteState) -> CoachAgentResponse:
    """Run the coach chain to generate coaching advice.

    Raises:
        ValueError: If OPENAI_API_KEY is not configured
        RuntimeError: If LLM call or parsing fails
    """
    if coach_chain is None or not settings.openai_api_key:
        raise ValueError(
            "OPENAI_API_KEY is not set. Please configure it in your .env file or environment variables. "
            "Add: OPENAI_API_KEY=sk-your-key-here"
        )

    logger.info("Running coach chain")

    try:
        result = coach_chain.invoke({
            "athlete_state": state.model_dump_json(indent=2),
            "format_instructions": output_parser.get_format_instructions(),
        })
    except ValueError:
        # Re-raise ValueError (e.g., missing API key) as-is
        raise
    except Exception as e:
        logger.error(f"Error in coach chain: {type(e).__name__}: {e}", exc_info=True)
        raise RuntimeError(f"Failed to generate coach advice: {type(e).__name__}: {e}") from e
    else:
        logger.info("Coach chain completed successfully")
        return result
