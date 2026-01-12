---
doc_type: session_template_set
domain: running
philosophy_id: daniels

race_types: [5k]
audience: intermediate
phase: taper
session_type: threshold

priority: 100
version: "1.0"
last_reviewed: "2026-01-12"
---

```template_spec
templates:
  - id: cruise_intervals_taper_v1
    description_key: daniels_threshold_cruise_intervals_taper_v1
    kind: cruise_intervals
    params:
      warmup_mi_range: [1.5, 3.0]
      cooldown_mi_range: [1.0, 2.5]
      reps_range: [2, 4]
      rep_minutes_range: [5, 8]
      float_minutes_range: [1, 2]
      intensity: "T"
    constraints:
      total_T_minutes_range: [12, 22]
      hard_minutes_max: 25
    tags: [taper, keep_touch]
```
