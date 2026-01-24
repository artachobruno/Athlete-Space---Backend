"""
Auto-Upgrade Vocabulary Level Based on Training Consistency

This module provides logic to automatically upgrade a user's vocabulary level
based on their training consistency and experience.

Rules:
- Foundational → Intermediate: After 4 weeks of consistent training
- Intermediate → Advanced: After 12 weeks of consistent training
- Never downgrades (only upgrades)
- Respects user's explicit choice if set
"""

from datetime import datetime, timedelta, timezone
from typing import Literal

from loguru import logger
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.coach.vocabulary import CoachVocabularyLevel
from app.db.models import Activity, UserSettings


def calculate_training_consistency(
    session: Session,
    user_id: str,
    weeks: int = 12,
) -> dict[str, int | float]:
    """Calculate training consistency metrics for vocabulary upgrade.
    
    Args:
        session: Database session
        user_id: User ID
        weeks: Number of weeks to analyze (default: 12)
        
    Returns:
        Dictionary with:
        - weeks_analyzed: Number of weeks with data
        - weeks_with_training: Number of weeks with at least one activity
        - consistency_percentage: Percentage of weeks with training
        - total_activities: Total activities in period
    """
    cutoff_date = datetime.now(timezone.utc) - timedelta(weeks=weeks)
    
    # Count activities in the period
    activities_query = select(func.count(Activity.id)).where(
        Activity.user_id == user_id,
        Activity.starts_at >= cutoff_date,
    )
    total_activities = session.execute(activities_query).scalar() or 0
    
    # Count distinct weeks with activities
    weeks_with_training_query = select(
        func.count(func.distinct(func.date_trunc("week", Activity.starts_at)))
    ).where(
        Activity.user_id == user_id,
        Activity.starts_at >= cutoff_date,
    )
    weeks_with_training = session.execute(weeks_with_training_query).scalar() or 0
    
    # Calculate consistency percentage
    consistency_percentage = (weeks_with_training / weeks * 100) if weeks > 0 else 0.0
    
    return {
        "weeks_analyzed": weeks,
        "weeks_with_training": weeks_with_training,
        "consistency_percentage": consistency_percentage,
        "total_activities": total_activities,
    }


def should_upgrade_vocabulary_level(
    session: Session,
    user_id: str,
    current_level: CoachVocabularyLevel | None,
) -> tuple[bool, CoachVocabularyLevel | None]:
    """Determine if user should be upgraded to next vocabulary level.
    
    Rules:
    - Foundational → Intermediate: 4+ weeks of consistent training (≥50% consistency)
    - Intermediate → Advanced: 12+ weeks of consistent training (≥70% consistency)
    - Never downgrades
    
    Args:
        session: Database session
        user_id: User ID
        current_level: Current vocabulary level (None = intermediate default)
        
    Returns:
        Tuple of (should_upgrade: bool, new_level: CoachVocabularyLevel | None)
    """
    if current_level is None:
        current_level = "intermediate"
    
    # Already at highest level
    if current_level == "advanced":
        return (False, None)
    
    # Check consistency for upgrade
    if current_level == "foundational":
        # Need 4 weeks of consistent training (≥50% consistency)
        metrics = calculate_training_consistency(session, user_id, weeks=4)
        if metrics["weeks_with_training"] >= 4 and metrics["consistency_percentage"] >= 50.0:
            logger.info(
                "Vocabulary upgrade: foundational → intermediate",
                user_id=user_id,
                weeks_with_training=metrics["weeks_with_training"],
                consistency=metrics["consistency_percentage"],
            )
            return (True, "intermediate")
    
    elif current_level == "intermediate":
        # Need 12 weeks of consistent training (≥70% consistency)
        metrics = calculate_training_consistency(session, user_id, weeks=12)
        if metrics["weeks_with_training"] >= 12 and metrics["consistency_percentage"] >= 70.0:
            logger.info(
                "Vocabulary upgrade: intermediate → advanced",
                user_id=user_id,
                weeks_with_training=metrics["weeks_with_training"],
                consistency=metrics["consistency_percentage"],
            )
            return (True, "advanced")
    
    return (False, None)


def auto_upgrade_vocabulary_level(
    session: Session,
    user_id: str,
    settings: UserSettings | None = None,
) -> bool:
    """Auto-upgrade user's vocabulary level if they meet criteria.
    
    This function:
    1. Gets current vocabulary level from settings
    2. Checks if user meets upgrade criteria
    3. Updates settings if upgrade is warranted
    4. Returns True if upgrade occurred, False otherwise
    
    Args:
        session: Database session
        user_id: User ID
        settings: UserSettings object (will be fetched if None)
        
    Returns:
        True if upgrade occurred, False otherwise
    """
    # Fetch settings if not provided
    if settings is None:
        from app.db.models import UserSettings
        settings = session.query(UserSettings).filter_by(user_id=user_id).first()
    
    if not settings:
        # No settings found - create default
        from app.db.models import UserSettings
        settings = UserSettings(user_id=user_id, preferences={})
        session.add(settings)
        session.flush()
    
    # Get current level
    current_level = settings.vocabulary_level
    
    # Check if upgrade is warranted
    should_upgrade, new_level = should_upgrade_vocabulary_level(
        session,
        user_id,
        current_level,
    )
    
    if should_upgrade and new_level:
        # Update vocabulary level
        settings.vocabulary_level = new_level
        session.commit()
        
        logger.info(
            "Vocabulary level auto-upgraded",
            user_id=user_id,
            old_level=current_level,
            new_level=new_level,
        )
        
        return True
    
    return False
