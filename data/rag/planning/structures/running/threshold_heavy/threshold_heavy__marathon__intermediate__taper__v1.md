---
doc_type: plan_structure
philosophy_id: threshold_heavy

id: threshold_heavy__marathon__intermediate__taper__v1
domain: training_structure
category: running

race_types: [marathon]
audience: intermediate
phase: taper

days_to_race_min: 0
days_to_race_max: 21

priority: 200
version: "1.0"
last_reviewed: "2026-01-11"

tags: [threshold, taper, marathon, freshness]
requires: [durability_base]
prohibits: [injury_prone, novice]
---

```structure_spec
week_pattern:
  mon: easy
  tue: threshold_light
  wed: easy
  thu: easy_or_marathon_touch
  fri: easy
  sat: pre_race_shakeout
  sun: race_day

rules:
  hard_days_max: 1
  no_consecutive_hard_days: true

  long_run:
    required_count: 0

session_groups:
  hard:
    - threshold_light

  easy:
    - easy
    - easy_or_marathon_touch
    - pre_race_shakeout

notes:
  intent: >
    Taper maintains aerobic sharpness through light threshold exposure
    while eliminating long runs and secondary quality sessions to
    maximize freshness and glycogen restoration.
```