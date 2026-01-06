from app.db.models import Base
from app.db.session import engine


def init_db():
    """Create all database tables."""
    Base.metadata.create_all(bind=engine)
    print("Database tables created successfully.")


if __name__ == "__main__":
    init_db()
