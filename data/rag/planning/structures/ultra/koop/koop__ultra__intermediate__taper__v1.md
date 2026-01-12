---
doc_type: plan_structure
philosophy_id: koop

id: koop__ultra__intermediate__taper__v1
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

tags: [koop, taper, specificity, freshness]
requires: [durability_base]
prohibits: [injury_prone, novice]
---

```structure_spec
week_pattern:
  mon: easy
  tue: economy_light
  wed: easy
  thu: easy
  fri: easy
  sat: short_specific
  sun: race_day

rules:
  hard_days_max: 1
  no_consecutive_hard_days: true

  long_run:
    required_count: 1
    preferred_day: sat

session_groups:
  hard:
    - economy_light

  long:
    - short_specific

  easy:
    - easy

notes:
  intent: >
    Koop taper preserves race-specific movement economy and confidence
    while sharply reducing volume. No new fitness is introduced. The
    final specific session reinforces execution without compromising
    recovery.
```