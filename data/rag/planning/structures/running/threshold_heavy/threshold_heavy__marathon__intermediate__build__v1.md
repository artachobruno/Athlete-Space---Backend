---
doc_type: plan_structure
philosophy_id: threshold_heavy

id: threshold_heavy__marathon__intermediate__build__v1
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

tags: [threshold, lactate_clearance, aerobic_strength]
requires: [durability_base]
prohibits: [injury_prone, novice]
---

```structure_spec
week_pattern:
  mon: easy
  tue: threshold
  wed: easy
  thu: threshold_or_marathon
  fri: easy
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
    - threshold
    - threshold_or_marathon

  long:
    - long

  easy:
    - easy

guards:
  beginner_max_hard_days: 1
  taper_days_to_race_le: 21
  taper_max_hard_days: 1

notes:
  intent: >
    Threshold-heavy marathon structure prioritizing aerobic strength and
    lactate clearance through frequent submaximal quality sessions.
    Threshold work dominates quality exposure while marathon-specific
    work is layered conservatively without increasing hard-day count.
```