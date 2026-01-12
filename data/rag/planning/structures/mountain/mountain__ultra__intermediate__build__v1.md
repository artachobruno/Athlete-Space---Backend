---
doc_type: plan_structure
philosophy_id: mountain

id: mountain__ultra__intermediate__build__v1
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

tags: [mountain, vertical_gain, climbing, descending, durability]
requires: [durability_base]
prohibits: [injury_prone, novice]
---

```structure_spec
week_pattern:
  mon: easy
  tue: uphill_strength_or_hike
  wed: easy
  thu: downhill_economy_or_technical
  fri: easy
  sat: long_mountain
  sun: long_back_to_back_hike

rules:
  hard_days_max: 2
  no_consecutive_hard_days: true

  long_run:
    required_count: 2
    preferred_days: [sat, sun]

session_groups:
  hard:
    - uphill_strength_or_hike
    - downhill_economy_or_technical

  long:
    - long_mountain
    - long_back_to_back_hike

  easy:
    - easy

guards:
  beginner_max_hard_days: 1
  taper_days_to_race_le: 28
  taper_max_hard_days: 1

notes:
  intent: >
    Mountain ultra structure prioritizing vertical climbing strength,
    downhill resilience, hiking efficiency, and connective tissue
    durability. Running economy is secondary to terrain mastery.
    Back-to-back long efforts and vertical stress are structural.
```