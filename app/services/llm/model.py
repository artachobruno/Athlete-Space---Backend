"""LLM model abstraction for consistent model access across the application."""

import os

from pydantic_ai.models.openai import OpenAIModel

from app.config.settings import settings


def get_model(provider: str, model_name: str):
    if provider == "openai":
        # Ensure OPENAI_API_KEY is set from settings for pydantic_ai
        if settings.openai_api_key and not os.getenv("OPENAI_API_KEY"):
            os.environ["OPENAI_API_KEY"] = settings.openai_api_key
        return OpenAIModel(model_name)

    raise ValueError(f"Unsupported LLM provider: {provider}")
