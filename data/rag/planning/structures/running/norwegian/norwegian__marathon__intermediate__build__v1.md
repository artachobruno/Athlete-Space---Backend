---
doc_type: plan_structure
philosophy_id: norwegian

id: norwegian__marathon__intermediate__build__v1
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

tags: [norwegian, threshold, marathon_specific, aerobic_volume]
requires: [durability_base]
prohibits: [injury_prone, novice]
---

```structure_spec
week_pattern:
  mon: easy
  tue: threshold_double
  wed: easy
  thu: easy
  fri: threshold_double_or_marathon
  sat: easy
  sun: long_progressive

rules:
  hard_days_max: 2
  no_consecutive_hard_days: true

  long_run:
    required_count: 1
    preferred_day: sun

session_groups:
  hard:
    - threshold_double
    - threshold_double_or_marathon

  long:
    - long_progressive

  easy:
    - easy

guards:
  beginner_max_hard_days: 1
  taper_days_to_race_le: 21
  taper_max_hard_days: 1

notes:
  intent: >
    Norwegian marathon structure emphasizing high aerobic volume and
    frequent controlled threshold exposure via double-threshold days.
    Marathon-specific work is layered cautiously without replacing
    aerobic durability or threshold consistency.
```