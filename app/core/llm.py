"""LLM model abstraction for consistent model access across the application."""

from pydantic_ai.models.openai import OpenAIModel


def get_model(provider: str, model_name: str):
    if provider == "openai":
        return OpenAIModel(model_name)

    raise ValueError(f"Unsupported LLM provider: {provider}")
