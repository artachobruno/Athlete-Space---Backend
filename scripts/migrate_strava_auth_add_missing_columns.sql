-- Migration: Add missing columns to strava_auth table
-- This fixes the ORM ↔ DB schema mismatch that causes stream fetch failures
-- 
-- The SQLAlchemy model expects these columns but they don't exist in Postgres:
-- - backfill_page
-- - backfill_done
-- - last_successful_sync_at
-- - backfill_updated_at
-- - last_error
-- - last_error_at

BEGIN;

ALTER TABLE strava_auth
  ADD COLUMN IF NOT EXISTS backfill_page INTEGER,
  ADD COLUMN IF NOT EXISTS backfill_done BOOLEAN DEFAULT FALSE,
  ADD COLUMN IF NOT EXISTS last_successful_sync_at TIMESTAMPTZ,
  ADD COLUMN IF NOT EXISTS backfill_updated_at TIMESTAMPTZ,
  ADD COLUMN IF NOT EXISTS last_error TEXT,
  ADD COLUMN IF NOT EXISTS last_error_at TIMESTAMPTZ;

COMMIT;
