import sqlite3

from app.core.settings import settings

DB_PATH = settings.database_url.replace("sqlite:///", "")

print(f"Using database at: {DB_PATH}")

COLUMNS = {
    "last_ingested_at": "INTEGER",
    "backfill_page": "INTEGER",
    "backfill_done": "BOOLEAN DEFAULT 0",
    "last_successful_sync_at": "INTEGER",
    "backfill_updated_at": "INTEGER",
    "last_error": "TEXT",
    "last_error_at": "INTEGER",
}

conn = sqlite3.connect(DB_PATH)
cur = conn.cursor()

cur.execute("PRAGMA table_info(strava_auth)")
existing = {row[1] for row in cur.fetchall()}

for col, col_type in COLUMNS.items():
    if col not in existing:
        print(f"Adding column {col}")
        cur.execute(f"ALTER TABLE strava_auth ADD COLUMN {col} {col_type}")

conn.commit()
conn.close()

print("StravaAuth schema migration complete")
