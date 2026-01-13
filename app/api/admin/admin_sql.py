"""Admin endpoint for SQL query execution.

This module provides an admin-only endpoint for executing read-only SQL queries.
Only SELECT queries are allowed for safety.
"""

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.api.admin.utils import require_admin
from app.api.dependencies.auth import get_current_user_id
from app.db.session import get_session

router = APIRouter(prefix="/admin/sql", tags=["admin-sql"])

MAX_ROWS = 500


class SQLQueryRequest(BaseModel):
    """Request model for SQL query execution."""

    sql: str


class SQLQueryResponse(BaseModel):
    """Response model for SQL query execution."""

    columns: list[str]
    rows: list[list[str | int | float | None]]
    row_count: int


@router.post("/query", response_model=SQLQueryResponse)
def run_sql(
    payload: SQLQueryRequest,
    session: Session = Depends(get_session),
    user_id: str = Depends(get_current_user_id),
) -> SQLQueryResponse:
    """Execute a read-only SQL query (admin only).

    Only SELECT queries are allowed for safety. Results are limited to MAX_ROWS.

    Args:
        payload: SQL query request containing the SQL string
        session: Database session
        user_id: Current authenticated user ID (from auth dependency)

    Returns:
        Dictionary with:
        - columns: List of column names
        - rows: List of rows (each row is a list of values)
        - row_count: Number of rows returned

    Raises:
        HTTPException: 401 if authentication is missing or invalid
        HTTPException: 403 if user is not admin
        HTTPException: 400 if query is not SELECT or execution fails
    """
    # B1: Explicit auth check - fail cleanly when unauthenticated
    if not user_id:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication required",
        )

    # B2: Admin guard - ensure only admins can execute SQL queries
    require_admin(user_id, session)

    sql = payload.sql.strip()

    if not sql.lower().startswith("select"):
        raise HTTPException(status_code=400, detail="Only SELECT queries allowed")

    try:
        result = session.execute(text(sql))
        rows = result.fetchmany(MAX_ROWS)
        columns = list(result.keys())

        # Convert rows to lists, handling None values
        rows_list: list[list[str | int | float | None]] = [list(row) for row in rows]

        return SQLQueryResponse(
            columns=columns,
            rows=rows_list,
            row_count=len(rows_list),
        )

    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
