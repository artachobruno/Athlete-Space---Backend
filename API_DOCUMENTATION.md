# Complete API Documentation for Frontend

This document provides complete information about all API endpoints available for the frontend.

**Base URL**: `https://your-backend-url.com` (or `http://localhost:8000` for local development)

**Authentication**: Most endpoints require JWT authentication via `Authorization: Bearer <token>` header.

---

## Table of Contents

1. [Authentication](#authentication)
2. [User & Profile](#user--profile)
3. [Activities](#activities)
4. [Calendar](#calendar)
5. [Training](#training)
6. [Coach](#coach)
7. [Intelligence](#intelligence)
8. [Strava Integration](#strava-integration)

---

## Authentication

### POST `/auth/login`
Login with Strava athlete_id and get JWT token.

**Request Body**:
```json
{
  "athlete_id": 12345678
}
```

**Response**:
```json
{
  "access_token": "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9...",
  "token_type": "bearer",
  "user_id": "user_abc123"
}
```

**Status Codes**:
- `200`: Success
- `404`: User not found

---

### GET `/auth/strava`
Initiate Strava OAuth connection flow.

**Headers**: Optional `Authorization: Bearer <token>` (if linking to existing user)

**Response**:
```json
{
  "redirect_url": "https://www.strava.com/oauth/authorize?...",
  "oauth_url": "https://www.strava.com/oauth/authorize?...",
  "url": "https://www.strava.com/oauth/authorize?..."
}
```

**Usage**: Frontend should redirect user to `redirect_url`.

---

### GET `/auth/strava/callback`
OAuth callback handler (handled by Strava, redirects to frontend).

**Note**: This endpoint is called by Strava, not directly by frontend.

---

### POST `/auth/strava/disconnect`
Disconnect user's Strava account.

**Headers**: `Authorization: Bearer <token>` (required)

**Response**:
```json
{
  "success": true,
  "message": "Strava account disconnected"
}
```

---

## User & Profile

### GET `/me/status`
Get athlete sync status and connection state.

**Headers**: `Authorization: Bearer <token>` (required)

**Response**:
```json
{
  "connected": true,
  "last_sync": "2024-01-15T10:30:00Z",
  "state": "ok"
}
```

**State Values**:
- `"ok"`: Synced within last 24 hours
- `"syncing"`: Currently syncing
- `"stale"`: Last sync > 24 hours ago

---

### GET `/me/overview`
Get athlete training overview with metrics.

**Headers**: `Authorization: Bearer <token>` (required)

**Query Parameters**:
- `days` (optional): Number of days to look back (default: 7, max: 365)

**Response**:
```json
{
  "connected": true,
  "last_sync": "2024-01-15T10:30:00Z",
  "data_quality": "ok",
  "metrics": {
    "ctl": [["2024-01-08", 45.2], ["2024-01-09", 45.8], ...],
    "atl": [["2024-01-08", 38.5], ["2024-01-09", 39.1], ...],
    "tsb": [["2024-01-08", 6.7], ["2024-01-09", 6.7], ...]
  },
  "today": {
    "ctl": 45.8,
    "atl": 39.1,
    "tsb": 6.7,
    "tsb_7d_avg": 5.2
  }
}
```

**Data Quality Values**:
- `"ok"`: >= 14 days of data
- `"limited"`: 7-13 days of data
- `"insufficient"`: < 7 days of data

---

### GET `/me/overview/debug`
Debug endpoint to visualize overview data (includes server timestamp).

**Headers**: `Authorization: Bearer <token>` (required)

**Query Parameters**:
- `days` (optional): Number of days to look back (default: 7, max: 365)

**Response**:
```json
{
  "server_time": "2024-01-15T10:30:00Z",
  "overview": {
    // Same as /me/overview response
  }
}
```

---

### GET `/me/profile`
Get athlete profile information.

**Headers**: `Authorization: Bearer <token>` (required)

**Response**:
```json
{
  "user_id": "user_abc123",
  "athlete_id": "12345678",
  "firstname": "John",
  "lastname": "Doe",
  "city": "San Francisco",
  "state": "CA",
  "country": "United States",
  "weight": 70.5,
  "profile_photo": "https://..."
}
```

---

### POST `/me/sync/history`
Trigger full historical backfill from Strava (runs in background).

**Headers**: `Authorization: Bearer <token>` (required)

**Response**:
```json
{
  "success": true,
  "message": "Historical sync started in background. This may take several minutes.",
  "user_id": "user_abc123",
  "last_sync": "2024-01-15T10:30:00Z"
}
```

---

## Activities

### GET `/activities`
Get paginated list of activities for current user.

**Headers**: `Authorization: Bearer <token>` (required)

**Query Parameters**:
- `limit` (optional): Max activities to return (1-100, default: 50)
- `offset` (optional): Number to skip (default: 0)

**Response**:
```json
{
  "activities": [
    {
      "id": "activity-uuid-123",
      "user_id": "user_abc123",
      "strava_activity_id": "987654321",
      "start_time": "2024-01-15T08:00:00Z",
      "type": "Run",
      "duration_seconds": 3600,
      "distance_meters": 10000,
      "elevation_gain_meters": 150,
      "created_at": "2024-01-15T08:05:00Z",
      "has_raw_json": true,
      "has_streams": false
    }
  ],
  "total": 150,
  "limit": 50,
  "offset": 0
}
```

**Note**: Data comes from database, not Strava API.

---

### GET `/activities/{activity_id}`
Get single activity by ID with full details.

**Headers**: `Authorization: Bearer <token>` (required)

**Path Parameters**:
- `activity_id`: Activity UUID

**Response**:
```json
{
  "id": "activity-uuid-123",
  "user_id": "user_abc123",
  "strava_activity_id": "987654321",
  "start_time": "2024-01-15T08:00:00Z",
  "type": "Run",
  "duration_seconds": 3600,
  "distance_meters": 10000,
  "elevation_gain_meters": 150,
  "raw_json": { /* Full Strava activity JSON */ },
  "streams_data": null,
  "created_at": "2024-01-15T08:05:00Z"
}
```

---

### POST `/activities/{activity_id}/fetch-streams`
Fetch and save streams data for an activity (on-demand).

**Headers**: `Authorization: Bearer <token>` (required)

**Path Parameters**:
- `activity_id`: Activity UUID

**Response**:
```json
{
  "success": true,
  "message": "Streams data fetched and saved",
  "streams_data": {
    "time": [0, 1, 2, ...],
    "latlng": [[37.7749, -122.4194], ...],
    "distance": [0, 10, 20, ...],
    "altitude": [100.5, 101.2, ...],
    "heartrate": [120, 125, ...],
    "watts": [200, 210, ...],
    "cadence": [85, 86, ...],
    "velocity_smooth": [3.5, 3.6, ...]
  },
  "data_points": 3600
}
```

**Status Codes**:
- `200`: Success
- `404`: Activity not found or streams unavailable

**Note**: This makes a Strava API call. Use sparingly.

---

### GET `/activities/{activity_id}/streams`
Get formatted streams data for frontend visualization.

**Headers**: `Authorization: Bearer <token>` (required)

**Path Parameters**:
- `activity_id`: Activity UUID

**Response**:
```json
{
  "time": [0, 1, 2, ...],
  "route_points": [[37.7749, -122.4194], [37.7750, -122.4195], ...],
  "elevation": [100.5, 101.2, ...],
  "pace": [5.2, 5.1, null, ...],
  "heartrate": [120, 125, ...],
  "distance": [0, 10, 20, ...],
  "power": [200, 210, ...],
  "cadence": [85, 86, ...],
  "data_points": 3600
}
```

**Status Codes**:
- `200`: Success
- `404`: Streams not available (use POST `/activities/{activity_id}/fetch-streams` first)

**Note**:
- `pace` is in min/km, `null` when stopped
- All arrays are aligned by index (streams[i] corresponds to time[i])
- Data comes from database, not Strava API

---

## Calendar

### GET `/calendar/week`
Get calendar data for the current week.

**Headers**: `Authorization: Bearer <token>` (required)

**Response**:
```json
{
  "week_start": "2024-01-15",
  "week_end": "2024-01-21",
  "sessions": [
    {
      "id": "activity-uuid-123",
      "date": "2024-01-15",
      "time": "08:00",
      "type": "Run",
      "title": "Morning Run",
      "duration_minutes": 60,
      "distance_km": 10.0,
      "intensity": "moderate",
      "status": "completed",
      "notes": null
    }
  ]
}
```

**Note**: Data comes from database, not Strava API.

---

### GET `/calendar/today`
Get calendar data for today.

**Headers**: `Authorization: Bearer <token>` (required)

**Response**:
```json
{
  "date": "2024-01-15",
  "sessions": [
    {
      "id": "activity-uuid-123",
      "date": "2024-01-15",
      "time": "08:00",
      "type": "Run",
      "title": "Morning Run",
      "duration_minutes": 60,
      "distance_km": 10.0,
      "intensity": "moderate",
      "status": "completed",
      "notes": null
    }
  ]
}
```

---

### GET `/calendar/sessions`
Get paginated list of all calendar sessions.

**Headers**: `Authorization: Bearer <token>` (required)

**Query Parameters**:
- `limit` (optional): Max sessions to return (default: 50)
- `offset` (optional): Number to skip (default: 0)

**Response**:
```json
{
  "sessions": [
    {
      "id": "activity-uuid-123",
      "date": "2024-01-15",
      "time": "08:00",
      "type": "Run",
      "title": "Morning Run",
      "duration_minutes": 60,
      "distance_km": 10.0,
      "intensity": "moderate",
      "status": "completed",
      "notes": null
    }
  ],
  "total": 150
}
```

---

### GET `/calendar/season`
Get calendar data for the season (90 days before and after today).

**Headers**: `Authorization: Bearer <token>` (required)

**Response**:
```json
{
  "season_start": "2023-10-17",
  "season_end": "2024-04-15",
  "sessions": [ /* All sessions in season */ ],
  "total_sessions": 120,
  "completed_sessions": 95,
  "planned_sessions": 25
}
```

---

## Training

### GET `/training/state`
Get current training state and metrics.

**Headers**: `Authorization: Bearer <token>` (required)

**Response**:
```json
{
  "current": {
    "ctl": 45.8,
    "atl": 39.1,
    "tsb": 6.7,
    "trend": "increasing"
  },
  "week_volume_hours": 8.5,
  "week_load": 125.5,
  "month_volume_hours": 35.2,
  "month_load": 520.3,
  "last_updated": "2024-01-15T10:30:00Z"
}
```

**Trend Values**: `"increasing"` | `"stable"` | `"decreasing"`

---

### GET `/training/distribution`
Get training distribution across zones and activity types.

**Headers**: `Authorization: Bearer <token>` (required)

**Query Parameters**:
- `period` (optional): `"week"` | `"month"` | `"season"` (default: `"week"`)

**Response**:
```json
{
  "period": "week",
  "total_hours": 8.5,
  "zones": [
    {
      "zone": "Zone 1",
      "hours": 3.2,
      "percentage": 37.6
    },
    {
      "zone": "Zone 2",
      "hours": 4.1,
      "percentage": 48.2
    }
    // ... Zone 3, 4, 5
  ],
  "by_type": {
    "Run": 5.2,
    "Ride": 3.3
  }
}
```

---

### GET `/training/signals`
Get training signals and observations.

**Headers**: `Authorization: Bearer <token>` (required)

**Response**:
```json
{
  "signals": [
    {
      "id": "signal_abc123_tsb_positive",
      "type": "readiness",
      "severity": "low",
      "message": "TSB is positive, indicating good recovery and readiness for training",
      "timestamp": "2024-01-15T10:30:00Z",
      "metrics": {
        "tsb": 6.7,
        "ctl": 45.8
      }
    }
  ],
  "summary": "Good recovery state with positive TSB. Ready for quality training.",
  "recommendation": "Consider adding intensity work while maintaining recovery."
}
```

**Signal Types**: `"fatigue"` | `"overreaching"` | `"undertraining"` | `"readiness"`

**Severity**: `"low"` | `"moderate"` | `"high"`

---

## Coach

### POST `/coach/chat`
Chat with the AI coach.

**Headers**: `Authorization: Bearer <token>` (required)

**Request Body**:
```json
{
  "message": "How should I structure my training this week?",
  "days": 60,
  "days_to_race": null
}
```

**Response**:
```json
{
  "intent": "training_advice",
  "reply": "Based on your current training load, I recommend..."
}
```

**Intent Values**: Various (e.g., `"training_advice"`, `"cold_start"`, `"error"`)

---

## Intelligence

### GET `/intelligence/season`
Get the latest active season plan.

**Headers**: `Authorization: Bearer <token>` (required)

**Response**:
```json
{
  "id": "plan-uuid-123",
  "user_id": "user_abc123",
  "athlete_id": 12345678,
  "plan": {
    "goal": "Marathon",
    "target_date": "2024-04-15",
    "phases": [ /* ... */ ]
  },
  "version": 1,
  "is_active": true,
  "created_at": "2024-01-01T00:00:00Z",
  "updated_at": "2024-01-15T10:30:00Z"
}
```

**Status Codes**:
- `200`: Success
- `503`: Plan not available (coach still learning)

---

### GET `/intelligence/week`
Get the latest active weekly intent.

**Headers**: `Authorization: Bearer <token>` (required)

**Query Parameters**:
- `week_start` (optional): Week start date (Monday) in `YYYY-MM-DD` format. If not provided, uses current week.

**Response**:
```json
{
  "id": "intent-uuid-123",
  "user_id": "user_abc123",
  "athlete_id": 12345678,
  "intent": {
    "week_start": "2024-01-15",
    "focus": "Base building",
    "sessions": [ /* ... */ ]
  },
  "season_plan_id": "plan-uuid-123",
  "version": 1,
  "is_active": true,
  "created_at": "2024-01-15T00:00:00Z",
  "updated_at": "2024-01-15T10:30:00Z"
}
```

**Status Codes**:
- `200`: Success
- `503`: Intent not available for the week

---

### GET `/intelligence/today`
Get the latest active daily decision.

**Headers**: `Authorization: Bearer <token>` (required)

**Query Parameters**:
- `decision_date` (optional): Decision date in `YYYY-MM-DD` format. If not provided, uses today.

**Response**:
```json
{
  "id": "decision-uuid-123",
  "user_id": "user_abc123",
  "athlete_id": 12345678,
  "decision": {
    "date": "2024-01-15",
    "recommendation": "Easy run",
    "rationale": "Recovery day after yesterday's hard session",
    "session": { /* ... */ }
  },
  "version": 1,
  "is_active": true,
  "created_at": "2024-01-15T00:00:00Z",
  "updated_at": "2024-01-15T10:30:00Z"
}
```

**Status Codes**:
- `200`: Success
- `503`: Decision not available for the date

---

## Strava Integration

### GET `/strava/status`
Get Strava connection status.

**Headers**: `Authorization: Bearer <token>` (required)

**Response**:
```json
{
  "connected": true,
  "activity_count": 150
}
```

---

### GET `/strava/sync-progress`
Get sync progress information.

**Headers**: `Authorization: Bearer <token>` (required)

**Response**:
```json
{
  "last_sync": "2024-01-15T10:30:00Z",
  "sync_in_progress": false,
  "total_activities": 150
}
```

---

### POST `/strava/sync`
Trigger manual sync (admin/dev only).

**Headers**: `Authorization: Bearer <token>` (required)

**Response**:
```json
{
  "success": true,
  "message": "Sync started"
}
```

---

## Error Responses

All endpoints may return the following error responses:

### 401 Unauthorized
```json
{
  "detail": "Not authenticated"
}
```

### 403 Forbidden
```json
{
  "detail": "Not enough permissions"
}
```

### 404 Not Found
```json
{
  "detail": "Resource not found"
}
```

### 500 Internal Server Error
```json
{
  "detail": "Internal server error message"
}
```

---

## Authentication Flow

1. **Initial Connection**:
   - Frontend calls `GET /auth/strava`
   - Redirects user to returned `redirect_url`
   - User authorizes on Strava
   - Strava redirects to `/auth/strava/callback`
   - Backend redirects to frontend with `?token=<jwt_token>`
   - Frontend stores token

2. **Subsequent Requests**:
   - Include `Authorization: Bearer <token>` header in all requests
   - Token expires after 30 days (configurable)

3. **Token Refresh**:
   - Currently, user must reconnect if token expires
   - Future: Implement refresh token endpoint

---

## Data Source Notes

**Important**: All endpoints marked with "Data Source: Reads from database" do NOT make Strava API calls. They read from the local database which is populated by background sync jobs.

**Endpoints that make Strava API calls**:
- `POST /activities/{activity_id}/fetch-streams` - On-demand streams fetch
- Background sync jobs (not directly called by frontend)

**Performance**: Database queries are optimized with composite indexes for fast responses.

---

## Rate Limiting

- No explicit rate limiting on endpoints
- Strava API calls are rate-limited internally (600 requests per 15 minutes)
- Background sync respects Strava quotas automatically

---

## CORS

CORS is configured to allow requests from:
- Production frontend: `https://pace-ai.onrender.com`
- Local development: `http://localhost:5173`, `http://localhost:3000`, etc.
- Configurable via `CORS_ALLOWED_ORIGINS` environment variable

---

## Health Check

### GET `/health`
Check if API is running.

**Response**:
```json
{
  "status": "ok"
}
```

No authentication required.

---

## Notes for Frontend Developers

1. **Always include Authorization header** for protected endpoints
2. **Handle 401 errors** by redirecting to login/reconnect
3. **Cache responses** where appropriate (overview, calendar can be cached for 1-5 minutes)
4. **Streams data** must be fetched on-demand using POST endpoint before GET
5. **Data quality** should be checked - show "Limited data" badge when `data_quality != "ok"`
6. **Sync status** - check `/me/status` to show sync state to users
7. **Error handling** - All endpoints return consistent error format with `detail` field

---

## Example Frontend Usage

```javascript
// Example: Fetch overview data
const response = await fetch('https://api.example.com/me/overview?days=30', {
  headers: {
    'Authorization': `Bearer ${token}`,
    'Content-Type': 'application/json'
  }
});

const data = await response.json();
// data.connected, data.metrics, data.today, etc.

// Example: Fetch activity streams
// First, fetch streams if not available
await fetch(`https://api.example.com/activities/${activityId}/fetch-streams`, {
  method: 'POST',
  headers: {
    'Authorization': `Bearer ${token}`
  }
});

// Then get formatted streams
const streamsResponse = await fetch(`https://api.example.com/activities/${activityId}/streams`, {
  headers: {
    'Authorization': `Bearer ${token}`
  }
});

const streams = await streamsResponse.json();
// streams.route_points, streams.elevation, streams.pace, etc.
```

---

## Support

For questions or issues, check the backend logs or contact the development team.
