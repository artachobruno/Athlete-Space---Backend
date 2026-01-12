---
doc_type: plan_structure
philosophy_id: marathon_specificity

id: marathon_specificity__marathon__intermediate__build__v1
domain: training_structure
category: running

race_types: [marathon]
audience: intermediate
phase: build

days_to_race_min: 21
days_to_race_max: 90

priority: 100
version: "1.0"
last_reviewed: "2026-01-11"

tags: [marathon, specificity, marathon_pace, endurance]
requires: [durability_base]
prohibits: [injury_prone, novice]
---

```structure_spec
week_pattern:
  mon: easy
  tue: marathon_pace
  wed: easy
  thu: threshold_or_steady
  fri: easy
  sat: medium_long
  sun: long

rules:
  hard_days_max: 2
  no_consecutive_hard_days: true

  long_run:
    required_count: 1
    preferred_day: sun

session_groups:
  hard:
    - marathon_pace
    - threshold_or_steady

  long:
    - long

  medium_long:
    - medium_long

  easy:
    - easy

guards:
  beginner_max_hard_days: 1
  taper_days_to_race_le: 21
  taper_max_hard_days: 1

notes:
  intent: >
    Marathon-specific structure emphasizing frequent exposure to
    marathon pace, a secondary steady/threshold stimulus, a medium-long
    run for aerobic durability, and a weekly long run as the primary
    endurance anchor.
```