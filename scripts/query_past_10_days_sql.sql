-- SQL Queries to get activities and planned sessions from past 10 days with pairing status
-- Run these queries directly on your PostgreSQL database

-- ============================================================================
-- 1. Get all activities from past 10 days with pairing status
-- ============================================================================
SELECT 
    a.id AS activity_id,
    a.starts_at::date AS activity_date,
    a.title AS activity_title,
    a.sport AS activity_sport,
    a.duration_seconds,
    a.distance_meters,
    a.tss,
    CASE 
        WHEN sl.id IS NOT NULL THEN 'PAIRED'
        ELSE 'UNPAIRED'
    END AS pairing_status,
    sl.planned_session_id AS paired_with_planned_session_id,
    ps.title AS paired_planned_session_title
FROM activities a
LEFT JOIN session_links sl ON sl.activity_id = a.id
LEFT JOIN planned_sessions ps ON ps.id = sl.planned_session_id
WHERE a.starts_at >= CURRENT_DATE - INTERVAL '9 days'
  AND a.starts_at < CURRENT_DATE + INTERVAL '1 day'
ORDER BY a.starts_at ASC;

-- ============================================================================
-- 2. Get all planned sessions from past 10 days with pairing status
-- ============================================================================
SELECT 
    ps.id AS planned_session_id,
    ps.starts_at::date AS planned_date,
    ps.title AS planned_title,
    ps.sport AS planned_sport,
    ps.session_type AS planned_session_type,
    ps.intensity,
    ps.duration_seconds,
    ps.distance_meters,
    ps.status AS planned_status,
    CASE 
        WHEN sl.id IS NOT NULL THEN 'PAIRED'
        ELSE 'UNPAIRED'
    END AS pairing_status,
    sl.activity_id AS paired_with_activity_id,
    a.title AS paired_activity_title
FROM planned_sessions ps
LEFT JOIN session_links sl ON sl.planned_session_id = ps.id
LEFT JOIN activities a ON a.id = sl.activity_id
WHERE ps.starts_at >= CURRENT_DATE - INTERVAL '9 days'
  AND ps.starts_at < CURRENT_DATE + INTERVAL '1 day'
  AND ps.status NOT IN ('deleted')
ORDER BY ps.starts_at ASC;

-- ============================================================================
-- 3. Get day-by-day summary with pairing counts
-- ============================================================================
WITH date_range AS (
    SELECT generate_series(
        CURRENT_DATE - INTERVAL '9 days',
        CURRENT_DATE,
        INTERVAL '1 day'
    )::date AS day_date
),
daily_activities AS (
    SELECT 
        a.starts_at::date AS activity_date,
        COUNT(*) AS total_activities,
        COUNT(sl.id) AS paired_activities,
        COUNT(*) - COUNT(sl.id) AS unpaired_activities
    FROM activities a
    LEFT JOIN session_links sl ON sl.activity_id = a.id
    WHERE a.starts_at >= CURRENT_DATE - INTERVAL '9 days'
      AND a.starts_at < CURRENT_DATE + INTERVAL '1 day'
    GROUP BY a.starts_at::date
),
daily_planned AS (
    SELECT 
        ps.starts_at::date AS planned_date,
        COUNT(*) AS total_planned,
        COUNT(CASE WHEN ps.status = 'completed' THEN 1 END) AS completed_planned,
        COUNT(CASE WHEN ps.status = 'planned' THEN 1 END) AS still_planned,
        COUNT(sl.id) AS paired_planned,
        COUNT(*) - COUNT(sl.id) AS unpaired_planned
    FROM planned_sessions ps
    LEFT JOIN session_links sl ON sl.planned_session_id = ps.id
    WHERE ps.starts_at >= CURRENT_DATE - INTERVAL '9 days'
      AND ps.starts_at < CURRENT_DATE + INTERVAL '1 day'
      AND ps.status NOT IN ('deleted')
    GROUP BY ps.starts_at::date
)
SELECT 
    dr.day_date,
    TO_CHAR(dr.day_date, 'Day, Month DD, YYYY') AS formatted_date,
    COALESCE(da.total_activities, 0) AS total_activities,
    COALESCE(da.paired_activities, 0) AS paired_activities,
    COALESCE(da.unpaired_activities, 0) AS unpaired_activities,
    COALESCE(dp.total_planned, 0) AS total_planned_sessions,
    COALESCE(dp.completed_planned, 0) AS completed_planned_sessions,
    COALESCE(dp.still_planned, 0) AS still_planned_sessions,
    COALESCE(dp.paired_planned, 0) AS paired_planned_sessions,
    COALESCE(dp.unpaired_planned, 0) AS unpaired_planned_sessions,
    CASE 
        WHEN COALESCE(da.total_activities, 0) > 1 THEN 'PAIRED DAY'
        WHEN COALESCE(da.total_activities, 0) = 1 AND COALESCE(dp.total_planned, 0) > 0 THEN 'PAIRED DAY'
        ELSE 'SINGLE ACTIVITY DAY'
    END AS day_type
FROM date_range dr
LEFT JOIN daily_activities da ON da.activity_date = dr.day_date
LEFT JOIN daily_planned dp ON dp.planned_date = dr.day_date
ORDER BY dr.day_date ASC;

-- ============================================================================
-- 4. Get detailed day-by-day breakdown (all items for each day)
-- ============================================================================
WITH date_range AS (
    SELECT generate_series(
        CURRENT_DATE - INTERVAL '9 days',
        CURRENT_DATE,
        INTERVAL '1 day'
    )::date AS day_date
),
day_activities AS (
    SELECT 
        a.starts_at::date AS activity_date,
        json_agg(
            json_build_object(
                'id', a.id,
                'title', a.title,
                'sport', a.sport,
                'duration_seconds', a.duration_seconds,
                'distance_meters', a.distance_meters,
                'tss', a.tss,
                'paired', CASE WHEN sl.id IS NOT NULL THEN true ELSE false END,
                'paired_with_planned_session_id', sl.planned_session_id
            ) ORDER BY a.starts_at
        ) AS activities
    FROM activities a
    LEFT JOIN session_links sl ON sl.activity_id = a.id
    WHERE a.starts_at >= CURRENT_DATE - INTERVAL '9 days'
      AND a.starts_at < CURRENT_DATE + INTERVAL '1 day'
    GROUP BY a.starts_at::date
),
day_planned AS (
    SELECT 
        ps.starts_at::date AS planned_date,
        json_agg(
            json_build_object(
                'id', ps.id,
                'title', ps.title,
                'sport', ps.sport,
                'session_type', ps.session_type,
                'intensity', ps.intensity,
                'duration_seconds', ps.duration_seconds,
                'distance_meters', ps.distance_meters,
                'status', ps.status,
                'paired', CASE WHEN sl.id IS NOT NULL THEN true ELSE false END,
                'paired_with_activity_id', sl.activity_id
            ) ORDER BY ps.starts_at
        ) AS planned_sessions
    FROM planned_sessions ps
    LEFT JOIN session_links sl ON sl.planned_session_id = ps.id
    WHERE ps.starts_at >= CURRENT_DATE - INTERVAL '9 days'
      AND ps.starts_at < CURRENT_DATE + INTERVAL '1 day'
      AND ps.status NOT IN ('deleted')
    GROUP BY ps.starts_at::date
)
SELECT 
    dr.day_date,
    TO_CHAR(dr.day_date, 'Day, Month DD, YYYY') AS formatted_date,
    COALESCE(da.activities, '[]'::json) AS activities,
    COALESCE(dp.planned_sessions, '[]'::json) AS planned_sessions
FROM date_range dr
LEFT JOIN day_activities da ON da.activity_date = dr.day_date
LEFT JOIN day_planned dp ON dp.planned_date = dr.day_date
ORDER BY dr.day_date ASC;

-- ============================================================================
-- 5. Overall summary statistics (past 10 days)
-- ============================================================================
SELECT 
    'Past 10 Days Summary' AS summary_type,
    COUNT(DISTINCT a.id) AS total_activities,
    COUNT(DISTINCT CASE WHEN sl_a.id IS NOT NULL THEN a.id END) AS paired_activities,
    COUNT(DISTINCT CASE WHEN sl_a.id IS NULL THEN a.id END) AS unpaired_activities,
    COUNT(DISTINCT ps.id) AS total_planned_sessions,
    COUNT(DISTINCT CASE WHEN ps.status = 'completed' THEN ps.id END) AS completed_planned_sessions,
    COUNT(DISTINCT CASE WHEN ps.status = 'planned' THEN ps.id END) AS still_planned_sessions,
    COUNT(DISTINCT CASE WHEN sl_p.id IS NOT NULL THEN ps.id END) AS paired_planned_sessions,
    COUNT(DISTINCT CASE WHEN sl_p.id IS NULL THEN ps.id END) AS unpaired_planned_sessions,
    COUNT(DISTINCT a.starts_at::date) AS days_with_activities,
    COUNT(DISTINCT ps.starts_at::date) AS days_with_planned_sessions
FROM activities a
FULL OUTER JOIN planned_sessions ps ON ps.starts_at::date = a.starts_at::date
LEFT JOIN session_links sl_a ON sl_a.activity_id = a.id
LEFT JOIN session_links sl_p ON sl_p.planned_session_id = ps.id
WHERE (a.starts_at >= CURRENT_DATE - INTERVAL '9 days' AND a.starts_at < CURRENT_DATE + INTERVAL '1 day')
   OR (ps.starts_at >= CURRENT_DATE - INTERVAL '9 days' AND ps.starts_at < CURRENT_DATE + INTERVAL '1 day' AND ps.status NOT IN ('deleted'));
