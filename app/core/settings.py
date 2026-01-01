import os
from pathlib import Path

from loguru import logger
from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


def get_database_url() -> str:
    """Get database URL, using absolute path for SQLite to avoid path resolution issues."""
    db_url = os.getenv("DATABASE_URL", "")
    if db_url:
        logger.info(f"Using DATABASE_URL from environment: {db_url}")
        return db_url

    # Use absolute path for SQLite
    db_path = Path(__file__).parent.parent.parent / "virtus.db"
    abs_path = db_path.resolve()
    db_url = f"sqlite:///{abs_path}"
    logger.info(f"Using default database path: {db_url}")
    return db_url


class Settings(BaseSettings):
    strava_client_id: str = Field(default="", validation_alias="STRAVA_CLIENT_ID")
    strava_client_secret: str = Field(default="", validation_alias="STRAVA_CLIENT_SECRET")
    strava_redirect_uri: str = Field(
        default="http://localhost:8000/strava/callback",  # Default for local dev; MUST be set to backend URL in production
        validation_alias="STRAVA_REDIRECT_URI",
    )
    frontend_url: str = Field(
        default="http://localhost:8501",  # Default for local dev; overridden in production via detection or FRONTEND_URL env var
        validation_alias="FRONTEND_URL",
    )
    database_url: str = Field(
        default_factory=get_database_url,
        validation_alias="DATABASE_URL",
    )
    redis_url: str = Field(default="redis://localhost:6379/0", validation_alias="REDIS_URL")
    openai_api_key: str = Field(default="", validation_alias="OPENAI_API_KEY")
    user_ui_enabled: bool = Field(default=False, validation_alias="USER_UI_ENABLED")

    model_config = SettingsConfigDict(
        env_file=".env",
        env_prefix="STRAVA_",
    )

    @field_validator("strava_client_id", "strava_client_secret")
    @classmethod
    def validate_required(cls, value: str) -> str:
        if not value:
            raise ValueError(
                "STRAVA_CLIENT_ID and STRAVA_CLIENT_SECRET environment variables are required. "
                "Set them in .env file or environment variables."
            )
        return value


settings = Settings()
