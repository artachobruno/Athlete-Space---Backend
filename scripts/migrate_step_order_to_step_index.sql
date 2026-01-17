-- Migration: Replace step_order with step_index in workout_steps table
-- 
-- This migration:
-- 1. Migrates data from step_order to step_index (if step_index is still 0/default)
-- 2. Drops the old step_order column
-- 3. Drops the unique constraint on (workout_id, step_order)

BEGIN;

-- Step 1: Migrate data from step_order to step_index where step_index is still default (0)
-- This handles existing rows that were created before step_index was added
UPDATE workout_steps
SET step_index = step_order
WHERE step_index = 0 AND step_order IS NOT NULL;

-- Step 2: Drop the unique constraint on (workout_id, step_order)
ALTER TABLE workout_steps
DROP CONSTRAINT IF EXISTS workout_steps_workout_id_step_order_key;

-- Step 3: Drop the old step_order column
ALTER TABLE workout_steps
DROP COLUMN IF EXISTS step_order;

COMMIT;

-- Verify the migration
-- After running, you should see:
-- - step_index column exists
-- - step_order column does NOT exist
-- - No constraint on step_order
