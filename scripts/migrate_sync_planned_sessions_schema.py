from app.db.engine import engine
from app.db.migrations.utils import add_column_if_missing


def migrate():
    with engine.begin() as conn:
        add_column_if_missing(conn, "planned_sessions", "philosophy_id", "UUID")
        add_column_if_missing(conn, "planned_sessions", "template_id", "UUID")
        add_column_if_missing(conn, "planned_sessions", "session_type", "VARCHAR")
        add_column_if_missing(conn, "planned_sessions", "distance_mi", "FLOAT")
        add_column_if_missing(conn, "planned_sessions", "tags", "JSONB")
