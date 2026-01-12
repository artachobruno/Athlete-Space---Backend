---
doc_type: session_template_set
domain: running
philosophy_id: daniels

race_types: [5k]
audience: intermediate
phase: taper
session_type: pre_race_shakeout

priority: 100
version: "1.0"
last_reviewed: "2026-01-12"
---

```template_spec
templates:
  - id: pre_race_shakeout_v1
    description_key: daniels_pre_race_shakeout_v1
    kind: easy_continuous
    params:
      easy_mi_range: [1.0, 3.0]
    constraints:
      intensity_minutes_max: 0
      total_duration_max: 25
    tags: [pre_race, shakeout, very_light, activation]

  - id: pre_race_shakeout_with_strides_v1
    description_key: daniels_pre_race_shakeout_with_strides_v1
    kind: easy_with_strides
    params:
      easy_mi_range: [1.0, 2.5]
      strides_count_range: [2, 4]
      stride_seconds_range: [10, 15]
      stride_recovery_seconds_range: [60, 90]
    constraints:
      intensity_minutes_max: 2
      total_duration_max: 30
      strides_max: 4
    tags: [pre_race, activation, neuromuscular]
```
