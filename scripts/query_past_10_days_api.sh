#!/bin/bash
# REST API commands to query activities and planned sessions from past 10 days
# Replace YOUR_AUTH_TOKEN with your actual authentication token

BASE_URL="http://localhost:8000"  # Change to your API URL
AUTH_TOKEN="YOUR_AUTH_TOKEN"  # Replace with your actual token

# Calculate date range (past 10 days)
END_DATE=$(date +%Y-%m-%d)
START_DATE=$(date -v-9d +%Y-%m-%d 2>/dev/null || date -d "9 days ago" +%Y-%m-%d)

echo "Querying data from $START_DATE to $END_DATE (past 10 days)"
echo ""

# ============================================================================
# 1. Get activities from past 10 days
# ============================================================================
echo "=== ACTIVITIES (Past 10 Days) ==="
curl -X GET "${BASE_URL}/activities?start=${START_DATE}&end=${END_DATE}&limit=1000" \
  -H "Authorization: Bearer ${AUTH_TOKEN}" \
  -H "Content-Type: application/json" | jq '.'

echo ""
echo ""

# ============================================================================
# 2. Get calendar season data (includes planned sessions and activities with pairing)
# ============================================================================
echo "=== CALENDAR SEASON DATA (includes pairing info) ==="
curl -X GET "${BASE_URL}/calendar/season" \
  -H "Authorization: Bearer ${AUTH_TOKEN}" \
  -H "Content-Type: application/json" | jq '.'

echo ""
echo ""

# ============================================================================
# 3. Get today's calendar data (example for today)
# ============================================================================
echo "=== TODAY'S CALENDAR DATA ==="
curl -X GET "${BASE_URL}/calendar/today" \
  -H "Authorization: Bearer ${AUTH_TOKEN}" \
  -H "Content-Type: application/json" | jq '.'

echo ""
echo ""

# ============================================================================
# 4. Get week data (if you want a specific week)
# ============================================================================
# WEEK_START="2026-01-15"  # Adjust to your desired week start
# echo "=== WEEK DATA (Week starting ${WEEK_START}) ==="
# curl -X GET "${BASE_URL}/calendar/week?start=${WEEK_START}" \
#   -H "Authorization: Bearer ${AUTH_TOKEN}" \
#   -H "Content-Type: application/json" | jq '.'
