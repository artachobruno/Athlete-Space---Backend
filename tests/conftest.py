"""Root conftest for all tests.

This file makes shared fixtures available across all test modules.
"""

import os
from contextlib import contextmanager, suppress

import pytest
from loguru import logger
from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.orm import sessionmaker

# Make MCP fixtures available to all tests (including CLI tests)
# NOTE: Commented out because tests.mcp is not a proper Python package (missing __init__.py)
# Uncomment and ensure tests/__init__.py and tests/mcp/__init__.py exist if MCP fixtures are needed
# pytest_plugins = ["tests.mcp.conftest"]


# Enable foreign key constraints for SQLite
@event.listens_for(Engine, "connect")
def _set_sqlite_pragma(dbapi_connection, connection_record):
    """Enable foreign key constraints in SQLite connections."""
    try:
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()
    except Exception:
        pass


@pytest.fixture(scope="session", autouse=True)
def ensure_models_imported():
    """Ensure all models are imported so SQLAlchemy metadata is complete.

    This autouse fixture runs once per test session and ensures that all models
    (including Workout) are imported and registered in Base.metadata before any
    tests run. This is critical for ForeignKey resolution.
    """
    # Import modules to register all models in Base.metadata
    import app.db.models  # Registers all models in app.db.models
    import app.workouts.models  # Registers Workout model (needed for PlannedSession.workout_id FK)

    # Verify Workout is registered
    from app.db.models import Base
    assert "workouts" in Base.metadata.tables, "Workout model not registered in Base.metadata"

    yield


@pytest.fixture(scope="session", autouse=True)
def initialize_template_library_for_tests():
    """Initialize template library for planner tests.

    This is required for any test that uses the planner (plan_race, etc.).
    Initializes from cache once per test session.
    """
    try:
        from app.domains.training_plan.template_loader import initialize_template_library_from_cache
        initialize_template_library_from_cache()
        logger.info("Template library initialized for tests")
    except Exception as e:
        # Log warning but don't fail - some tests may not need planner
        logger.warning(f"Template library initialization failed (may be expected for some tests): {e}")

    yield


@pytest.fixture(scope="session", autouse=True)
def create_sqlite_schema(ensure_models_imported):
    """Create database schema when using SQLite.

    When DATABASE_URL points to SQLite (not in-memory), create all tables
    once per test session. This ensures tables exist before tests run DELETE
    statements.

    Depends on ensure_models_imported to ensure Base.metadata is populated.
    """
    # Check both environment variable and settings
    db_url_env = os.getenv("DATABASE_URL", "").lower()
    if "sqlite" in db_url_env and ":memory:" not in db_url_env:
        from app.db.models import Base
        from app.db.session import _get_engine

        engine = _get_engine()
        # Verify this is actually SQLite
        engine_url = str(engine.url).lower()
        if "sqlite" in engine_url and ":memory:" not in engine_url:
            Base.metadata.create_all(bind=engine)

    yield

    # Cleanup could go here if needed, but typically we leave tables for reuse


@pytest.fixture
def test_athlete_id() -> int:
    """Test athlete ID fixture.

    Returns a stable athlete ID for use in tests.
    Matches the pattern used across plan tools (athlete_id=1).
    """
    return 1


@pytest.fixture(scope="function")
def db_session(monkeypatch):
    """
    Provides a transactional in-memory SQLite DB session for tests.

    This fixture:
    - Creates an isolated in-memory SQLite database per test
    - Patches the engine creation to use SQLite
    - Patches get_session() to return the test session
    - Uses transaction rollback for fast, lock-free cleanup (no DELETE statements)

    Usage:
        def test_something(db_session):
            session = PlannedSession(...)
            db_session.add(session)
            db_session.commit()
    """
    # Create in-memory SQLite engine
    engine = create_engine(
        "sqlite:///:memory:",
        echo=False,
        connect_args={"check_same_thread": False},
    )

    # Patch JSONB to JSON for SQLite compatibility
    from sqlalchemy.dialects.postgresql import JSONB
    from sqlalchemy.dialects.sqlite.base import SQLiteTypeCompiler

    # Add visit_JSONB method to SQLiteTypeCompiler if it doesn't exist
    if not hasattr(SQLiteTypeCompiler, "visit_JSONB"):
        def visit_jsonb(self, type_, **kw):
            return "JSON"
        SQLiteTypeCompiler.visit_JSONB = visit_jsonb

    # CRITICAL: Reset global engine and session factory to force recreation
    import app.db.session as session_module

    # Reset globals to force recreation with our test engine
    # This must happen BEFORE any code that might have cached the engine
    session_module._engine = None
    session_module._SessionLocal = None

    # Force close any existing engine connections to prevent reuse
    if hasattr(session_module, "_engine") and session_module._engine is not None:
        with suppress(Exception):
            session_module._engine.dispose()
        session_module._engine = None

    # Patch the engine getter to return our test engine
    def mock_get_engine():
        return engine

    monkeypatch.setattr(session_module, "_get_engine", mock_get_engine)
    monkeypatch.setattr(session_module, "get_engine", mock_get_engine)

    # Also patch _get_session_local to use our test engine
    def mock_get_session_local():
        return sessionmaker(autocommit=False, autoflush=False, bind=engine)

    monkeypatch.setattr(session_module, "_get_session_local", mock_get_session_local)

    # Import models after patching engine
    # CRITICAL: Import models so SQLAlchemy metadata is complete
    # This allows ForeignKey resolution and ensures all required fields are present
    # Import modules to ensure all models register in Base.metadata before table creation
    import app.db.models  # Registers all models in app.db.models (PlannedSession, AthleteProfile, etc.)
    import app.workouts.models  # Registers Workout model (referenced by PlannedSession.workout_id FK)
    from app.db.models import AthleteProfile, Base, PlannedSession
    from app.workouts.models import Workout

    # Ensure metadata is populated - Workout table must be registered before FK resolution
    assert "workouts" in Base.metadata.tables, "Workout model not registered in Base.metadata"

    # Create all tables once (in-memory DB is fast)
    Base.metadata.create_all(engine)

    # Create a connection and start a transaction
    connection = engine.connect()
    transaction = connection.begin()

    # Create session factory bound to our test connection
    test_session_local = sessionmaker(bind=connection, autocommit=False, autoflush=False)
    session = test_session_local()

    # Patch get_session to return our test session
    # We need to patch it in multiple places where it might be imported
    @contextmanager
    def mock_get_session():
        yield session

    # Patch at the module level (where it's defined)
    monkeypatch.setattr(session_module, "get_session", mock_get_session)

    # CRITICAL: Patch get_session in all modules that import it directly
    # This ensures that even if they imported it at module level, they get our mock
    modules_to_patch = [
        "app.coach.tools.plan_week",
        "app.coach.tools.plan_season",
        "app.coach.tools.session_planner",
        "app.coach.tools.modify_week",
        "app.coach.tools.modify_day",
        "app.coach.tools.modify_season",
        "app.coach.tools.modify_race",
        "app.coach.tools.unified_plan",
        "app.tools.read.plans",
        "app.tools.semantic.evaluate_plan_change",
        "app.tools.adapters.db_adapter",
        "app.tools.guards",
        "app.tools.read.audit",
        "app.tools.read.feedback",
        "app.tools.write.feedback",
        "app.tools.read.profile",
        "app.tools.read.calendar",
        "app.tools.read.activities",
        "app.tools.read.metrics",
        "app.calendar.training_summary",
        "app.plans.regenerate.regeneration_service",
        "app.plans.modify.repository",
    ]

    for module_path in modules_to_patch:
        try:
            module = __import__(module_path, fromlist=[""])
            # Patch both get_session and _get_session (aliased imports)
            if hasattr(module, "get_session"):
                monkeypatch.setattr(module, "get_session", mock_get_session)
            if hasattr(module, "_get_session"):
                monkeypatch.setattr(module, "_get_session", mock_get_session)
        except (ImportError, AttributeError):
            # Module doesn't exist or doesn't have get_session - that's OK
            pass

    try:
        yield session
    finally:
        # Rollback transaction (fast, no locks, no DELETE statements needed)
        # Check if transaction is still active before rolling back
        session.rollback()
        if transaction.is_active:
            transaction.rollback()
        session.close()
        connection.close()
