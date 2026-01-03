"""DEPRECATED: Authentication middleware (Clerk-based).

This module is deprecated and replaced by app.api.dependencies.auth (JWT-based).
Kept for reference only. All routes now use get_current_user_id from app.api.dependencies.auth.
"""

# This file is kept for reference but is no longer used.
# All authentication now uses JWT tokens issued by the backend.
# See app.api.dependencies.auth for the new authentication dependency.
