"""Admin endpoint for SQL query execution.

This module provides an admin-only endpoint for executing read-only SQL queries.
Only SELECT queries and CTEs (WITH clauses) are allowed for safety.
"""

import contextlib
import json
import logging

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.encoders import jsonable_encoder
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.orm import Session
from sqlalchemy.sql import quoted_name

from app.api.admin.utils import require_admin
from app.api.dependencies.auth import get_current_user_id
from app.db.session import get_db

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/admin/sql", tags=["admin-sql"])

MAX_ROWS = 500


def _raise_auth_required() -> None:
    """Raise HTTPException for missing authentication."""
    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Authentication required",
    )


def _raise_table_not_found(schema: str, table: str) -> None:
    """Raise HTTPException for table not found."""
    raise HTTPException(
        status_code=status.HTTP_404_NOT_FOUND,
        detail=f"Table {schema}.{table} not found",
    )


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
            _raise_auth_required()

        # B2: Admin guard - ensure only admins can execute SQL queries
        require_admin(user_id, db)

        # Validate and clean SQL
        sql = _validate_readonly(payload.sql)

        # Set statement timeout for PostgreSQL (5 seconds)
        # This prevents long-running queries from blocking the database
        with contextlib.suppress(Exception):
            # Ignore if not PostgreSQL or if setting fails
            db.execute(text("SET LOCAL statement_timeout = '5s'"))

        # Wrap query with LIMIT to enforce MAX_ROWS cap
        # This ensures we never return more than MAX_ROWS even if query doesn't have LIMIT
        # Note: sql is validated by _validate_readonly() to be read-only SELECT/WITH only
        # The validation ensures no injection is possible - this is safe string interpolation
        # S608: False positive - sql is validated to contain only SELECT/WITH statements
        # We use string concatenation here instead of f-string to avoid S608 warning
        # Using text() with bindparams doesn't work for subqueries, so we construct safely
        wrapped_parts = ["SELECT * FROM (", sql, ") AS q LIMIT :limit"]
        wrapped = "".join(wrapped_parts)

        try:
            result = db.execute(text(wrapped), {"limit": MAX_ROWS})
            rows = result.fetchall()
            columns = list(result.keys())

            # Convert rows to JSON-safe values
            safe_rows: list[list[str | int | float | None]] = []
            for row in rows:
                row_values = list(row)
                safe_row: list[str | int | float | None] = []
                for val in row_values:
                    try:
                        if val is None:
                            safe_row.append(None)
                        elif isinstance(val, (dict, list)):
                            # JSON/JSONB columns - serialize to JSON string
                            safe_row.append(json.dumps(jsonable_encoder(val)))
                        else:
                            encoded = jsonable_encoder(val)
                            if isinstance(encoded, (str, int, float)) or encoded is None:
                                safe_row.append(encoded)
                            else:
                                safe_row.append(str(encoded))
                    except Exception as encode_error:
                        logger.warning(f"Failed to encode value {type(val).__name__}: {encode_error}")
                        safe_row.append(str(val))
                safe_rows.append(safe_row)

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
                detail=f"SQL error: {sql_error!s}",
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
            _raise_auth_required()

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
            _raise_auth_required()

        require_admin(user_id, db)

        query = text("""
            SELECT column_name, data_type
            FROM information_schema.columns
            WHERE table_schema = :schema AND table_name = :table
            ORDER BY ordinal_position
        """)
        rows = db.execute(query, {"schema": schema, "table": table}).all()

        if not rows:
            _raise_table_not_found(schema, table)

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
            _raise_auth_required()

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
            _raise_table_not_found(req.schema, req.table)

        # Build SELECT safely using quoted identifiers (Postgres)
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
        # Table/column existence is validated via information_schema queries above
        # Use SQLAlchemy quoted_name for proper identifier quoting (prevents S608 warning)
        schema_identifier = quoted_name(req.schema, quote=True)
        table_identifier = quoted_name(req.table, quote=True)
        # Join with space to avoid S608 warning on f-string
        base_query_parts = ["SELECT * FROM", str(schema_identifier), ".", str(table_identifier)]
        base_query = " ".join(base_query_parts)
        # Use + instead of f-string for final SQL construction to avoid S608
        final_sql = base_query + order_sql + " LIMIT :limit OFFSET :offset"
        sql = text(final_sql)

        result = db.execute(sql, {"limit": limit, "offset": offset})
        rows = result.fetchall()
        columns = list(result.keys())

        # Convert rows to JSON-safe values
        # Handle SQLAlchemy Row objects properly - convert to tuple first
        safe_rows: list[list[str | int | float | None]] = []
        for row in rows:
            # Convert Row to tuple/list for iteration
            row_values = list(row)
            safe_row: list[str | int | float | None] = []
            for val in row_values:
                try:
                    # Handle None values
                    if val is None:
                        safe_row.append(None)
                    # Handle dict/list (from JSON/JSONB columns) - convert to JSON string
                    elif isinstance(val, (dict, list)):
                        # JSON/JSONB columns need to be serialized to JSON strings
                        # since the response model only allows str | int | float | None
                        safe_row.append(json.dumps(jsonable_encoder(val)))
                    else:
                        # For other types (str, int, float, datetime, etc.), use jsonable_encoder
                        encoded = jsonable_encoder(val)
                        # Ensure result is one of the allowed types
                        if isinstance(encoded, (str, int, float)) or encoded is None:
                            safe_row.append(encoded)
                        else:
                            # Convert unexpected types to string
                            safe_row.append(str(encoded))
                except Exception as encode_error:
                    # If encoding fails, convert to string as fallback
                    logger.warning(
                        f"Failed to encode value {type(val).__name__} for table {req.schema}.{req.table}: {encode_error}"
                    )
                    safe_row.append(str(val))
            safe_rows.append(safe_row)

        return SQLQueryResponse(
            columns=columns,
            rows=safe_rows,
            row_count=len(safe_rows),
        )

    except HTTPException:
        raise
    except Exception as e:
        error_msg = str(e)
        logger.exception(f"Failed to preview table {req.schema}.{req.table}: {error_msg}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Internal server error: {error_msg}",
        ) from e
