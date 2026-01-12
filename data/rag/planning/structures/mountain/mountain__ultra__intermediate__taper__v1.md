---
doc_type: plan_structure
philosophy_id: mountain

id: mountain__ultra__intermediate__taper__v1
domain: training_structure
category: running

race_types: [ultra]
audience: intermediate
phase: taper

days_to_race_min: 0
days_to_race_max: 28

priority: 200
version: "1.0"
last_reviewed: "2026-01-11"

tags: [mountain, taper, vertical, freshness]
requires: [durability_base]
prohibits: [injury_prone, novice]
---

```structure_spec
week_pattern:
  mon: easy
  tue: uphill_light
  wed: easy
  thu: easy_or_terrain_touch
  fri: easy
  sat: short_mountain
  sun: race_day

rules:
  hard_days_max: 1
  no_consecutive_hard_days: true

  long_run:
    required_count: 1
    preferred_day: sat

session_groups:
  hard:
    - uphill_light

  long:
    - short_mountain

  easy:
    - easy
    - easy_or_terrain_touch

notes:
  intent: >
    Mountain ultra taper preserves climbing rhythm, downhill confidence,
    and terrain familiarity while sharply reducing volume and mechanical
    stress. No intensity is introduced. Fresh legs and resilient joints
    are the priority.
```