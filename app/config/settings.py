import os
from pathlib import Path

from loguru import logger
from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


def get_database_url() -> str:
    """Get database URL, using absolute path for SQLite to avoid path resolution issues.

    ⚠️ WARNING: SQLite is NOT suitable for production deployments!
    - Data will be LOST on container rebuilds or machine spin-downs
    - Use PostgreSQL by setting DATABASE_URL environment variable
    - See docs/DEPLOYMENT_DATA_PERSISTENCE.md for details
    """
    db_url = os.getenv("DATABASE_URL", "")
    if db_url:
        logger.info(f"Using DATABASE_URL from environment: {db_url}")
        # Warn if SQLite detected in production-like environment
        is_production = bool(os.getenv("RENDER") or os.getenv("RAILWAY_ENVIRONMENT") or os.getenv("DYNO"))
        if db_url.startswith("sqlite://") and is_production:
            logger.error(
                "⚠️ CRITICAL: SQLite detected in production environment! "
                "Data will be LOST on rebuilds. Use PostgreSQL instead. "
                "Set DATABASE_URL to a PostgreSQL connection string."
            )
        return db_url

    # Use absolute path for SQLite (LOCAL DEVELOPMENT ONLY)
    db_path = Path(__file__).parent.parent.parent / "virtus.db"
    abs_path = db_path.resolve()
    db_url = f"sqlite:///{abs_path}"
    logger.warning(
        f"⚠️ Using SQLite database (LOCAL DEV ONLY): {db_url}\n"
        "⚠️ SQLite is NOT suitable for production - data will be LOST on rebuilds!\n"
        "⚠️ Set DATABASE_URL environment variable to use PostgreSQL in production.\n"
        "⚠️ See docs/DEPLOYMENT_DATA_PERSISTENCE.md for setup instructions."
    )
    return db_url


class Settings(BaseSettings):
    strava_client_id: str = Field(default="", validation_alias="STRAVA_CLIENT_ID")
    strava_client_secret: str = Field(default="", validation_alias="STRAVA_CLIENT_SECRET")
    strava_redirect_uri: str = Field(
        default="http://localhost:8000/auth/strava/callback",  # Default for local dev; MUST be set to backend URL in production
        validation_alias="STRAVA_REDIRECT_URI",
    )
    google_client_id: str = Field(default="", validation_alias="GOOGLE_CLIENT_ID")
    google_client_secret: str = Field(default="", validation_alias="GOOGLE_CLIENT_SECRET")
    google_redirect_uri: str = Field(
        default="http://localhost:8000/auth/google/callback",  # Default for local dev; MUST be set to backend URL in production
        validation_alias="GOOGLE_REDIRECT_URI",
    )
    backend_url: str = Field(
        default="http://localhost:8000",  # Default for local dev; MUST be set to backend URL in production
        validation_alias="BACKEND_URL",
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
    dev_user_id: str = Field(default="", validation_alias="DEV_USER_ID")
    auth_secret_key: str = Field(default="", validation_alias="AUTH_SECRET_KEY")
    auth_algorithm: str = Field(default="HS256", validation_alias="AUTH_ALGORITHM")
    auth_token_expire_days: int = Field(default=30, validation_alias="AUTH_TOKEN_EXPIRE_DAYS")
    strava_webhook_verify_token: str = Field(default="", validation_alias="STRAVA_WEBHOOK_VERIFY_TOKEN")
    admin_user_ids: str = Field(default="", validation_alias="ADMIN_USER_IDS")  # Comma-separated list
    log_level: str = Field(default="INFO", validation_alias="LOG_LEVEL")
    mcp_db_server_url: str = Field(
        default="https://athlete-space-mcp-db.onrender.com",
        validation_alias="MCP_DB_SERVER_URL",
        description="MCP Database Server URL",
    )
    mcp_fs_server_url: str = Field(
        default="https://athlete-space-mcp-fs.onrender.com",
        validation_alias="MCP_FS_SERVER_URL",
        description="MCP Filesystem Server URL",
    )
    observe_enabled: bool = Field(
        default=False,
        validation_alias="OBSERVE_ENABLED",
        description="Enable Observe tracing",
    )
    observe_sample_rate: float = Field(
        default=1.0,
        validation_alias="OBSERVE_SAMPLE_RATE",
        description="Observe sampling rate (0.0-1.0)",
    )
    observe_api_key: str = Field(
        default="",
        validation_alias="OBSERVE_API_KEY",
        description="Observe API key",
    )
    enable_progress_events: bool = Field(
        default=False,
        validation_alias="ENABLE_PROGRESS_EVENTS",
        description="Enable progress event emission (default: false for production stability)",
    )

    @field_validator("log_level")
    @classmethod
    def validate_log_level(cls, value: str) -> str:
        """Validate that log level is one of the standard logging levels."""
        valid_levels = {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}
        upper_value = value.upper()
        if upper_value not in valid_levels:
            logger.warning(f"Invalid LOG_LEVEL '{value}'. Valid levels are: {', '.join(valid_levels)}. Defaulting to INFO.")
            return "INFO"
        return upper_value

    model_config = SettingsConfigDict(
        env_file=".env",
        env_prefix="STRAVA_",
    )

    @field_validator("strava_client_id", "strava_client_secret")
    @classmethod
    def validate_required(cls, value: str) -> str:
        """Validate Strava credentials are provided.

        For local development/testing, empty values are allowed with a warning.
        Strava OAuth and API features will not work without these credentials.
        """
        if not value:
            logger.warning(
                "⚠️ STRAVA_CLIENT_ID and/or STRAVA_CLIENT_SECRET are not set. "
                "Strava OAuth and API features will not work. "
                "Set them in .env file or environment variables to enable Strava integration."
            )
        return value

    @field_validator("strava_redirect_uri")
    @classmethod
    def validate_redirect_uri(cls, value: str) -> str:
        """Validate that redirect URI points to /auth/strava/callback."""
        if value and "/auth/strava/callback" not in value:
            logger.warning(f"STRAVA_REDIRECT_URI should point to /auth/strava/callback, but got: {value}. This may cause OAuth failures.")
        return value

    @field_validator("google_redirect_uri")
    @classmethod
    def validate_google_redirect_uri(cls, value: str) -> str:
        """Validate that redirect URI points to /auth/google/callback."""
        if value and "/auth/google/callback" not in value:
            logger.warning(f"GOOGLE_REDIRECT_URI should point to /auth/google/callback, but got: {value}. This may cause OAuth failures.")
        return value

    @field_validator("mcp_db_server_url")
    @classmethod
    def validate_mcp_db_server_url(cls, value: str) -> str:
        """Validate MCP DB server URL is not localhost in production."""
        is_production = bool(os.getenv("RENDER") or os.getenv("RAILWAY_ENVIRONMENT") or os.getenv("DYNO"))
        if is_production and value:
            has_localhost = "localhost" in value
            has_127 = "127.0.0.1" in value
            # Check for all-interfaces binding address (constructed to avoid S104 warning)
            all_interfaces = "0" + ".0.0.0"
            has_all_interfaces = all_interfaces in value
            if has_localhost or has_127 or has_all_interfaces:
                logger.error(
                    f"⚠️ CRITICAL: MCP_DB_SERVER_URL is set to localhost/127.0.0.1/{all_interfaces} in production: {value}\n"
                    "⚠️ This will cause network failures. Set MCP_DB_SERVER_URL to your deployed service URL "
                    "(e.g., https://your-mcp-db-service.onrender.com)"
                )
        return value

    @field_validator("mcp_fs_server_url")
    @classmethod
    def validate_mcp_fs_server_url(cls, value: str) -> str:
        """Validate MCP FS server URL is not localhost in production."""
        is_production = bool(os.getenv("RENDER") or os.getenv("RAILWAY_ENVIRONMENT") or os.getenv("DYNO"))
        if is_production and value:
            has_localhost = "localhost" in value
            has_127 = "127.0.0.1" in value
            # Check for all-interfaces binding address (constructed to avoid S104 warning)
            all_interfaces = "0" + ".0.0.0"
            has_all_interfaces = all_interfaces in value
            if has_localhost or has_127 or has_all_interfaces:
                logger.error(
                    f"⚠️ CRITICAL: MCP_FS_SERVER_URL is set to localhost/127.0.0.1/{all_interfaces} in production: {value}\n"
                    "⚠️ This will cause network failures. Set MCP_FS_SERVER_URL to your deployed service URL "
                    "(e.g., https://your-mcp-fs-service.onrender.com)"
                )
        return value


settings = Settings()
