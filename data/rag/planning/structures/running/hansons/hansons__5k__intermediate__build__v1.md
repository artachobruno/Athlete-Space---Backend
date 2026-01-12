---
doc_type: plan_structure
philosophy_id: hansons

id: hansons__5k__intermediate__build__v1
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

tags: [hansons, cumulative_fatigue, threshold, vo2]
requires: [durability_base]
prohibits: [injury_prone, novice]
---

```structure_spec
week_pattern:
  mon: easy
  tue: speed_or_vo2
  wed: easy
  thu: threshold
  fri: easy
  sat: moderate_long
  sun: easy

rules:
  hard_days_max: 2
  no_consecutive_hard_days: true

  long_run:
    required_count: 0

session_groups:
  hard:
    - speed_or_vo2
    - threshold

  moderate_long:
    - moderate_long

  easy:
    - easy

guards:
  beginner_max_hard_days: 1
  taper_days_to_race_le: 10
  taper_max_hard_days: 1

notes:
  intent: >
    Hansons-style cumulative fatigue structure emphasizing two quality
    sessions per week, no traditional long run, and a moderate-long run
    to maintain endurance without excessive recovery cost.
```