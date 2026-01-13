"""Admin utility functions for access control."""

from fastapi import HTTPException, status
from sqlalchemy.orm import Session

from app.config.settings import settings


def require_admin(user_id: str, _session: Session) -> None:
    """Require that the user is an admin or dev user.

    Checks if user_id matches dev_user_id or is in admin_user_ids list.
    Raises HTTPException 403 if access is denied.

    Args:
        user_id: User ID to check
        _session: Database session (unused, kept for consistency with other guards)

    Raises:
        HTTPException: 403 if user is not admin/dev
    """
    # Dev mode: check if user_id matches dev_user_id
    if settings.dev_user_id and user_id == settings.dev_user_id:
        return

    # Admin: check if user_id is in admin list
    if settings.admin_user_ids:
        admin_list = [uid.strip() for uid in settings.admin_user_ids.split(",") if uid.strip()]
        if user_id in admin_list:
            return

    raise HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail="Admin access required",
    )
