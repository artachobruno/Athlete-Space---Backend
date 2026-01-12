---
doc_type: plan_structure
philosophy_id: koop

id: koop__ultra__intermediate__build__v1
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

tags: [koop, specificity, economy, fatigue_resistance]
requires: [durability_base]
prohibits: [injury_prone, novice]
---

```structure_spec
week_pattern:
  mon: easy
  tue: aerobic_steady_or_climb
  wed: easy
  thu: economy_or_specific
  fri: easy
  sat: long_specific
  sun: long_back_to_back

rules:
  hard_days_max: 2
  no_consecutive_hard_days: true

  long_run:
    required_count: 2
    preferred_days: [sat, sun]

session_groups:
  hard:
    - aerobic_steady_or_climb
    - economy_or_specific

  long:
    - long_specific
    - long_back_to_back

  easy:
    - easy

guards:
  beginner_max_hard_days: 1
  taper_days_to_race_le: 28
  taper_max_hard_days: 1

notes:
  intent: >
    Koop-style ultra structure prioritizing race-specific economy,
    terrain specificity, and fatigue resistance. Intensity is introduced
    only to the extent that it improves efficiency at expected race
    outputs. Back-to-back long runs and specific long sessions are
    structural.
```