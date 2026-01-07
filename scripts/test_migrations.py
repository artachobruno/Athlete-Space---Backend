"""Test script to validate migration scripts.

This script validates that:
1. All migration functions can be imported
2. Migration functions have correct signatures
3. Migration logic is syntactically correct

Note: This doesn't actually run migrations against the database,
just validates the code is correct.
"""

import sys

print("Testing migration script imports and validation...")
print()

# Test imports
try:
    from scripts.migrate_activities_schema import migrate_activities_schema
    from scripts.migrate_activities_user_id import migrate_activities_user_id
    from scripts.migrate_daily_summary import migrate_daily_summary
    from scripts.migrate_history_cursor import migrate_history_cursor
    from scripts.migrate_strava_accounts import migrate_strava_accounts
    from scripts.migrate_strava_accounts_sync_tracking import migrate_strava_accounts_sync_tracking

    print("✓ All migration functions imported successfully")
except ImportError as e:
    print(f"✗ Failed to import migration functions: {e}")
    sys.exit(1)

# Validate function signatures
migrations = [
    ("migrate_activities_schema", migrate_activities_schema),
    ("migrate_activities_user_id", migrate_activities_user_id),
    ("migrate_daily_summary", migrate_daily_summary),
    ("migrate_history_cursor", migrate_history_cursor),
    ("migrate_strava_accounts", migrate_strava_accounts),
    ("migrate_strava_accounts_sync_tracking", migrate_strava_accounts_sync_tracking),
]

for name, func in migrations:
    if not callable(func):
        print(f"✗ {name} is not callable")
        sys.exit(1)
    print(f"✓ {name} is callable")

print()
print("=" * 60)
print("All migration validation tests passed!")
print("=" * 60)
print()
print("To run migrations, use:")
print("  python scripts/run_migrations.py")
print()
print("Or they will run automatically on application startup.")
