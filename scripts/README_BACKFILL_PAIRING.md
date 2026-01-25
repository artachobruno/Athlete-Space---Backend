# Pairing backfill: fix "two cards per day" on Schedule

If the Schedule view shows **two cards per day** for the same workout (one planned, one activity), planned sessions and Strava activities are not linked in `session_links`. The calendar API hides **paired** activities and shows only the planned-session card; unpaired activities appear as a second card.

**Fix: run the pairing backfill** to create `SessionLink` rows for activities that match planned sessions (same user, date, sport, similar duration). After that, the API will filter out those activities and you’ll see a single card per workout.

## 1. Dry run (no changes)

```bash
cd /path/to/Athlete-Space---Backend
python -m scripts.backfill_unpaired_activities
```

Optional: `--user-id YOUR_USER_ID` and/or `--days 30` to limit scope.

## 2. Execute backfill

```bash
python -m scripts.backfill_unpaired_activities --no-dry-run
```

With filters:

```bash
python -m scripts.backfill_unpaired_activities --no-dry-run --days 60
python -m scripts.backfill_unpaired_activities --no-dry-run --user-id "c_xxx"
```

## 3. Repair broken pairings

If pairings were lost or incorrect, use:

```bash
python -m scripts.repair_unpaired_activities --no-dry-run
```

Same `--user-id` and `--days` options apply.

## Notes

- Backfill uses the auto-pairing logic (date, sport, duration tolerance). Created links use status `proposed`; the calendar treats `proposed` and `confirmed` as paired.
- Only **unpaired** activities are processed. Already-linked activities are skipped.
- Ensure DB is migrated and `session_links` exists before running.
