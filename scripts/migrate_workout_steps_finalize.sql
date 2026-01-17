-- Migration: Finalize workout_steps schema
--
-- This migration addresses the ACTUAL current state:
-- 1. Adds missing semantic columns (instructions, purpose)
-- 2. Consolidates step_order → step_index
-- 3. Removes legacy constraint
--
-- Current state:
--   - step_order (legacy, has unique constraint)
--   - step_index (new, default 0)
--   - step_type (correct)
--   - targets (JSONB, correct)
--   - notes (exists, but code expects instructions/purpose)
--
-- Target state:
--   - step_index (single source of truth)
--   - step_type
--   - targets (JSONB)
--   - instructions
--   - purpose

BEGIN;

-- Step 1: Add missing semantic columns
ALTER TABLE workout_steps
  ADD COLUMN IF NOT EXISTS instructions TEXT,
  ADD COLUMN IF NOT EXISTS purpose TEXT;

-- Step 2: Migrate existing notes to instructions (preserve data)
UPDATE workout_steps
SET instructions = COALESCE(instructions, notes)
WHERE notes IS NOT NULL AND instructions IS NULL;

-- Step 3: Backfill step_index from step_order (consolidate ordering)
UPDATE workout_steps
SET step_index = step_order
WHERE step_index = 0 AND step_order IS NOT NULL;

-- Step 4: Remove legacy unique constraint on step_order
ALTER TABLE workout_steps
DROP CONSTRAINT IF EXISTS workout_steps_workout_id_step_order_key;

COMMIT;

-- Verify migration
-- Run this to check:
-- SELECT id, step_index, step_order, step_type, targets, instructions, purpose, notes 
-- FROM workout_steps LIMIT 5;

-- After verifying everything works, you can later drop step_order:
-- ALTER TABLE workout_steps DROP COLUMN IF EXISTS step_order;
