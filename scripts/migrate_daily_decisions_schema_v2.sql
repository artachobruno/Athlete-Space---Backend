-- Migration: Update daily_decisions table to match SQLAlchemy model
-- This fixes the schema mismatch that causes /intelligence/today to fail
--
-- Changes:
-- 1. Rename 'day' to 'decision_date' and change type to TIMESTAMPTZ
-- 2. Rename 'decision' to 'decision_data'
-- 3. Add metadata fields: recommendation_type, recommended_intensity, has_workout
-- 4. Add versioning fields: version, is_active
-- 5. Add relationship: weekly_intent_id
-- 6. Add updated_at timestamp
-- 7. Update constraints and indexes

BEGIN;

-- Step 1: Add new columns (if they don't exist)
ALTER TABLE daily_decisions
  ADD COLUMN IF NOT EXISTS recommendation_type VARCHAR,
  ADD COLUMN IF NOT EXISTS recommended_intensity VARCHAR,
  ADD COLUMN IF NOT EXISTS has_workout BOOLEAN,
  ADD COLUMN IF NOT EXISTS version INTEGER DEFAULT 1 NOT NULL,
  ADD COLUMN IF NOT EXISTS is_active BOOLEAN DEFAULT TRUE NOT NULL,
  ADD COLUMN IF NOT EXISTS weekly_intent_id VARCHAR,
  ADD COLUMN IF NOT EXISTS updated_at TIMESTAMPTZ DEFAULT now() NOT NULL,
  ADD COLUMN IF NOT EXISTS decision_date TIMESTAMPTZ,
  ADD COLUMN IF NOT EXISTS decision_data JSONB DEFAULT '{}'::jsonb NOT NULL;

-- Step 2: Migrate data from old columns to new columns (if old columns exist)
DO $$
BEGIN
  -- Migrate 'day' to 'decision_date' (convert DATE to TIMESTAMPTZ at midnight UTC)
  IF EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name = 'daily_decisions' AND column_name = 'day') THEN
    UPDATE daily_decisions
    SET decision_date = (day AT TIME ZONE 'UTC')::timestamptz
    WHERE decision_date IS NULL AND day IS NOT NULL;
  END IF;

  -- Migrate 'decision' to 'decision_data'
  IF EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name = 'daily_decisions' AND column_name = 'decision') THEN
    UPDATE daily_decisions
    SET decision_data = decision
    WHERE decision_data = '{}'::jsonb AND decision IS NOT NULL AND decision != '{}'::jsonb;
  END IF;
END $$;

-- Step 3: Set NOT NULL constraints after data migration
ALTER TABLE daily_decisions
  ALTER COLUMN decision_date SET NOT NULL,
  ALTER COLUMN decision_data SET NOT NULL;

-- Step 4: Drop old columns if they exist (after migration)
ALTER TABLE daily_decisions
  DROP COLUMN IF EXISTS day,
  DROP COLUMN IF EXISTS decision;

-- Step 5: Update primary key if needed (id should already be VARCHAR/UUID)
-- Note: If id is UUID, we may need to change it to VARCHAR, but let's check first
-- This is handled by the model's default, so we'll leave it as-is

-- Step 6: Update user_id type if needed (should be VARCHAR, not UUID)
-- Check if user_id is UUID and needs to be changed to VARCHAR
DO $$
BEGIN
  IF EXISTS (
    SELECT 1 FROM information_schema.columns 
    WHERE table_name = 'daily_decisions' 
    AND column_name = 'user_id' 
    AND data_type = 'uuid'
  ) THEN
    -- Convert UUID to VARCHAR
    ALTER TABLE daily_decisions ALTER COLUMN user_id TYPE VARCHAR USING user_id::text;
  END IF;
END $$;

-- Step 7: Drop old unique constraint if it exists
ALTER TABLE daily_decisions
  DROP CONSTRAINT IF EXISTS daily_decisions_user_id_day_key;

-- Step 8: Create new unique constraint
ALTER TABLE daily_decisions
  DROP CONSTRAINT IF EXISTS uq_daily_decision_user_date_version,
  ADD CONSTRAINT uq_daily_decision_user_date_version 
    UNIQUE (user_id, decision_date, version);

-- Step 9: Create indexes
CREATE INDEX IF NOT EXISTS idx_daily_decision_user_id ON daily_decisions(user_id);
CREATE INDEX IF NOT EXISTS idx_daily_decision_decision_date ON daily_decisions(decision_date);
CREATE INDEX IF NOT EXISTS idx_daily_decision_weekly_intent_id ON daily_decisions(weekly_intent_id);
CREATE INDEX IF NOT EXISTS idx_daily_decision_user_date_active 
  ON daily_decisions(user_id, decision_date) 
  WHERE is_active IS TRUE;

COMMIT;
