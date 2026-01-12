---
doc_type: session_template_set
domain: running
philosophy_id: daniels

race_types: [5k]
audience: intermediate
phase: build
session_type: threshold

priority: 100
version: "1.0"
last_reviewed: "2026-01-12"
---

```template_spec
templates:
  - id: cruise_intervals_v1
    description_key: daniels_threshold_cruise_intervals_v1
    kind: cruise_intervals
    params:
      warmup_mi_range: [1.5, 3.0]
      cooldown_mi_range: [1.0, 3.0]
      reps_range: [3, 6]
      rep_minutes_range: [5, 10]
      float_minutes_range: [1, 3]
      intensity: "T"
    constraints:
      total_T_minutes_range: [20, 40]
      hard_minutes_max: 45
    tags: [threshold, vdot]

  - id: steady_T_block_v1
    description_key: daniels_threshold_steady_block_v1
    kind: steady_T_block
    params:
      warmup_mi_range: [1.5, 3.0]
      cooldown_mi_range: [1.0, 3.0]
      continuous_T_minutes_range: [18, 30]
      intensity: "T"
    constraints:
      total_T_minutes_range: [18, 30]
      hard_minutes_max: 35
    tags: [threshold, continuous]
```
