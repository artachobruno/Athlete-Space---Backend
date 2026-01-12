---
doc_type: session_template_pack
domain: running
philosophy_id: 5k_speed
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
      - id: easy_continuous_v1
        description_key: 5k_speed_easy_continuous_v1
        kind: easy_continuous
        params: { easy_mi_range: [3.0, 8.0] }
        constraints: { intensity_minutes_max: 0 }
        tags: [aerobic]

  - session_type: easy_plus_strides
    templates:
      - id: easy_strides_v1
        description_key: 5k_speed_easy_strides_v1
        kind: easy_with_strides
        params: { easy_mi_range: [3.0, 7.0], strides_count_range: [6, 10], stride_seconds_range: [12, 20], stride_recovery_seconds_range: [60, 90] }
        constraints: { strides_max: 10, intensity_minutes_max: 5 }
        tags: [speed, economy]

  - session_type: threshold
    templates:
      - id: short_threshold_v1
        description_key: 5k_speed_short_threshold_v1
        kind: cruise_intervals
        params: { warmup_mi_range: [1.5, 3.0], cooldown_mi_range: [1.0, 2.5], reps_range: [3, 6], rep_minutes_range: [4, 8], float_minutes_range: [1, 2], intensity: "T" }
        constraints: { total_T_minutes_range: [15, 30], hard_minutes_max: 35 }
        tags: [threshold]

  - session_type: vo2
    templates:
      - id: speed_reps_v1
        description_key: 5k_speed_speed_reps_v1
        kind: vo2_distance_reps
        params: { warmup_mi_range: [2.0, 3.5], cooldown_mi_range: [1.5, 3.0], rep_distance_m_range: [200, 600], reps_range: [8, 16], recovery_seconds_range: [60, 120], intensity: "I" }
        constraints: { hard_minutes_max: 28 }
        tags: [vo2, speed]

  - session_type: long
    templates:
      - id: long_easy_v1
        description_key: 5k_speed_long_easy_v1
        kind: long_easy
        params: { long_mi_range: [7.0, 12.0], finish_pickup_mi_range: [0.0, 1.5], finish_intensity: "steady" }
        constraints: { intensity_minutes_max: 0 }
        tags: [durability]
```
