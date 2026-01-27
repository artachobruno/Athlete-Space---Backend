"""Startup schema verification to fail fast on missing columns.

Checks that SQLAlchemy model columns match database columns before the app starts.
Fails fast > silent corruption.

Compares model.__table__.columns with actual database columns.
Any mismatch raises RuntimeError to prevent serving requests with invalid schema.
"""

from __future__ import annotations

from loguru import logger
from sqlalchemy import inspect

from app.db.models import Activity, AthleteProfile, GarminWebhookEvent, PlannedSession, User, UserIntegration, UserSettings
from app.db.session import engine


def verify_schema() -> None:
    """Verify database schema matches SQLAlchemy models.

    Compares model.__table__.columns with actual database columns.
    Raises RuntimeError if any model columns are missing in the database.

    Raises:
        RuntimeError: If any model columns are missing in the database
    """
    logger.info("Verifying database schema matches SQLAlchemy models...")
    inspector = inspect(engine)

    # Check User model
    if inspector.has_table("users"):  # pyright: ignore[reportOptionalMemberAccess]
        db_cols = {col["name"] for col in inspector.get_columns("users")}  # pyright: ignore[reportOptionalMemberAccess]
        model_cols = set(User.__table__.columns.keys())
        missing = model_cols - db_cols
        if missing:
            raise RuntimeError(
                f"DB schema mismatch in users table. Model columns missing in DB: {missing}. Run migrations to add these columns."
            )
        logger.debug(f"✓ users table verified ({len(model_cols)} columns match)")

    # Check AthleteProfile model
    if inspector.has_table("athlete_profiles"):  # pyright: ignore[reportOptionalMemberAccess]
        db_cols = {col["name"] for col in inspector.get_columns("athlete_profiles")}  # pyright: ignore[reportOptionalMemberAccess]
        model_cols = set(AthleteProfile.__table__.columns.keys())
        missing = model_cols - db_cols
        if missing:
            raise RuntimeError(
                f"DB schema mismatch in athlete_profiles table. "
                f"Model columns missing in DB: {missing}. "
                f"Run migrations to add these columns."
            )
        logger.debug(f"✓ athlete_profiles table verified ({len(model_cols)} columns match)")

    # Check UserSettings model (critical for coach chat and training features)
    if inspector.has_table("user_settings"):  # pyright: ignore[reportOptionalMemberAccess]
        db_cols = {col["name"] for col in inspector.get_columns("user_settings")}  # pyright: ignore[reportOptionalMemberAccess]
        model_cols = set(UserSettings.__table__.columns.keys())
        missing = model_cols - db_cols
        if missing:
            raise RuntimeError(
                f"DB schema mismatch in user_settings table. "
                f"Model columns missing in DB: {missing}. "
                f"Run migrations to add these columns."
            )
        logger.debug(f"✓ user_settings table verified ({len(model_cols)} columns match)")

    # Check PlannedSession model (critical for calendar persistence and B7)
    if inspector.has_table("planned_sessions"):  # pyright: ignore[reportOptionalMemberAccess]
        db_cols = {col["name"] for col in inspector.get_columns("planned_sessions")}  # pyright: ignore[reportOptionalMemberAccess]
        model_cols = set(PlannedSession.__table__.columns.keys())
        missing = model_cols - db_cols
        if missing:
            raise RuntimeError(
                f"DB schema mismatch in planned_sessions table. "
                f"Model columns missing in DB: {missing}. "
                f"Run migrations to add these columns. "
                f"Missing columns will cause B7 calendar persistence failures."
            )
        logger.debug(f"✓ planned_sessions table verified ({len(model_cols)} columns match)")

    # Check Activity model (for Garmin fields)
    # Only check if table exists (migrations will create it if needed)
    if inspector.has_table("activities"):  # pyright: ignore[reportOptionalMemberAccess]
        db_cols = {col["name"] for col in inspector.get_columns("activities")}  # pyright: ignore[reportOptionalMemberAccess]
        model_cols = set(Activity.__table__.columns.keys())
        missing = model_cols - db_cols
        if missing:
            raise RuntimeError(
                f"DB schema mismatch in activities table. "
                f"Model columns missing in DB: {missing}. "
                f"Run migrations to add these columns."
            )
        logger.debug(f"✓ activities table verified ({len(model_cols)} columns match)")

    # Check UserIntegration model (Garmin integration)
    # Only check if table exists (migrations will create it if needed)
    if inspector.has_table("user_integrations"):  # pyright: ignore[reportOptionalMemberAccess]
        db_cols = {col["name"] for col in inspector.get_columns("user_integrations")}  # pyright: ignore[reportOptionalMemberAccess]
        model_cols = set(UserIntegration.__table__.columns.keys())
        missing = model_cols - db_cols
        if missing:
            raise RuntimeError(
                f"DB schema mismatch in user_integrations table. "
                f"Model columns missing in DB: {missing}. "
                f"Run migrations to add these columns."
            )
        logger.debug(f"✓ user_integrations table verified ({len(model_cols)} columns match)")
    else:
        logger.debug("user_integrations table does not exist yet (migrations will create it)")

    # Check GarminWebhookEvent model
    # Only check if table exists (migrations will create it if needed)
    if inspector.has_table("garmin_webhook_events"):  # pyright: ignore[reportOptionalMemberAccess]
        db_cols = {col["name"] for col in inspector.get_columns("garmin_webhook_events")}  # pyright: ignore[reportOptionalMemberAccess]
        model_cols = set(GarminWebhookEvent.__table__.columns.keys())
        missing = model_cols - db_cols
        if missing:
            raise RuntimeError(
                f"DB schema mismatch in garmin_webhook_events table. "
                f"Model columns missing in DB: {missing}. "
                f"Run migrations to add these columns."
            )
        logger.debug(f"✓ garmin_webhook_events table verified ({len(model_cols)} columns match)")
    else:
        logger.debug("garmin_webhook_events table does not exist yet (migrations will create it)")

    logger.info("✓ Database schema verification completed - all model columns exist in DB")


if __name__ == "__main__":
    verify_schema()
