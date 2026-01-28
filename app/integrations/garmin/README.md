# Garmin integration — event-driven architecture

**Garmin is NOT a pull API.**

All activity data must arrive via **Push** or **Ping** callbacks. The system **MUST NEVER** rely on polling Garmin for activities.

---

## Mental model

| | Strava | Garmin |
|---|--------|--------|
| **Model** | Pull-based REST | Event-driven, server-to-server |
| **You ask** | "Give me activities" | — |
| **Garmin tells you** | — | When data exists |
| **Backfill** | Loop + pagination | Async replay via events |
| **Sync button** | Fetch now | **Trigger Garmin**, not fetch |

If you treat Garmin like Strava, you will **always get zero data**.

---

## Garmin Developer Portal (manual)

In **Garmin Endpoint Configuration Tool**, enable:

### MUST ENABLE

- **Activity Summary (CONNECT_ACTIVITY)** — Push or Ping (Push preferred)

### Optional (later)

- Activity Details (only if you need samples)
- Activity Files (FIT/TCX/GPX)

### Do not enable yet

- Training API
- Daily summaries
- Epoch summaries

> If CONNECT_ACTIVITY is not enabled, **nothing works**.

---

## Testing (do this first)

Use **Garmin Data Generator** (Garmin Portal → Tools → Data Generator → Activity).

**Expected:** POST hits webhook → activity stored → `/activities` returns data.

If this doesn’t work → **stop coding**, config is wrong.

---

## Code layout

- **backfill.py** — Trigger `GET /wellness-api/rest/backfill/activities` only. Never fetch summaries.
- **ingest.py** — Ingest activity summary from webhook payload. Dedupe by `activityId`, save. No fetch.
- **client.py** — `fetch_activity_detail` for **lazy** detail/samples only (e.g. user opens activity). No history fetch.
- **webhook_handlers.py** — Receive POST, persist event, enqueue ingest job.
- **samples.py** — Fetch details **only** when user needs GPS/HR samples.
