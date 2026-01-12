---
doc_type: session_template_set
domain: running
philosophy_id: 5k_speed

race_types: [5k]
audience: intermediate
phase: build
session_type: threshold_or_speed_endurance

priority: 100
version: "1.0"
last_reviewed: "2026-01-12"
---

```template_spec
templates:
- id: threshold_or_speed_endurance_v1
  description_key: 5k_speed_threshold_or_speed_endurance_v1
  kind: cruise_intervals
  params:
    warmup_mi_range:
    - 1.5
    - 3.0
    cooldown_mi_range:
    - 1.0
    - 3.0
    reps_range:
    - 3
    - 6
    rep_minutes_range:
    - 3
    - 8
    float_minutes_range:
    - 1
    - 2
    intensity: I
  constraints:
    total_T_minutes_range:
    - 15
    - 30
    hard_minutes_max: 35
  tags:
  - threshold
  - speed
```
