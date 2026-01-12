---
doc_type: session_template_pack
domain: running
philosophy_id: threshold_heavy
race_types: [marathon]
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
        description_key: th_easy_aerobic_v1
        kind: easy_continuous
        params: { easy_mi_range: [4.0, 10.0] }
        constraints: { intensity_minutes_max: 0 }
        tags: [aerobic]

  - session_type: easy_plus_strides
    templates:
      - id: easy_strides_v1
        description_key: th_easy_strides_v1
        kind: easy_with_strides
        params: { easy_mi_range: [4.0, 8.0], strides_count_range: [4, 8], stride_seconds_range: [15, 22], stride_recovery_seconds_range: [60, 90] }
        constraints: { strides_max: 8, intensity_minutes_max: 4 }
        tags: [economy]

  - session_type: threshold
    templates:
      - id: long_threshold_v1
        description_key: th_long_threshold_v1
        kind: cruise_intervals
        params: { warmup_mi_range: [2.0, 4.0], cooldown_mi_range: [1.0, 3.0], reps_range: [3, 6], rep_minutes_range: [8, 12], float_minutes_range: [1, 3], intensity: "T" }
        constraints: { total_T_minutes_range: [30, 55], hard_minutes_max: 65 }
        tags: [threshold]

  - session_type: vo2
    templates:
      - id: vo2_support_v1
        description_key: th_vo2_support_v1
        kind: vo2_intervals
        params: { warmup_mi_range: [2.0, 3.5], cooldown_mi_range: [1.5, 3.0], reps_range: [4, 7], rep_minutes_range: [2, 4], recovery_minutes_range: [2, 4], intensity: "I" }
        constraints: { total_I_minutes_range: [10, 16], hard_minutes_max: 20 }
        tags: [support]

  - session_type: long
    templates:
      - id: long_steady_v1
        description_key: th_long_steady_v1
        kind: long_with_steady_finish
        params: { long_mi_range: [14.0, 22.0], steady_finish_mi_range: [3.0, 7.0], finish_intensity: "steady" }
        constraints: { hard_minutes_max: 95 }
        tags: [endurance]
```
