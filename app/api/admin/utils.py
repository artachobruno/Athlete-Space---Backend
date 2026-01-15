"""Admin utility functions for access control."""

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config.settings import settings
from app.db.models import User


def require_admin(user_id: str, session: Session) -> None:
    """Require that the user is an admin or dev user.

    Checks admin status via:
    1. Dev user ID (DEV_USER_ID env var)
    2. Admin user IDs list (ADMIN_USER_IDS env var - comma-separated)
    3. Admin emails list (ADMIN_EMAILS env var - comma-separated)

    Raises HTTPException 401/403 if access is denied.

    Args:
        user_id: User ID to check (must not be None or empty)
        session: Database session (used to query user email for email-based allowlist)

    Raises:
        HTTPException: 401 if user_id is missing or user not found
        HTTPException: 403 if user is not admin/dev
    """
    # Defensive check: user_id must be provided
    if not user_id:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication required",
        )

    # Dev mode: check if user_id matches dev_user_id
    if settings.dev_user_id and user_id == settings.dev_user_id:
        return

    # Admin: check if user_id is in admin list
    if settings.admin_user_ids:
        admin_list = [uid.strip() for uid in settings.admin_user_ids.split(",") if uid.strip()]
        if user_id in admin_list:
            return

    # Admin: check if user email is in admin emails list
    if settings.admin_emails:
        try:
            user_result = session.execute(select(User).where(User.id == user_id)).first()
            if not user_result:
                raise HTTPException(
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    detail="User not found",
                )

            user = user_result[0]
            admin_email_list = [email.strip().lower() for email in settings.admin_emails.split(",") if email.strip()]
            if user.email and user.email.lower() in admin_email_list:
                return
        except HTTPException:
            raise
        except Exception as e:
            # Log but don't expose internal errors - just deny access
            from loguru import logger

            logger.error(f"Error checking admin email for user_id={user_id}: {e}")
            # Fall through to 403 below

    raise HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail="Admin access required",
    )
