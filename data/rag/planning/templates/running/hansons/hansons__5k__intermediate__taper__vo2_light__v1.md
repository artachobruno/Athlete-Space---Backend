---
doc_type: session_template_set
domain: running
philosophy_id: hansons

race_types: [5k]
audience: intermediate
phase: taper
session_type: vo2_light

priority: 100
version: "1.0"
last_reviewed: "2026-01-12"
---

```template_spec
templates:
- id: vo2_light_sharpen_1to2min_v1
  description_key: hansons_vo2_light_sharpen_1to2min_v1
  kind: vo2_intervals
  params:
    warmup_mi_range:
    - 1.5
    - 2.5
    cooldown_mi_range:
    - 1.0
    - 2.0
    reps_range:
    - 3
    - 5
    rep_minutes_range:
    - 1
    - 2
    recovery_minutes_range:
    - 1
    - 2
    intensity: I
  constraints:
    total_I_minutes_range:
    - 4
    - 10
    hard_minutes_max: 12
  tags:
  - taper
  - light
  - sharp
```
