"""Admin endpoint for SQL query execution.

This module provides an admin-only endpoint for executing read-only SQL queries.
Only SELECT queries and CTEs (WITH clauses) are allowed for safety.
"""

import logging

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.encoders import jsonable_encoder
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.api.admin.utils import require_admin
from app.api.dependencies.auth import get_current_user_id
from app.db.session import get_db

logger = logging.getLogger(__name__)

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


class TableInfo(BaseModel):
    """Table information model."""

    schema: str
    name: str


class ColumnInfo(BaseModel):
    """Column information model."""

    name: str
    type: str


class TablePreviewRequest(BaseModel):
    """Request model for table preview."""

    schema: str
    table: str
    limit: int = 50
    offset: int = 0
    order_by: str | None = None
    order_dir: str = "desc"  # "asc"|"desc"


def _validate_readonly(sql: str) -> str:
    """Validate that SQL query is read-only (SELECT or WITH/CTE only).

    Args:
        sql: SQL query string to validate

    Returns:
        Cleaned SQL string (stripped, semicolons removed)

    Raises:
        HTTPException: 400 if query contains write operations or is not SELECT/CTE
    """
    s = sql.strip().rstrip(";")
    lowered = s.lower()

    # Allow SELECT and WITH (CTE) queries
    if not (lowered.startswith("select") or lowered.startswith("with")):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Only SELECT and WITH (CTE) queries are allowed",
        )

    # Block write operations - check for forbidden keywords
    forbidden = [
        "insert",
        "update",
        "delete",
        "drop",
        "alter",
        "truncate",
        "create",
        "grant",
        "revoke",
        "exec",
        "execute",
        "call",
    ]

    # Check for forbidden keywords (with word boundaries)
    for keyword in forbidden:
        # Use word boundaries to avoid false positives (e.g., "select" in "selected")
        if f" {keyword} " in f" {lowered} ":
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Write operations are not allowed. Found forbidden keyword: {keyword}",
            )

    return s


@router.post("/query", response_model=SQLQueryResponse)
def run_sql(
    payload: SQLQueryRequest,
    db: Session = Depends(get_db),
    user_id: str = Depends(get_current_user_id),
) -> SQLQueryResponse:
    """Execute a read-only SQL query (admin only).

    Only SELECT and WITH (CTE) queries are allowed for safety. Results are limited to MAX_ROWS.
    A statement timeout of 5 seconds is enforced for PostgreSQL databases.

    Args:
        payload: SQL query request containing the SQL string
        db: Database session (from FastAPI dependency)
        user_id: Current authenticated user ID (from auth dependency)

    Returns:
        SQLQueryResponse with:
        - columns: List of column names
        - rows: List of rows (each row is a list of values)
        - row_count: Number of rows returned

    Raises:
        HTTPException: 401 if authentication is missing or invalid
        HTTPException: 403 if user is not admin
        HTTPException: 400 if query is not SELECT/CTE or execution fails
        HTTPException: 500 if an unexpected error occurs (logged for debugging)
    """
    try:
        # B1: Explicit auth check - fail cleanly when unauthenticated
        if not user_id:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Authentication required",
            )

        # B2: Admin guard - ensure only admins can execute SQL queries
        require_admin(user_id, db)

        # Validate and clean SQL
        sql = _validate_readonly(payload.sql)

        # Set statement timeout for PostgreSQL (5 seconds)
        # This prevents long-running queries from blocking the database
        try:
            db.execute(text("SET LOCAL statement_timeout = '5s'"))
        except Exception:
            # Ignore if not PostgreSQL or if setting fails
            pass

        # Wrap query with LIMIT to enforce MAX_ROWS cap
        # This ensures we never return more than MAX_ROWS even if query doesn't have LIMIT
        wrapped = f"SELECT * FROM ({sql}) AS q LIMIT :limit"

        try:
            result = db.execute(text(wrapped), {"limit": MAX_ROWS})
            rows = result.fetchall()
            columns = list(result.keys())

            # Convert rows to JSON-safe values using FastAPI's encoder
            safe_rows = [[jsonable_encoder(v) for v in row] for row in rows]

            return SQLQueryResponse(
                columns=columns,
                rows=safe_rows,
                row_count=len(safe_rows),
            )
        except Exception as sql_error:
            # SQL execution errors should return 400 (bad request)
            logger.warning(f"SQL execution error: {sql_error}, SQL: {sql[:100]}")
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"SQL error: {str(sql_error)}",
            ) from sql_error

    except HTTPException:
        # Re-raise HTTP exceptions as-is (401, 403, 400)
        raise
    except Exception as e:
        # Log the full exception for debugging
        logger.exception("admin_sql endpoint crashed with unexpected error")
        # Return 500 for unexpected errors (not user-facing SQL errors)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Internal server error",
        ) from e


@router.get("/tables", response_model=list[TableInfo])
def list_tables(
    user_id: str = Depends(get_current_user_id),
    db: Session = Depends(get_db),
) -> list[TableInfo]:
    """List all database tables (admin only).

    Returns a list of all base tables in the database, excluding system schemas.

    Args:
        user_id: Current authenticated user ID (from auth dependency)
        db: Database session (from FastAPI dependency)

    Returns:
        List of table information with schema and name

    Raises:
        HTTPException: 401 if authentication is missing or invalid
        HTTPException: 403 if user is not admin
    """
    try:
        if not user_id:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Authentication required",
            )

        require_admin(user_id, db)

        query = text("""
            SELECT table_schema, table_name
            FROM information_schema.tables
            WHERE table_type = 'BASE TABLE'
              AND table_schema NOT IN ('pg_catalog', 'information_schema')
            ORDER BY table_schema, table_name
        """)
        rows = db.execute(query).all()
        return [TableInfo(schema=r[0], name=r[1]) for r in rows]

    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Failed to list tables")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Internal server error",
        ) from e


@router.get("/tables/{schema}/{table}/columns", response_model=list[ColumnInfo])
def list_columns(
    schema: str,
    table: str,
    user_id: str = Depends(get_current_user_id),
    db: Session = Depends(get_db),
) -> list[ColumnInfo]:
    """List columns for a specific table (admin only).

    Args:
        schema: Table schema name
        table: Table name
        user_id: Current authenticated user ID (from auth dependency)
        db: Database session (from FastAPI dependency)

    Returns:
        List of column information with name and type

    Raises:
        HTTPException: 401 if authentication is missing or invalid
        HTTPException: 403 if user is not admin
        HTTPException: 404 if table not found
    """
    try:
        if not user_id:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Authentication required",
            )

        require_admin(user_id, db)

        query = text("""
            SELECT column_name, data_type
            FROM information_schema.columns
            WHERE table_schema = :schema AND table_name = :table
            ORDER BY ordinal_position
        """)
        rows = db.execute(query, {"schema": schema, "table": table}).all()

        if not rows:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Table {schema}.{table} not found",
            )

        return [ColumnInfo(name=r[0], type=r[1]) for r in rows]

    except HTTPException:
        raise
    except Exception as e:
        logger.exception(f"Failed to list columns for {schema}.{table}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Internal server error",
        ) from e


@router.post("/table-preview", response_model=SQLQueryResponse)
def table_preview(
    req: TablePreviewRequest,
    user_id: str = Depends(get_current_user_id),
    db: Session = Depends(get_db),
) -> SQLQueryResponse:
    """Preview table data safely (admin only).

    Safely previews table data by validating table existence and using quoted identifiers
    to prevent SQL injection. Supports pagination and optional ordering.

    Args:
        req: Table preview request with schema, table, limit, offset, and ordering
        user_id: Current authenticated user ID (from auth dependency)
        db: Database session (from FastAPI dependency)

    Returns:
        SQLQueryResponse with columns, rows, and row count

    Raises:
        HTTPException: 401 if authentication is missing or invalid
        HTTPException: 403 if user is not admin
        HTTPException: 404 if table not found
        HTTPException: 400 if invalid parameters
    """
    try:
        if not user_id:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Authentication required",
            )

        require_admin(user_id, db)

        # Validate and clamp limits
        limit = max(1, min(req.limit, 200))
        offset = max(0, req.offset)

        # Validate table exists by checking information_schema (prevents injection)
        exists = db.execute(
            text("""
                SELECT 1
                FROM information_schema.tables
                WHERE table_schema = :schema AND table_name = :table
            """),
            {"schema": req.schema, "table": req.table},
        ).first()

        if not exists:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Table {req.schema}.{req.table} not found",
            )

        # Build SELECT safely using quoted identifiers (Postgres)
        # Escape double quotes in identifiers
        schema_quoted = req.schema.replace('"', '""')
        table_quoted = req.table.replace('"', '""')

        order_sql = ""
        if req.order_by:
            # Validate column exists
            col_exists = db.execute(
                text("""
                    SELECT 1
                    FROM information_schema.columns
                    WHERE table_schema = :schema
                      AND table_name = :table
                      AND column_name = :col
                """),
                {"schema": req.schema, "table": req.table, "col": req.order_by},
            ).first()

            if col_exists:
                col_quoted = req.order_by.replace('"', '""')
                direction = "ASC" if req.order_dir.lower() == "asc" else "DESC"
                order_sql = f' ORDER BY "{col_quoted}" {direction}'

        # Build safe SQL with quoted identifiers
        sql = text(f'SELECT * FROM "{schema_quoted}"."{table_quoted}"{order_sql} LIMIT :limit OFFSET :offset')

        result = db.execute(sql, {"limit": limit, "offset": offset})
        rows = result.fetchall()
        columns = list(result.keys())

        # Convert rows to JSON-safe values
        safe_rows = [[jsonable_encoder(v) for v in row] for row in rows]

        return SQLQueryResponse(
            columns=columns,
            rows=safe_rows,
            row_count=len(safe_rows),
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.exception(f"Failed to preview table {req.schema}.{req.table}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Internal server error",
        ) from e
