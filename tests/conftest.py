"""Root conftest for all tests.

This file makes shared fixtures available across all test modules.
"""

from contextlib import contextmanager

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

# Make MCP fixtures available to all tests (including CLI tests)
pytest_plugins = ["tests.mcp.conftest"]


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
    - Rolls back and closes after each test

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

    # Patch the engine getter to return our test engine
    def mock_get_engine():
        return engine

    monkeypatch.setattr("app.db.session._get_engine", mock_get_engine)
    monkeypatch.setattr("app.db.session.get_engine", mock_get_engine)

    # Import models after patching engine
    # Create all tables (SQLite will handle missing FK targets gracefully with proper setup)
    # We need to create workouts table first since PlannedSession references it
    from sqlalchemy import Column, String, Table

    from app.db.models import Base, PlannedSession

    # Use extend_existing to avoid redefinition errors across tests
    if "workouts" not in Base.metadata.tables:
        workouts_table = Table(
            "workouts",
            Base.metadata,
            Column("id", String, primary_key=True),
        )
    else:
        workouts_table = Base.metadata.tables["workouts"]

    # Create workouts table first
    workouts_table.create(engine, checkfirst=True)

    # Create athlete_profiles table (needed by some tests)
    from sqlalchemy import Integer
    if "athlete_profiles" not in Base.metadata.tables:
        athlete_profiles_table = Table(
            "athlete_profiles",
            Base.metadata,
            Column("user_id", String, primary_key=True),
            Column("athlete_id", Integer, nullable=False),
        )
    else:
        athlete_profiles_table = Base.metadata.tables["athlete_profiles"]
    athlete_profiles_table.create(engine, checkfirst=True)

    # Now create PlannedSession table (it references workouts)
    PlannedSession.__table__.create(engine, checkfirst=True)

    # Create session factory bound to our test engine
    test_session_local = sessionmaker(bind=engine, autocommit=False, autoflush=False)
    session = test_session_local()

    # Patch get_session to return our test session
    # We need to patch it in multiple places where it might be imported
    @contextmanager
    def mock_get_session():
        yield session

    # Patch at the module level
    import app.db.session as session_module
    monkeypatch.setattr(session_module, "get_session", mock_get_session)

    # Also patch in repository module if it imports get_session directly
    try:
        import app.plans.modify.repository as repo_module
        monkeypatch.setattr(repo_module, "get_session", mock_get_session)
    except ImportError:
        pass

    try:
        yield session
    finally:
        session.rollback()
        session.close()
        PlannedSession.__table__.drop(engine, checkfirst=True)
        workouts_table.drop(engine, checkfirst=True)
