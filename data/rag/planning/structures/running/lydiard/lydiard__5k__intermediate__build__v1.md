---
doc_type: plan_structure
philosophy_id: lydiard

id: lydiard__5k__intermediate__build__v1
domain: training_structure
category: running

race_types: [5k]
audience: intermediate
phase: build

days_to_race_min: 21
days_to_race_max: 90

priority: 100
version: "1.0"
last_reviewed: "2026-01-11"

tags: [lydiard, aerobic_base, hill_strength, economy]
requires: [durability_base]
prohibits: [injury_prone, novice]
---

```structure_spec
week_pattern:
  mon: easy
  tue: aerobic
  wed: easy
  thu: hill_strength_or_fartlek
  fri: easy
  sat: aerobic
  sun: long

rules:
  hard_days_max: 1
  no_consecutive_hard_days: true

  long_run:
    required_count: 1
    preferred_day: sun

session_groups:
  hard:
    - hill_strength_or_fartlek

  long:
    - long

  aerobic:
    - aerobic

  easy:
    - easy

guards:
  beginner_max_hard_days: 0
  taper_days_to_race_le: 14
  taper_max_hard_days: 0

notes:
  intent: >
    Lydiard-style base and early build structure emphasizing high-volume
    aerobic running, one hill or fartlek session for strength and economy,
    and a weekly long run as the cornerstone of endurance development.
```