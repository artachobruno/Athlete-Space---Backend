---
doc_type: plan_structure
philosophy_id: daniels

id: daniels__5k__intermediate__build__v1
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

tags: [vdot, threshold, vo2, economy]
requires: [durability_base]
prohibits: [injury_prone, novice]
---

```structure_spec
week_pattern:
  mon: easy
  tue: threshold
  wed: easy
  thu: vo2
  fri: easy
  sat: easy_plus_strides
  sun: long

rules:
  hard_days_max: 2
  no_consecutive_hard_days: true

  long_run:
    required_count: 1
    preferred_day: sun

session_groups:
  hard:
    - threshold
    - vo2

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
    Daniels-style 5K structure with one threshold session (T) and one VO2
    session (I) per week, separated by recovery, plus a weekly long run
    to support aerobic development.
```