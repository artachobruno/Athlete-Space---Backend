---
doc_type: plan_structure
philosophy_id: norwegian

id: norwegian__5k__intermediate__build__v1
domain: training_structure
category: running

race_types: [5k]
audience: intermediate
phase: build

days_to_race_min: 14
days_to_race_max: 70

priority: 100
version: "1.0"
last_reviewed: "2026-01-11"

tags: [norwegian, threshold, double_threshold, controlled_intensity]
requires: [durability_base]
prohibits: [injury_prone, novice]
---

```structure_spec
week_pattern:
  mon: easy
  tue: threshold_double
  wed: easy
  thu: easy
  fri: threshold_double
  sat: easy
  sun: long

rules:
  hard_days_max: 2
  no_consecutive_hard_days: true

  long_run:
    required_count: 1
    preferred_day: sun

session_groups:
  hard:
    - threshold_double

  long:
    - long

  easy:
    - easy

guards:
  beginner_max_hard_days: 1
  taper_days_to_race_le: 14
  taper_max_hard_days: 1

notes:
  intent: >
    Norwegian-style structure emphasizing two controlled double-threshold
    days per week, strict separation of hard days, extensive aerobic
    support, and a weekly long run to preserve durability and volume
    tolerance.
```