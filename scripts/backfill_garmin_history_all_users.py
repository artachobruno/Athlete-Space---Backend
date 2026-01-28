#!/usr/bin/env python3
"""Trigger Garmin summary backfill for ALL users with Garmin integrations.

Uses event-driven backfill (trigger only). Data arrives via webhooks.
No pull. See app/integrations/garmin/README.md.

Usage:
    python scripts/backfill_garmin_history_all_users.py [--force]

Examples:
    python scripts/backfill_garmin_history_all_users.py
    python scripts/backfill_garmin_history_all_users.py --force
"""

import sys
from pathlib import Path

project_root = Path(__file__).parent.parent
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))

from loguru import logger
from sqlalchemy import select

from app.db.models import UserIntegration
from app.db.session import get_session
from app.integrations.garmin.backfill import backfill_garmin_activities


def main() -> int:
    """Trigger summary backfill for all users with Garmin."""
    force = "--force" in sys.argv or "-f" in sys.argv

    print("=" * 70)
    print("GARMIN SUMMARY BACKFILL - ALL USERS (event-driven)")
    print("=" * 70)
    print("Triggers backfill only. Data arrives via webhooks.")
    print(f"Force: {force}")
    print("=" * 70)

    try:
        with get_session() as session:
            integrations = session.execute(
                select(UserIntegration).where(
                    UserIntegration.provider == "garmin",
                    UserIntegration.revoked_at.is_(None),
                )
            ).all()

            if not integrations:
                print("\nNo active Garmin integrations found")
                return 1

            user_ids = [i[0].user_id for i in integrations]
            print(f"\nFound {len(user_ids)} user(s) with Garmin integrations\n")
    except Exception as e:
        print(f"\nERROR finding users: {e}")
        logger.exception("Failed to find Garmin integrations")
        return 1

    results = []
    for idx, user_id in enumerate(user_ids, 1):
        print(f"[{idx}/{len(user_ids)}] user_id={user_id} ...", end=" ", flush=True)
        try:
            r = backfill_garmin_activities(user_id=user_id, force=force)
            status = r.get("status", "?")
            accepted = r.get("accepted_count", 0)
            duplicates = r.get("duplicate_count", 0)
            errors = r.get("error_count", 0)
            print(f"status={status} accepted={accepted} duplicates={duplicates} errors={errors}")
            results.append({"user_id": user_id, "status": status, "result": r})
        except Exception as e:
            print(f"FAILED: {e}")
            logger.exception("Backfill failed for user_id=%s", user_id)
            results.append({"user_id": user_id, "status": "error", "error": str(e)})

    print("\n" + "=" * 70)
    print("SUMMARY")
    print("=" * 70)
    ok = sum(1 for r in results if r["status"] in ("completed", "skipped_recent_request"))
    err = sum(1 for r in results if r["status"] == "error")
    print(f"Triggered: {len(results)} | OK: {ok} | Errors: {err}")
    print("=" * 70)
    return 1 if err > 0 else 0


if __name__ == "__main__":
    sys.exit(main())
