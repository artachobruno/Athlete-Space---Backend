import sys
from pathlib import Path

# Add project root to Python path
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from app.db.models import Base
from app.db.session import get_engine


def init_db():
    """Create all database tables."""
    Base.metadata.create_all(bind=get_engine())
    print("Database tables created successfully.")


if __name__ == "__main__":
    init_db()
