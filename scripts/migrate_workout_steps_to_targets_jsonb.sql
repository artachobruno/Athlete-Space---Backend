-- Migration: Convert workout_steps to use targets JSONB
--
-- This migration:
-- 1. Adds targets JSONB column (if not exists)
-- 2. Migrates data from individual columns to targets JSONB
-- 3. Drops old columns (optional, can be done later for safety)
--
-- IMPORTANT: Test this on a backup first!

BEGIN;

-- Step 1: Add targets column if it doesn't exist
ALTER TABLE workout_steps
ADD COLUMN IF NOT EXISTS targets JSONB NOT NULL DEFAULT '{}'::jsonb;

-- Step 2: Migrate existing data to targets JSONB
-- This converts duration_seconds/distance_meters and target_* columns to JSONB
UPDATE workout_steps
SET targets = (
    SELECT jsonb_build_object(
        'duration', CASE
            WHEN duration_seconds IS NOT NULL THEN
                jsonb_build_object('type', 'time', 'seconds', duration_seconds)
            WHEN distance_meters IS NOT NULL THEN
                jsonb_build_object('type', 'distance', 'meters', distance_meters)
            ELSE NULL
        END,
        'target', CASE
            WHEN target_metric IS NOT NULL AND target_min IS NOT NULL AND target_max IS NOT NULL THEN
                jsonb_build_object('metric', target_metric, 'min', target_min, 'max', target_max)
            WHEN target_metric IS NOT NULL AND target_value IS NOT NULL THEN
                jsonb_build_object('metric', target_metric, 'value', target_value)
            ELSE NULL
        END
    )
)
WHERE targets = '{}'::jsonb;

-- Step 3: Rename 'type' to 'step_type' if needed
-- (Check if step_type column exists first)
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_name = 'workout_steps' AND column_name = 'step_type'
    ) THEN
        ALTER TABLE workout_steps RENAME COLUMN type TO step_type;
    END IF;
END $$;

-- Step 4: (OPTIONAL - Comment out for safety, run later)
-- Drop old columns after verifying migration worked
-- ALTER TABLE workout_steps DROP COLUMN IF EXISTS duration_seconds;
-- ALTER TABLE workout_steps DROP COLUMN IF EXISTS distance_meters;
-- ALTER TABLE workout_steps DROP COLUMN IF EXISTS target_metric;
-- ALTER TABLE workout_steps DROP COLUMN IF EXISTS target_min;
-- ALTER TABLE workout_steps DROP COLUMN IF EXISTS target_max;
-- ALTER TABLE workout_steps DROP COLUMN IF EXISTS target_value;
-- ALTER TABLE workout_steps DROP COLUMN IF EXISTS intensity_zone;
-- ALTER TABLE workout_steps DROP COLUMN IF EXISTS inferred;

COMMIT;

-- Verify migration
-- Run this to check a few rows:
-- SELECT id, step_index, step_type, targets, instructions, purpose FROM workout_steps LIMIT 5;
