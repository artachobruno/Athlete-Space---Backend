# Training Load Metrics Recalculation

## Overview

This migration script recalculates all training load metrics (CTL, ATL, TSB) for all users using the new TSS-based methodology. It eliminates stale/legacy data and ensures consistency across the system.

## What It Does

1. **Identifies all users** with activities in the database
2. **Deletes all existing** `DailyTrainingLoad` records for each user
3. **Recomputes metrics from scratch** using:
   - Raw activities as source of truth
   - TSS-based computation (`compute_activity_tss`, `compute_daily_tss_load`)
   - CTL/ATL/Form calculation (`compute_ctl_atl_form_from_tss`)
   - Date range: First activity date → Today
4. **Stores results** in `daily_training_load` table
5. **Validates** metrics for sample users

## Methodology

The script uses the **new TSS-based methodology** from `app/metrics/load_computation.py`:

- **TSS Calculation Priority:**
  1. Power-based TSS (cycling)
  2. Pace-based TSS (running/swimming)
  3. HR-based TRIMP → mapped to TSS
  4. Session-RPE → mapped to TSS

- **Metrics Computation:**
  - **CTL**: 42-day exponentially weighted moving average of daily TSS
  - **ATL**: 7-day exponentially weighted moving average of daily TSS
  - **TSB (Form)**: CTL[t-1] - ATL[t-1] (stored in `tsb` column for backward compatibility)

## Usage

### Dry Run (Recommended First)

Test what would be done without making changes:

```bash
python scripts/recalculate_training_load_metrics.py --dry-run
```

### Full Migration

Run the actual recalculation:

```bash
python scripts/recalculate_training_load_metrics.py
```

## Output

The script provides:

- Progress logging for each user
- Summary statistics:
  - Users processed
  - Users failed
  - Total records deleted
  - Total records created
- Validation results for first 2 users:
  - CTL/ATL not stuck near ±100
  - CTL/ATL are different (not symmetric)
  - TSB varies (not constant)
  - Recent metric values

## Safety Features

- **Dry run mode** to preview changes
- **Per-user processing** with error handling (one failure doesn't stop others)
- **Transaction safety** (commits per user, not all-or-nothing)
- **Validation checks** to ensure metrics are reasonable
- **Detailed logging** for audit trail

## Expected Results

After migration:

✅ All `DailyTrainingLoad` records use new methodology
✅ CTL and ATL are not symmetric
✅ Metrics track recent training volume
✅ TSB is negative during heavy training, positive after rest
✅ No legacy data remains

## Endpoints Affected

The following endpoints read from `DailyTrainingLoad`:

- `/training/state` - Current training state metrics
- `/training/signals` - Training signals and observations
- Coach services (via `get_training_data` in `app/state/api_helpers.py`)

**Note:** The `/state/training-load` endpoint computes metrics on-the-fly from activities. It may need a separate update to use the stored `DailyTrainingLoad` table for consistency, but that's outside the scope of this migration.

## Post-Migration Checklist

1. ✅ Run dry-run first to verify
2. ✅ Run full migration during off-peak hours
3. ✅ Check validation results in output
4. ✅ Verify dashboard charts show correct data
5. ✅ Check coach summaries are accurate
6. ✅ Hard refresh frontend to clear caches
7. ✅ Monitor for any anomalies in first 24 hours

## Troubleshooting

### No users found
- Check that activities table has data
- Verify `user_id` column is populated

### Validation failures
- Check that users have sufficient activity history (14+ days recommended)
- Verify TSS computation is working (check activity `raw_json` has required fields)

### Performance issues
- Script processes users sequentially
- For large datasets, consider running during maintenance window
- Each user commits separately to avoid long transactions

## Related Files

- `app/metrics/load_computation.py` - TSS computation logic
- `app/metrics/computation_service.py` - Incremental recomputation service
- `app/db/models.py` - `DailyTrainingLoad` model definition
- `app/api/training/state.py` - `/state/training-load` endpoint (on-the-fly computation)
- `app/api/training/training.py` - Endpoints that read from `DailyTrainingLoad`
