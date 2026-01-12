---
doc_type: plan_structure
philosophy_id: 8020_polarized

id: 8020_polarized__5k__intermediate__build__v1
domain: training_structure
category: running

race_types: [5k]
audience: intermediate
phase: build

days_to_race_min: 11
days_to_race_max: 60

priority: 100
version: "1.0"
last_reviewed: "2026-01-11"

tags: [polarized, aerobic, vo2, efficiency]
requires: [durability_base]
prohibits: [injury_prone, novice]
---

```structure_spec
week_pattern:
  mon: easy
  tue: vo2_or_hill_reps
  wed: easy
  thu: easy
  fri: easy
  sat: easy_plus_strides
  sun: long

rules:
  hard_days_max: 1
  no_consecutive_hard_days: true

  long_run:
    required_count: 1
    preferred_day: sun

session_groups:
  hard:
    - vo2_or_hill_reps

  long:
    - long

  easy:
    - easy
    - easy_plus_strides

guards:
  beginner_max_hard_days: 1
  taper_days_to_race_le: 10
  taper_max_hard_days: 1

notes:
  intent: >
    Classic 80/20 polarized structure emphasizing high aerobic volume,
    a single high-intensity session per week, and a weekly long run.
    Threshold work is intentionally minimized to preserve polarization.
```