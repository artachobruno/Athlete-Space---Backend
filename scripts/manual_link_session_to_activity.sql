-- Manual SQL script to link a completed planned session to an activity
-- 
-- Usage: Replace the placeholder values below:
-- 1. Find the session ID from planned_sessions table
-- 2. Find the activity ID from activities table (same date and sport)
-- 3. Update the user_id if needed

-- Step 1: Find the session you want to link (already have this: 5f8b89c9-6515-4d83-a679-a29957f2d891)
-- Step 2: Find activities on the same date that match the sport

-- Example: Find activities for 2026-01-17, sport = 'run'
SELECT 
    id,
    starts_at,
    sport,
    duration_seconds,
    distance_meters,
    title
FROM activities 
WHERE user_id = 'be7ea8c0-fac2-40f7-94d1-11af7783b0be'
  AND DATE(starts_at) = '2026-01-17'
  AND sport = 'run'
ORDER BY starts_at;

-- Step 3: Once you have the activity_id, run this to create the link:
-- Replace 'ACTIVITY_ID_HERE' with the actual activity ID from the query above

INSERT INTO session_links (
    id,
    user_id,
    planned_session_id,
    activity_id,
    status,
    method,
    confidence,
    notes,
    created_at,
    updated_at
) VALUES (
    gen_random_uuid()::text,
    'be7ea8c0-fac2-40f7-94d1-11af7783b0be',
    '5f8b89c9-6515-4d83-a679-a29957f2d891',  -- Your session ID
    'ACTIVITY_ID_HERE',  -- Replace with activity ID from Step 2
    'confirmed',
    'manual',
    1.0,
    'Manually linked via SQL script',
    NOW(),
    NOW()
)
ON CONFLICT (planned_session_id) DO UPDATE
SET
    activity_id = EXCLUDED.activity_id,
    status = EXCLUDED.status,
    method = EXCLUDED.method,
    confidence = EXCLUDED.confidence,
    notes = EXCLUDED.notes,
    updated_at = NOW();

-- Step 4: Verify the link was created
SELECT 
    sl.id,
    sl.planned_session_id,
    sl.activity_id,
    sl.status,
    sl.method,
    ps.starts_at as session_date,
    a.starts_at as activity_date,
    ps.sport as session_sport,
    a.sport as activity_sport
FROM session_links sl
JOIN planned_sessions ps ON ps.id = sl.planned_session_id
JOIN activities a ON a.id = sl.activity_id
WHERE sl.planned_session_id = '5f8b89c9-6515-4d83-a679-a29957f2d891';
