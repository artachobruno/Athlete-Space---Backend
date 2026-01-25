# Pairing backfill: fix "two cards per day" on Schedule

If the Schedule view shows **two cards per day** for the same workout (one planned, one activity), planned sessions and Strava activities are not linked in `session_links`. The calendar API hides **paired** activities and shows only the planned-session card; unpaired activities appear as a second card.

**Fix: run the pairing backfill** to create `SessionLink` rows for activities that match planned sessions. After that, the API will filter out those activities and you'll see a single card per workout.

## 1. Standard backfill (duration ±30%)

Try this first. Pairs by same user, date, sport, and **duration within ±30%**.

```bash
cd /path/to/Athlete-Space---Backend
# Dry run
python -m scripts.backfill_unpaired_activities --days 60
# Execute
python -m scripts.backfill_unpaired_activities --no-dry-run --days 60
```

Optional: `--user-id YOUR_USER_ID`, `--days N`.

If you see **all failures** with `duration_mismatch (no plans within ±30% duration)`, use **relaxed** mode instead.

## 2. Relaxed backfill (date + sport 1:1, no duration)

When standard backfill fails (e.g. planned vs actual duration differ too much), use `--relaxed`. Pairs when there is **exactly one** unpaired plan and **one** unpaired activity on the same day for the same sport. No duration check.

```bash
# Dry run
python -m scripts.backfill_unpaired_activities --relaxed --days 60
# Execute
python -m scripts.backfill_unpaired_activities --relaxed --no-dry-run --days 60
```

Same `--user-id` and `--days` options apply.

## 3. Repair broken pairings

If pairings were lost or incorrect:

```bash
python -m scripts.repair_unpaired_activities --no-dry-run
```

## Notes

- **Standard** backfill uses auto-pairing (date, sport, ±30% duration). Links use status `proposed`.
- **Relaxed** backfill uses date+sport 1:1 only. Links use status `confirmed`, method `manual`.
- The calendar treats `proposed` and `confirmed` as paired; paired activities are hidden (single card per workout).
- Only **unpaired** activities are processed. Ensure DB is migrated and `session_links` exists.
