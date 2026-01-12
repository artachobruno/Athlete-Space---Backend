---
doc_type: plan_structure
philosophy_id: lydiard

id: lydiard__5k__intermediate__taper__v1
domain: training_structure
category: running

race_types: [5k]
audience: intermediate
phase: taper

days_to_race_min: 0
days_to_race_max: 20

priority: 200
version: "1.0"
last_reviewed: "2026-01-11"

tags: [lydiard, taper, freshness]
requires: [durability_base]
prohibits: [injury_prone, novice]
---

```structure_spec
week_pattern:
  mon: easy
  tue: aerobic_plus_strides
  wed: easy
  thu: easy_or_light_fartlek
  fri: easy
  sat: easy_plus_strides
  sun: race_day

rules:
  hard_days_max: 0
  no_consecutive_hard_days: true

  long_run:
    required_count: 0

session_groups:
  easy:
    - easy
    - aerobic_plus_strides
    - easy_or_light_fartlek
    - easy_plus_strides

notes:
  intent: >
    Lydiard pre-competition taper preserving aerobic rhythm and
    neuromuscular sharpness while removing long runs and structured
    intensity to ensure freshness on race day.
```