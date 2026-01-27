"""Admin endpoint for running database migrations.

Provides admin-only endpoints to manually trigger migrations in production.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, status
from loguru import logger
from pydantic import BaseModel

from app.api.admin.utils import require_admin
from app.api.dependencies.auth import get_current_user_id
from app.db.session import get_session
from scripts.migrate_activities_garmin_fields import migrate_activities_garmin_fields
from scripts.migrate_garmin_webhook_events import migrate_garmin_webhook_events
from scripts.migrate_user_integrations import migrate_user_integrations

router = APIRouter(prefix="/admin/migrations", tags=["admin-migrations"])


def _raise_migration_error(errors: list[str], migrations_run: list[str]) -> None:
    """Raise HTTPException for migration errors."""
    raise HTTPException(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        detail=f"Some migrations failed: {', '.join(errors)}. Migrations run: {', '.join(migrations_run)}",
    )


class MigrationResponse(BaseModel):
    """Response model for migration execution."""

    success: bool
    message: str
    migrations_run: list[str]


@router.post("/garmin")
def run_garmin_migrations(
    all: bool = Query(True, description="Run all Garmin migrations"),
    user_integrations: bool = Query(False, description="Run user_integrations migration only"),
    webhook_events: bool = Query(False, description="Run garmin_webhook_events migration only"),
    activities: bool = Query(False, description="Run activities Garmin fields migration only"),
    user_id: str = Depends(get_current_user_id),
) -> MigrationResponse:
    """Run Garmin integration database migrations.

    Admin-only endpoint to manually trigger Garmin migrations in production.

    Args:
        all: Run all Garmin migrations (default: True)
        user_integrations: Run user_integrations migration only
        webhook_events: Run garmin_webhook_events migration only
        activities: Run activities Garmin fields migration only
        user_id: Current admin user ID (from require_admin dependency)

    Returns:
        MigrationResponse with success status and list of migrations run

    Raises:
        HTTPException: If migration fails
    """
    logger.info(f"[ADMIN_MIGRATIONS] Garmin migrations requested by user_id={user_id}")

    # Check admin access
    with get_session() as session:
        require_admin(user_id, session)

    if all:
        user_integrations = True
        webhook_events = True
        activities = True

    if not (user_integrations or webhook_events or activities):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="No migrations selected. Set all=true or specify individual migrations.",
        )

    migrations_run: list[str] = []
    errors: list[str] = []

    try:
        if user_integrations:
            logger.info("[ADMIN_MIGRATIONS] Running migration: user_integrations table")
            try:
                migrate_user_integrations()
                migrations_run.append("user_integrations")
                logger.info("[ADMIN_MIGRATIONS] ✓ user_integrations migration completed")
            except Exception as e:
                error_msg = f"user_integrations migration failed: {e}"
                logger.exception(f"[ADMIN_MIGRATIONS] {error_msg}")
                errors.append(error_msg)

        if webhook_events:
            logger.info("[ADMIN_MIGRATIONS] Running migration: garmin_webhook_events table")
            try:
                migrate_garmin_webhook_events()
                migrations_run.append("garmin_webhook_events")
                logger.info("[ADMIN_MIGRATIONS] ✓ garmin_webhook_events migration completed")
            except Exception as e:
                error_msg = f"garmin_webhook_events migration failed: {e}"
                logger.exception(f"[ADMIN_MIGRATIONS] {error_msg}")
                errors.append(error_msg)

        if activities:
            logger.info("[ADMIN_MIGRATIONS] Running migration: activities Garmin fields")
            try:
                migrate_activities_garmin_fields()
                migrations_run.append("activities_garmin_fields")
                logger.info("[ADMIN_MIGRATIONS] ✓ activities Garmin fields migration completed")
            except Exception as e:
                error_msg = f"activities_garmin_fields migration failed: {e}"
                logger.exception(f"[ADMIN_MIGRATIONS] {error_msg}")
                errors.append(error_msg)

        if errors:
            _raise_migration_error(errors, migrations_run)

        logger.info(f"[ADMIN_MIGRATIONS] All Garmin migrations completed successfully: {migrations_run}")

        return MigrationResponse(
            success=True,
            message=f"Successfully ran {len(migrations_run)} migration(s)",
            migrations_run=migrations_run,
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.exception(f"[ADMIN_MIGRATIONS] Unexpected error during migrations: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Migration execution failed: {e}",
        ) from e
