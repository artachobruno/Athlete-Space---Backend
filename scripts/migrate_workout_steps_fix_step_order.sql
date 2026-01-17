-- Migration: Fix step_order NOT NULL constraint
--
-- The step_order column still has a NOT NULL constraint, but the code
-- no longer sets it (we're using step_index instead).
--
-- Options:
-- 1. Make step_order nullable (safer, allows gradual migration)
-- 2. Set default value from step_index (temporary bridge)
-- 3. Drop step_order entirely (cleanest, but more risky)
--
-- We'll do option 1 (make nullable) for safety, then you can drop it later.

BEGIN;

-- Option 1: Make step_order nullable
ALTER TABLE workout_steps
ALTER COLUMN step_order DROP NOT NULL;

-- Option 2 (alternative): Set default to copy from step_index
-- This would require a trigger or default function, which is more complex.
-- For now, nullable is simpler.

COMMIT;

-- After this runs successfully and you've verified everything works,
-- you can drop the column entirely:
-- ALTER TABLE workout_steps DROP COLUMN IF EXISTS step_order;
