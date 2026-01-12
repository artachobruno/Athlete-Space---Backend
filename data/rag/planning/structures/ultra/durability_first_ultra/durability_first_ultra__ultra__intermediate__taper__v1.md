---
doc_type: plan_structure
philosophy_id: durability_first_ultra

id: durability_first_ultra__ultra__intermediate__taper__v1
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

tags: [durability, taper, freshness, ultra]
requires: [injury_free]
prohibits: [injury_prone, novice]
---

```structure_spec
week_pattern:
  mon: easy
  tue: aerobic_steady_light
  wed: easy
  thu: easy
  fri: easy
  sat: short_long
  sun: race_day

rules:
  hard_days_max: 0
  no_consecutive_hard_days: true

  long_run:
    required_count: 1
    preferred_day: sat

session_groups:
  long:
    - short_long

  easy:
    - easy
    - aerobic_steady_light

notes:
  intent: >
    Ultra taper reduces volume while preserving movement economy and
    fatigue tolerance. No intensity is introduced. The final short long
    run maintains confidence without compromising recovery.
```