-- Migration: Convert workout_steps to use targets JSONB
--
-- ⚠️ DEPRECATED: This migration assumes an old schema that doesn't exist.
-- 
-- If your schema already has:
--   - step_type (not 'type')
--   - targets (JSONB)
--   - step_index
--
-- Then DO NOT run this script. Instead, run:
--   scripts/migrate_workout_steps_finalize.sql
--
-- This script is kept for reference only.
--
-- IMPORTANT: Test this on a backup first!

BEGIN;

-- Step 1: Add targets column if it doesn't exist
ALTER TABLE workout_steps
ADD COLUMN IF NOT EXISTS targets JSONB NOT NULL DEFAULT '{}'::jsonb;

-- Step 2: Migrate existing data to targets JSONB (only if old columns exist)
-- This step is skipped if old columns don't exist (schema already migrated or different)
DO $$
DECLARE
    has_duration_seconds BOOLEAN;
    has_distance_meters BOOLEAN;
    has_target_metric BOOLEAN;
    has_target_min BOOLEAN;
    has_target_max BOOLEAN;
    has_target_value BOOLEAN;
    sql_text TEXT;
BEGIN
    -- Check which columns exist
    SELECT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_name = 'workout_steps' AND column_name = 'duration_seconds'
    ) INTO has_duration_seconds;
    
    SELECT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_name = 'workout_steps' AND column_name = 'distance_meters'
    ) INTO has_distance_meters;
    
    SELECT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_name = 'workout_steps' AND column_name = 'target_metric'
    ) INTO has_target_metric;
    
    SELECT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_name = 'workout_steps' AND column_name = 'target_min'
    ) INTO has_target_min;
    
    SELECT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_name = 'workout_steps' AND column_name = 'target_max'
    ) INTO has_target_max;
    
    SELECT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_name = 'workout_steps' AND column_name = 'target_value'
    ) INTO has_target_value;
    
    -- Only migrate if old columns exist
    IF has_duration_seconds OR has_distance_meters OR has_target_metric THEN
        -- Build dynamic SQL based on what columns exist
        sql_text := 'UPDATE workout_steps SET targets = jsonb_build_object(';
        
        -- Build duration part
        sql_text := sql_text || '''duration'', CASE ';
        IF has_duration_seconds THEN
            sql_text := sql_text || 'WHEN duration_seconds IS NOT NULL THEN jsonb_build_object(''type'', ''time'', ''seconds'', duration_seconds) ';
        END IF;
        IF has_distance_meters THEN
            sql_text := sql_text || 'WHEN distance_meters IS NOT NULL THEN jsonb_build_object(''type'', ''distance'', ''meters'', distance_meters) ';
        END IF;
        sql_text := sql_text || 'ELSE NULL END, ';
        
        -- Build target part
        sql_text := sql_text || '''target'', CASE ';
        IF has_target_metric AND has_target_min AND has_target_max THEN
            sql_text := sql_text || 'WHEN target_metric IS NOT NULL AND target_min IS NOT NULL AND target_max IS NOT NULL THEN jsonb_build_object(''metric'', target_metric, ''min'', target_min, ''max'', target_max) ';
        END IF;
        IF has_target_metric AND has_target_value THEN
            sql_text := sql_text || 'WHEN target_metric IS NOT NULL AND target_value IS NOT NULL THEN jsonb_build_object(''metric'', target_metric, ''value'', target_value) ';
        END IF;
        sql_text := sql_text || 'ELSE NULL END) WHERE targets = ''{}''::jsonb';
        
        EXECUTE sql_text;
        
        RAISE NOTICE 'Migrated data from old columns to targets JSONB';
    ELSE
        RAISE NOTICE 'Old columns do not exist - skipping data migration (schema may already be migrated)';
    END IF;
END $$;

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
