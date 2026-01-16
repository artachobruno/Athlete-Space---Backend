# Schema V2 Migration Status

## ✅ Completed (Step 0-3 Partial)

1. **Branch Created**: `schema-v2-3.1`
2. **Models Updated** (`app/db/models.py`):
   - ✅ `User`: Updated `auth_provider`/`role` to String (enums kept for compatibility)
   - ✅ `Activity`: All field renames applied
   - ✅ `PlannedSession`: All field renames applied
   - ✅ `SessionLink`: New model added

## 📋 Remaining Work

### Critical Files to Update (High Priority)

1. **Activity References** (774+ total references):
   - `app/api/activities/activities.py` - API endpoints
   - `app/calendar/api.py` - Calendar queries
   - `app/ingestion/save_activities.py` - Activity creation
   - `app/ingestion/background_sync.py` - Sync logic
   - `app/ingestion/api.py` - Ingestion API
   - `app/pairing/auto_pairing_service.py` - Pairing logic
   - `app/pairing/manual_pairing_service.py` - Manual pairing
   - `app/api/user/me.py` - User profile queries

2. **PlannedSession References** (626+ total references):
   - `app/coach/tools/modify_day.py` - Day modifications
   - `app/coach/tools/modify_week.py` - Week modifications
   - `app/calendar/api.py` - Calendar queries
   - `app/calendar/reconciliation.py` - Reconciliation logic
   - `app/coach/tools/session_planner.py` - Session planning
   - `app/api/training/manual_upload.py` - Manual uploads
   - `app/plans/regenerate/regeneration_executor.py` - Regeneration

3. **User/Auth References** (47+ total references):
   - `app/api/auth/auth_google.py` - Google auth
   - `app/api/auth/auth.py` - Email auth
   - `app/api/user/me.py` - User profile
   - `app/api/settings/settings.py` - Settings
   - `app/onboarding/persistence.py` - Onboarding

4. **SQL Queries** (100+ raw SQL references):
   - `app/api/training/state.py` - Raw SQL with `start_time`
   - `app/analytics/api.py` - Raw SQL queries
   - Various migration scripts

### Field Mapping Reference

#### Activities
```python
# OLD → NEW
Activity.start_time → Activity.starts_at
Activity.type → Activity.sport
Activity.strava_activity_id → Activity.source_activity_id
Activity.raw_json → Activity.metrics['raw_json']
Activity.streams_data → Activity.metrics['streams_data']
Activity.planned_session_id → (removed, use SessionLink)
Activity.athlete_id → (removed, use user_id only)
```

#### Planned Sessions
```python
# OLD → NEW
PlannedSession.date + PlannedSession.time → PlannedSession.starts_at
PlannedSession.type → PlannedSession.sport
PlannedSession.duration_minutes → PlannedSession.duration_seconds (multiply by 60)
PlannedSession.distance_km → PlannedSession.distance_meters (multiply by 1000)
PlannedSession.distance_mi → PlannedSession.distance_meters (multiply by 1609.344)
PlannedSession.completed_activity_id → (removed, use SessionLink)
PlannedSession.athlete_id → (removed, use user_id only)
```

#### Users
```python
# OLD → NEW
User.auth_provider (Enum) → User.auth_provider (String: 'google', 'email', 'apple')
User.role (Enum) → User.role (String: 'athlete', 'coach', 'admin')
# Note: Old enum values map directly to new strings
```

### Next Steps

1. **Update Activity Creation** (`app/ingestion/save_activities.py`):
   - Update `_create_activity()` to use new fields
   - Move `raw_json`/`streams_data` to `metrics` dict
   - Remove `planned_session_id` parameter

2. **Update Calendar Queries** (`app/calendar/api.py`):
   - Change `Activity.start_time` → `Activity.starts_at`
   - Change `PlannedSession.date` → `PlannedSession.starts_at`
   - Update date filtering logic

3. **Update Pairing Logic** (`app/pairing/auto_pairing_service.py`):
   - Remove `planned_session_id` from Activity
   - Use `SessionLink` model instead
   - Update queries to check `session_links` table

4. **Update SQL Queries**:
   - Search for raw SQL with old column names
   - Update to new column names
   - Test all queries

5. **Update Tests**:
   - Update test fixtures
   - Update assertions
   - Run full test suite

6. **Remove Deprecated Code**:
   - Remove `AuthProvider` and `UserRole` enums (after all references updated)
   - Clean up unused imports

### Verification Commands

```bash
# After completing updates, verify no old references:
rg -n "\bstart_time\b|\bstrava_activity_id\b|\braw_json\b|\bstreams_data\b|\bplanned_session_id\b" . || echo "✅ No old activity references"
rg -n "planned_sessions\.(date|time|duration_minutes|distance_km|distance_mi|completed_activity_id)" . || echo "✅ No old planned_session references"
rg -n "\bauthprovider\b|\buserrole\b" -i . || echo "✅ No old enum references"
```

### Schema Compatibility Check

After migration, add startup check (Step 6):
- Verify required columns exist
- Verify CHECK constraints exist
- Verify indexes exist
