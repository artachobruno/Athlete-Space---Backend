---
doc_type: session_template_set
domain: running
philosophy_id: 5k_speed

race_types: [5k]
audience: intermediate
phase: build
session_type: vo2_or_speed

priority: 100
version: "1.0"
last_reviewed: "2026-01-12"
---

```template_spec
templates:
- id: vo2_or_speed_v1
  description_key: 5k_speed_vo2_or_speed_v1
  kind: vo2_intervals
  params:
    warmup_mi_range:
    - 1.5
    - 3.0
    cooldown_mi_range:
    - 1.0
    - 3.0
    reps_range:
    - 4
    - 8
    rep_minutes_range:
    - 1
    - 3
    recovery_minutes_range:
    - 2
    - 4
    intensity: I
  constraints:
    total_I_minutes_range:
    - 10
    - 20
    hard_minutes_max: 25
  tags:
  - vo2
  - speed
```
