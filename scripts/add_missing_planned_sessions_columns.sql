-- Migration script to add missing columns to planned_sessions table
-- Run this directly on your PostgreSQL database

-- Add execution_notes column (if it doesn't exist)
ALTER TABLE planned_sessions
ADD COLUMN IF NOT EXISTS execution_notes VARCHAR(120);

-- Add must_dos column (if it doesn't exist)
ALTER TABLE planned_sessions
ADD COLUMN IF NOT EXISTS must_dos JSONB;

-- Verify columns were added
SELECT column_name, data_type, is_nullable
FROM information_schema.columns
WHERE table_name = 'planned_sessions'
AND column_name IN ('execution_notes', 'must_dos')
ORDER BY column_name;
