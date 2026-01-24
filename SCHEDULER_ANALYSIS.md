# Scheduler Resync Analysis

## Problem Identified

The scheduler is **unnecessarily resyncing users** even when they've already been synced recently. This wastes API quota and increases memory/CPU usage.

## Current Behavior

### 1. Background Sync (`sync_tick`) - Runs Every 6 Hours
**Location:** `app/ingestion/sync_scheduler.py`

**Issue:**
- Calls `sync_all_users()` which syncs **ALL users** regardless of when they were last synced
- No check to skip users who were synced recently (e.g., within the last hour)
- Always makes Strava API calls even if `last_sync_at` is very recent

**Code Flow:**
```python
def sync_tick() -> None:
    result = sync_all_users()  # Syncs ALL users, no filtering

def sync_all_users():
    accounts = session.execute(select(StravaAccount)).all()  # Gets ALL accounts
    for account in accounts:
        sync_user_activities(user_id)  # Syncs every user, no skip logic
```

### 2. Ingestion Tick (`ingestion_tick`) - Runs Every 30 Minutes
**Location:** `app/ingestion/scheduler.py`

**Issue:**
- Runs incremental tasks for **ALL users** every 30 minutes
- No check to skip users who were recently synced
- Always makes API calls even if there are no new activities

**Code Flow:**
```python
def ingestion_tick():
    users = session.query(StravaAuth).all()  # Gets ALL users
    _run_incremental_tasks(user_data)  # Runs for ALL users, no skip logic
```

### 3. Incremental Sync - Always Makes API Calls
**Location:** `app/ingestion/jobs/strava_incremental.py`

**Issue:**
- Always calls `client.fetch_recent_activities()` even if `last_ingested_at` is very recent
- No early exit if sync happened recently (e.g., within last hour)
- Makes API call, then checks if activities exist (should check first)

## Impact

1. **API Quota Waste:**
   - Users synced manually or via frontend are resynced again on next scheduler run
   - Multiple syncs per day for the same user when only one is needed
   - Strava API has rate limits (600 requests per 15 minutes, 30,000 per day)

2. **Memory/CPU Usage:**
   - Unnecessary database queries
   - Unnecessary API calls
   - Unnecessary processing of activities that are already synced

3. **Performance:**
   - Slower scheduler runs
   - Higher latency for legitimate syncs

## Recommended Fixes

### ✅ Fix 1: Skip Recently Synced Users in Background Sync - IMPLEMENTED
Added logic to skip users who were synced recently (within last 2 hours):

- Modified `sync_all_users()` in `app/ingestion/background_sync.py`
- Skips users with `last_sync_at` within 2 hours
- Logs skipped users for monitoring
- Returns skipped count in results

### ✅ Fix 2: Skip Recently Synced Users in Ingestion Tick - IMPLEMENTED
Added similar logic to incremental tasks:

- Modified `_run_incremental_tasks()` in `app/ingestion/scheduler.py`
- Fetches `last_sync_at` from `StravaAccount` for each user
- Skips users synced within last 1 hour
- Logs skipped users for monitoring

### ✅ Fix 3: Early Exit in Incremental Sync - IMPLEMENTED
Added early exit check before making API call:

- Modified `incremental_sync_user()` in `app/ingestion/jobs/strava_incremental.py`
- Checks if `last_ingested_at` is within 1 hour
- Returns early if recently synced (avoids API call)
- Logs skip reason for debugging

## Implementation Status

1. ✅ **High Priority:** Fix background sync (`sync_all_users`) - COMPLETED
   - ✅ Added smart sync decision logic based on user activity patterns
   - ✅ Checks if user has recent activities (last 7 days) to determine if they're active
   - ✅ Different thresholds for active vs inactive users:
     - Active users: sync if 2+ hours since last sync
     - Inactive users: sync if 4+ hours since last sync
     - Always sync if 6+ hours (scheduled sync)
   - ✅ Skips users synced within last 1 hour (too soon for new activities)

2. ✅ **Medium Priority:** Fix ingestion tick incremental tasks - COMPLETED
3. ✅ **Low Priority:** Early exit in incremental sync - COMPLETED

## Smart Sync Logic

The new `_should_sync_user()` function implements intelligent sync decisions:

1. **First Sync:** Always sync users who have never been synced
2. **Very Recent (< 1 hour):** Skip - too soon for new activities
3. **Scheduled Sync (6+ hours):** Always sync - catch up even if user inactive
4. **Active Users (2-6 hours):** 
   - Check if user has activities in last 7 days
   - If active: sync if 2+ hours since last sync
   - If inactive: sync if 4+ hours since last sync

This approach:
- Reduces API calls for inactive users
- Keeps active users up-to-date
- Ensures scheduled syncs still run
- Marks sync completion via `last_sync_at` (already implemented)

## Testing

After implementing fixes:
1. Monitor scheduler logs to verify users are skipped when recently synced
2. Check API quota usage decreases
3. Verify users are still synced when needed (not skipped when they should sync)
4. Test edge cases (first sync, large gaps, etc.)
