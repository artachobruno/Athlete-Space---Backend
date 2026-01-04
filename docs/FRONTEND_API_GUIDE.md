# Frontend API Guide

## Coach Endpoints

### 1. POST `/coach/chat` - Conversational Chat

**Purpose**: Interactive conversational interface for asking questions about training, fatigue, race prep, etc.

**Authentication**: None (currently unauthenticated)

**Request Body**:
```typescript
{
  message: string;           // Required: User's question/message
  days: number;              // Optional (default: 60): Number of days to look back for context
  days_to_race?: number;     // Optional: Number of days until race (for race-specific advice)
}
```

**Example Request**:
```json
{
  "message": "What session should I do today?",
  "days": 60,
  "days_to_race": null
}
```

**Response**:
```typescript
{
  intent: string;            // Detected intent (e.g., "today_session", "fatigue_check", etc.)
  reply: string;             // Coach's response text
}
```

**Example Response**:
```json
{
  "intent": "today_session",
  "reply": "Based on your training load, I recommend a moderate intensity run..."
}
```

**Current UI Usage** (✅ Correct):
- Location: `ui/app.py` line 52-56
- Sends: `message` and `days` (matches selected time window)
- Missing: `days_to_race` (optional, so this is fine)

---

### 2. GET `/state/coach` - Structured Coach Insights

**Purpose**: Get structured coaching insights, recommendations, and risk assessment (used for the coach panel display)

**Authentication**: Required (JWT token in Authorization header)

**Request Headers**:
```
Authorization: Bearer <JWT_TOKEN>
```

**Request Parameters**: None

**Response**:
```typescript
{
  summary: string;                    // 1-2 sentence high-level assessment
  insights: string[];                 // Bullet-level observations (max 3)
  recommendations: string[];          // Action items (max 2)
  risk_level: "none" | "low" | "medium" | "high";
  intervention: boolean;              // Should UI emphasize/alert?
  follow_up_prompts?: string[];       // Optional constrained questions
}
```

**Example Response**:
```json
{
  "summary": "Training load is stable and well absorbed.",
  "insights": [
    "Volume is consistent with good recovery",
    "TSB trending positive",
    "No red flags detected"
  ],
  "recommendations": [
    "Continue current training pattern",
    "Consider adding intensity next week"
  ],
  "risk_level": "none",
  "intervention": false,
  "follow_up_prompts": null
}
```

**Current UI Usage** (✅ Correct):
- Location: `ui/app.py` line 289
- Uses: `insights[0]`, `risk_level`, `recommendations[0]`
- Display: Shows in coach panel sidebar

---

## Other Endpoints Used by UI

### GET `/me/status` - Sync Status
**Purpose**: Check Strava connection and sync status

**Authentication**: Required

**Response**:
```typescript
{
  connected: boolean;
  last_sync: string | null;  // ISO 8601 timestamp
  state: "ok" | "syncing" | "stale";
}
```

### GET `/me/overview` - Training Overview
**Purpose**: Get training metrics (CTL, ATL, TSB) and data quality

**Authentication**: Required

**Response**:
```typescript
{
  connected: boolean;
  last_sync: string | null;
  data_quality: "ok" | "limited" | "insufficient";
  metrics: {
    ctl: [string, number][];  // [(date, value), ...]
    atl: [string, number][];
    tsb: [string, number][];
  };
  today: {
    ctl: number;
    atl: number;
    tsb: number;
    tsb_7d_avg: number;
  };
}
```

---

## Recommendations for Frontend

### ✅ Currently Correct
1. **POST `/coach/chat`**: Properly implemented with `message` and `days` parameters
2. **GET `/state/coach`**: Correctly used for structured insights display

### 💡 Optional Enhancements

1. **Add `days_to_race` parameter** to `/coach/chat` if you have race date information:
   ```typescript
   {
     "message": "How should I taper for my race?",
     "days": 60,
     "days_to_race": 14  // 14 days until race
   }
   ```

2. **Use `follow_up_prompts`** from `/state/coach` response to suggest follow-up questions to users

3. **Handle `intervention: true`** from `/state/coach` to highlight/alerts in UI when coach recommends intervention

4. **Consider using other coach endpoints** if needed:
   - `GET /coach/summary` - High-level summary only
   - `GET /coach/observations` - Just observations
   - `GET /coach/recommendations` - Just recommendations
   - `GET /coach/confidence` - Confidence scores for data quality

---

## Authentication Notes

- Most endpoints require JWT authentication via `Authorization: Bearer <token>` header
- `/coach/chat` endpoint does NOT currently require authentication (may change in future)
- Check `/app/api/dependencies/auth.py` for authentication implementation details

---

## Error Handling

All endpoints may return standard HTTP error codes:
- `401`: Unauthorized (missing/invalid token)
- `404`: Resource not found (e.g., no Strava account connected)
- `500`: Server error

Handle errors gracefully and provide user-friendly messages.

