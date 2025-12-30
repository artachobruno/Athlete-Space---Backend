from pydantic import BaseModel


class CoachChatRequest(BaseModel):
    message: str
    days: int = 60
    days_to_race: int | None = None


class CoachChatResponse(BaseModel):
    intent: str
    reply: str
