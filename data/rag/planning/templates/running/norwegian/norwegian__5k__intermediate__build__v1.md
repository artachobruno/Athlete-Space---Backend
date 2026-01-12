---
doc_type: session_template_pack
domain: running
philosophy_id: norwegian
race_types: [5k]
audience: intermediate
phase: build
priority: 100
version: "1.0"
last_reviewed: "2026-01-12"
---

```template_sets
sets:
  - session_type: easy
    templates:
      - id: easy_aerobic_v1
        description_key: norwegian_easy_aerobic_v1
        kind: easy_continuous
        params: { easy_mi_range: [4.0, 9.0] }
        constraints: { intensity_minutes_max: 0 }
        tags: [aerobic]

  - session_type: easy_plus_strides
    templates:
      - id: easy_strides_v1
        description_key: norwegian_easy_strides_v1
        kind: easy_with_strides
        params: { easy_mi_range: [3.0, 7.0], strides_count_range: [4, 8], stride_seconds_range: [15, 20], stride_recovery_seconds_range: [60, 90] }
        constraints: { strides_max: 8, intensity_minutes_max: 4 }
        tags: [economy]

  - session_type: threshold
    templates:
      - id: double_threshold_day_v1
        description_key: norwegian_double_threshold_day_v1
        kind: double_threshold
        params: { warmup_mi_range: [1.0, 2.0], cooldown_mi_range: [1.0, 2.0], am_reps_range: [3, 5], am_rep_minutes_range: [6, 10], pm_reps_range: [3, 5], pm_rep_minutes_range: [5, 8], float_minutes_range: [1, 2], intensity: "T" }
        constraints: { total_T_minutes_range: [30, 55], hard_minutes_max: 65 }
        tags: [threshold, norwegian]

  - session_type: vo2
    templates:
      - id: controlled_vo2_v1
        description_key: norwegian_controlled_vo2_v1
        kind: vo2_intervals
        params: { warmup_mi_range: [2.0, 3.5], cooldown_mi_range: [1.5, 3.0], reps_range: [5, 8], rep_minutes_range: [2, 4], recovery_minutes_range: [2, 4], intensity: "I" }
        constraints: { total_I_minutes_range: [12, 22], hard_minutes_max: 26 }
        tags: [vo2]

  - session_type: long
    templates:
      - id: long_easy_v1
        description_key: norwegian_long_easy_v1
        kind: long_easy
        params: { long_mi_range: [9.0, 14.0], finish_pickup_mi_range: [0.0, 2.0], finish_intensity: "steady" }
        constraints: { intensity_minutes_max: 0 }
        tags: [aerobic]
```
