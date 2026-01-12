---
doc_type: session_template_set
domain: running
philosophy_id: daniels

race_types: [5k]
audience: intermediate
phase: taper
session_type: vo2

priority: 100
version: "1.0"
last_reviewed: "2026-01-12"
---

```template_spec
templates:
  - id: vo2_sharpen_2to3min_v1
    description_key: daniels_vo2_sharpen_2to3min_v1
    kind: vo2_intervals
    params:
      warmup_mi_range: [2.0, 3.0]
      cooldown_mi_range: [1.5, 2.5]
      reps_range: [4, 6]
      rep_minutes_range: [2, 3]
      recovery_minutes_range: [2, 3]
      intensity: "I"
    constraints:
      total_I_minutes_range: [8, 16]
      hard_minutes_max: 18
    tags: [taper, sharp]
```
