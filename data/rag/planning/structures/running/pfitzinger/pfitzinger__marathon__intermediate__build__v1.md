---
doc_type: plan_structure
philosophy_id: pfitzinger

id: pfitzinger__marathon__intermediate__build__v1
domain: training_structure
category: running

race_types: [marathon]
audience: intermediate
phase: build

days_to_race_min: 28
days_to_race_max: 140

priority: 100
version: "1.0"
last_reviewed: "2026-01-11"

tags: [pfitzinger, lactate_threshold, marathon_specific, aerobic_endurance]
requires: [durability_base]
prohibits: [injury_prone, novice]
---
```structure_spec
week_pattern:
  mon: recovery
  tue: threshold
  wed: medium_long
  thu: easy
  fri: easy
  sat: marathon_specific_or_progression
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
    - marathon_specific_or_progression

  long:
    - long

  medium_long:
    - medium_long

  easy:
    - easy
    - recovery

guards:
  beginner_max_hard_days: 1
  taper_days_to_race_le: 21
  taper_max_hard_days: 1

notes:
  intent: >
    Classic Pfitzinger-style marathon structure emphasizing cumulative
    aerobic stress via medium-long runs, frequent long runs, and
    sustained lactate-threshold work. Marathon-specific sessions are
    layered late in the build without increasing hard-day count.
```