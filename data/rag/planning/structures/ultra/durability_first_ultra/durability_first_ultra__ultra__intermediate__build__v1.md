---
doc_type: plan_structure
philosophy_id: durability_first_ultra

id: durability_first_ultra__ultra__intermediate__build__v1
domain: training_structure
category: running

race_types: [ultra]
audience: intermediate
phase: build

days_to_race_min: 42
days_to_race_max: 210

priority: 100
version: "1.0"
last_reviewed: "2026-01-11"

tags: [durability, volume, fatigue_resistance, time_on_feet]
requires: [injury_free]
prohibits: [injury_prone, novice]
---

```structure_spec
week_pattern:
  mon: easy
  tue: aerobic_steady
  wed: easy
  thu: aerobic_steady_light
  fri: easy
  sat: long
  sun: long_back_to_back

rules:
  hard_days_max: 1
  no_consecutive_hard_days: true

  long_run:
    required_count: 2
    preferred_days: [sat, sun]

session_groups:
  hard:
    - aerobic_steady

  long:
    - long
    - long_back_to_back

  easy:
    - easy

guards:
  beginner_max_hard_days: 0
  taper_days_to_race_le: 28
  taper_max_hard_days: 0

notes:
  intent: >
    Durability-first ultra structure prioritizing total volume, time on
    feet, connective tissue resilience, and fatigue resistance.
    Intensity is intentionally suppressed. Back-to-back long runs
    develop late-race durability without high mechanical stress.
```