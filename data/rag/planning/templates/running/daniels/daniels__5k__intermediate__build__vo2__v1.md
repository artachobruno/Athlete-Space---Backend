---
doc_type: session_template_set
domain: running
philosophy_id: daniels

race_types: [5k]
audience: intermediate
phase: build
session_type: vo2

priority: 100
version: "1.0"
last_reviewed: "2026-01-12"
---

```template_spec
templates:
  - id: vo2_intervals_3to5min_v1
    description_key: daniels_vo2_intervals_3to5min_v1
    kind: vo2_intervals
    params:
      warmup_mi_range: [2.0, 3.5]
      cooldown_mi_range: [1.5, 3.0]
      reps_range: [4, 7]
      rep_minutes_range: [3, 5]
      recovery_minutes_range: [2, 4]
      intensity: "I"
    constraints:
      total_I_minutes_range: [15, 28]
      hard_minutes_max: 32
    tags: [vo2, 5k]

  - id: vo2_400s_to_1k_v1
    description_key: daniels_vo2_400s_to_1k_v1
    kind: vo2_distance_reps
    params:
      warmup_mi_range: [2.0, 3.5]
      cooldown_mi_range: [1.5, 3.0]
      rep_distance_m_range: [400, 1000]
      reps_range: [5, 10]
      recovery_seconds_range: [90, 180]
      intensity: "I"
    constraints:
      hard_minutes_max: 30
    tags: [vo2, economy]
```
