"""Integration status endpoint for all providers.

Returns status for Strava and Garmin integrations.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends
from loguru import logger
from sqlalchemy import select, text
from sqlalchemy.exc import ProgrammingError

from app.api.dependencies.auth import get_current_user_id
from app.db.models import StravaAccount, UserIntegration
from app.db.session import get_session

router = APIRouter(prefix="/integrations", tags=["integrations"])


@router.get("/status")
def integrations_status(user_id: str = Depends(get_current_user_id)):
    """Get integration status for all providers.

    Returns connection status for Strava and Garmin.
    Never exposes tokens or sensitive data.

    Args:
        user_id: Current authenticated user ID (from auth dependency)

    Returns:
        List of integration status objects
    """
    logger.info(f"[INTEGRATIONS_STATUS] Status check for user_id={user_id}")

    integrations: list[dict[str, str | bool | None]] = []

    with get_session() as session:
        # Check Strava
        strava_account = session.execute(
            select(StravaAccount).where(StravaAccount.user_id == user_id)
        ).first()

        integrations.append(
            {
                "provider": "strava",
                "connected": strava_account is not None,
                "last_sync_at": strava_account[0].last_sync_at.isoformat() if strava_account and strava_account[0].last_sync_at else None,
            }
        )

        # Check Garmin
        # Handle schema mismatch gracefully - if column doesn't exist, use raw SQL
        garmin_integration = None
        try:
            garmin_integration = session.execute(
                select(UserIntegration).where(
                    UserIntegration.user_id == user_id,
                    UserIntegration.provider == "garmin",
                    UserIntegration.revoked_at.is_(None),
                )
            ).first()
        except ProgrammingError as e:
            # Check if this is a schema mismatch error (missing column)
            error_str = str(e.orig) if hasattr(e, "orig") else str(e)
            if "historical_backfill_cursor_date" in error_str or "does not exist" in error_str:
                logger.warning(
                    f"[INTEGRATIONS_STATUS] Schema mismatch detected - missing column. "
                    f"Using fallback query for user_id={user_id}"
                )
                # Fallback: Use raw SQL to check if integration exists without the missing column
                result = session.execute(
                    text("""
                        SELECT id, user_id, provider, last_sync_at
                        FROM user_integrations
                        WHERE user_id = :user_id
                        AND provider = 'garmin'
                        AND revoked_at IS NULL
                    """),
                    {"user_id": user_id},
                ).first()
                if result:
                    # Create a minimal response without the missing columns
                    garmin_integration = (type("obj", (object,), {
                        "id": result[0],
                        "user_id": result[1],
                        "provider": result[2],
                        "last_sync_at": result[3],
                        "historical_backfill_complete": False,
                        "historical_backfill_cursor_date": None,
                    })(),)
            else:
                # Re-raise if it's a different error
                raise

        last_sync_str = (
            garmin_integration[0].last_sync_at.isoformat()
            if garmin_integration and garmin_integration[0].last_sync_at
            else None
        )
        garmin_data: dict[str, str | bool | None] = {
            "provider": "garmin",
            "connected": garmin_integration is not None,
            "last_sync_at": last_sync_str,
        }

        # Add historical backfill progress for Garmin (if available)
        if garmin_integration:
            integration_obj = garmin_integration[0]
            # Only access these fields if they exist (may be None in fallback case)
            if hasattr(integration_obj, "historical_backfill_complete"):
                garmin_data["historical_backfill_complete"] = integration_obj.historical_backfill_complete
            if hasattr(integration_obj, "historical_backfill_cursor_date"):
                garmin_data["historical_backfill_cursor_date"] = (
                    integration_obj.historical_backfill_cursor_date.isoformat()
                    if integration_obj.historical_backfill_cursor_date
                    else None
                )

        integrations.append(garmin_data)

    logger.debug(f"[INTEGRATIONS_STATUS] Found {sum(1 for i in integrations if i['connected'])} connected integrations")

    return {"integrations": integrations}
