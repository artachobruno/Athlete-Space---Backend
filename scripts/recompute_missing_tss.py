import sys
from pathlib import Path

from sqlalchemy.orm import Session

# Add backend directory to Python path
backend_dir = Path(__file__).parent.parent / "Athlete-Space---Backend"
sys.path.insert(0, str(backend_dir))

from loguru import logger

from app.db.models import Activity
from app.db.session import SessionLocal
from app.metrics.load_computation import compute_activity_tss


def recompute_missing_tss(dry_run: bool = True) -> None:
    session: Session = SessionLocal()

    try:
        activities = (
            session.query(Activity)
            .filter(
                Activity.tss.is_(None),
                Activity.streams_data.isnot(None),
            )
            .order_by(Activity.start_time.desc())
            .all()
        )

        logger.info(f"Found {len(activities)} activities missing TSS")

        updated_count = 0
        error_count = 0

        for activity in activities:
            try:
                logger.info(
                    f"Recomputing TSS for activity={activity.id} "
                    f"start_time={activity.start_time} "
                    f"duration={activity.duration_seconds}s "
                    f"distance={activity.distance_meters}m"
                )

                if not dry_run:
                    new_tss = compute_activity_tss(activity)
                    activity.tss = new_tss
                    updated_count += 1
                    logger.info(f"✅ Activity {activity.id}: TSS = {new_tss:.2f}")
                else:
                    computed_tss = compute_activity_tss(activity)
                    logger.info(f"🟡 Activity {activity.id}: would set TSS = {computed_tss:.2f}")

            except Exception as e:
                error_count += 1
                logger.error(f"❌ Error processing activity {activity.id}: {e}", exc_info=True)

        if not dry_run:
            session.commit()
            logger.info(f"✅ TSS recomputation committed: {updated_count} updated, {error_count} errors")
        else:
            logger.warning(f"🟡 Dry-run mode — no changes written ({len(activities)} activities would be updated)")

    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


if __name__ == "__main__":
    recompute_missing_tss(dry_run=True)   # 🔒 review first
