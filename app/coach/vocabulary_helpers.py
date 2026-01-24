"""Helper functions for getting vocabulary level from user settings."""

from app.coach.vocabulary import CoachVocabularyLevel
from app.coach.vocabulary_examples import get_user_vocabulary_level
from app.db.models import UserSettings
from app.db.session import get_session


def get_vocabulary_level_for_user(user_id: str | None) -> CoachVocabularyLevel:
    """Get vocabulary level for a user, defaulting to intermediate.
    
    Args:
        user_id: User ID (None returns 'intermediate')
        
    Returns:
        Coach vocabulary level
    """
    if not user_id:
        return "intermediate"
    
    try:
        with get_session() as session:
            settings = session.query(UserSettings).filter_by(user_id=user_id).first()
            return get_user_vocabulary_level(settings)
    except Exception:
        # On any error, default to intermediate
        return "intermediate"
