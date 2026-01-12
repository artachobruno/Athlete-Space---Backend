---
doc_type: plan_structure
philosophy_id: norwegian

id: norwegian__marathon__intermediate__taper__v1
domain: training_structure
category: running

race_types: [marathon]
audience: intermediate
phase: taper

days_to_race_min: 0
days_to_race_max: 21

priority: 200
version: "1.0"
last_reviewed: "2026-01-11"

tags: [norwegian, taper, freshness, marathon]
requires: [durability_base]
prohibits: [injury_prone, novice]
---

```structure_spec
week_pattern:
  mon: easy
  tue: threshold_light
  wed: easy
  thu: easy_or_marathon_touch
  fri: easy
  sat: pre_race_shakeout
  sun: race_day

rules:
  hard_days_max: 1
  no_consecutive_hard_days: true

  long_run:
    required_count: 0

session_groups:
  hard:
    - threshold_light
    - marathon_touch

  easy:
    - easy
    - easy_or_marathon_touch
    - pre_race_shakeout

notes:
  intent: >
    Marathon taper preserving threshold efficiency and light marathon
    rhythm while sharply reducing volume, eliminating double sessions,
    and prioritizing neuromuscular freshness.
```