---
doc_type: plan_structure
philosophy_id: 8020_polarized

id: 8020_polarized__5k__intermediate__taper__v1
domain: training_structure
category: running

race_types: [5k]
audience: intermediate
phase: taper

days_to_race_min: 0
days_to_race_max: 10

priority: 200
version: "1.0"
last_reviewed: "2026-01-11"

tags: [polarized, taper, freshness]
requires: [durability_base]
prohibits: [injury_prone, novice]
---

```structure_spec
week_pattern:
  mon: easy
  tue: vo2_light
  wed: easy
  thu: easy_or_shakeout
  fri: easy_plus_strides
  sat: pre_race_shakeout
  sun: race_day

rules:
  hard_days_max: 1
  no_consecutive_hard_days: true

  long_run:
    required_count: 0

session_groups:
  hard:
    - vo2_light

  easy:
    - easy
    - easy_plus_strides
    - easy_or_shakeout
    - pre_race_shakeout

notes:
  intent: >
    Race-week taper preserving polarized distribution by maintaining
    one light high-intensity stimulus while maximizing freshness and
    minimizing fatigue.
```