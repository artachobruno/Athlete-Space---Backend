---
doc_type: plan_structure
philosophy_id: norwegian

id: norwegian__5k__intermediate__taper__v1
domain: training_structure
category: running

race_types: [5k]
audience: intermediate
phase: taper

days_to_race_min: 0
days_to_race_max: 14

priority: 200
version: "1.0"
last_reviewed: "2026-01-11"

tags: [norwegian, taper, freshness]
requires: [durability_base]
prohibits: [injury_prone, novice]
---

```structure_spec
week_pattern:
  mon: easy
  tue: threshold_light
  wed: easy
  thu: easy_or_shakeout
  fri: easy_plus_strides
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

  easy:
    - easy
    - easy_plus_strides
    - easy_or_shakeout
    - pre_race_shakeout

notes:
  intent: >
    Norwegian race-week taper maintaining light threshold stimulus and
    neuromuscular sharpness while eliminating long runs and double
    sessions to maximize freshness.
```