---
doc_type: session_template_set
domain: running
philosophy_id: threshold_heavy

race_types: [marathon]
audience: intermediate
phase: taper
session_type: threshold_light

priority: 100
version: "1.0"
last_reviewed: "2026-01-12"
---

```template_spec
templates:
- id: threshold_light_taper_v1
  description_key: threshold_heavy_threshold_light_taper_v1
  kind: cruise_intervals
  params:
    warmup_mi_range:
    - 1.5
    - 2.5
    cooldown_mi_range:
    - 1.0
    - 2.0
    reps_range:
    - 2
    - 4
    rep_minutes_range:
    - 4
    - 8
    float_minutes_range:
    - 1
    - 2
    intensity: T
  constraints:
    total_T_minutes_range:
    - 10
    - 20
    hard_minutes_max: 22
  tags:
  - taper
  - keep_touch
```
