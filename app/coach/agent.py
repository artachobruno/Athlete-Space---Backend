from langchain_core.output_parsers import PydanticOutputParser
from langchain_core.prompts import ChatPromptTemplate
from langchain_openai import ChatOpenAI
from loguru import logger

from app.coach.instructions import COACH_AGENT_INSTRUCTIONS
from app.coach.models import AthleteState
from app.coach.responses import CoachAgentResponse

llm = ChatOpenAI(
    model="gpt-4o",
    temperature=0.2,
)

output_parser = PydanticOutputParser(pydantic_object=CoachAgentResponse)

prompt = ChatPromptTemplate.from_messages([
    ("system", COACH_AGENT_INSTRUCTIONS),
    (
        "human",
        "Athlete state:\n{athlete_state}\n\n{format_instructions}",
    ),
])

coach_chain = prompt | llm | output_parser


def run_coach_chain(state: AthleteState) -> CoachAgentResponse:
    logger.info("Running coach chain")

    return coach_chain.invoke({
        "athlete_state": state.model_dump_json(indent=2),
        "format_instructions": output_parser.get_format_instructions(),
    })
