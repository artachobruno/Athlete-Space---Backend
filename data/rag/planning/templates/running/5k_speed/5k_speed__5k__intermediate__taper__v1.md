---
doc_type: session_template_pack
domain: running
philosophy_id: 5k_speed
race_types: [5k]
audience: intermediate
phase: taper
priority: 100
version: "1.0"
last_reviewed: "2026-01-12"
---

```template_sets
sets:
  - session_type: easy
    templates:
      - id: easy_taper_v1
        description_key: 5k_speed_easy_taper_v1
        kind: easy_continuous
        params: { easy_mi_range: [3.0, 6.0] }
        constraints: { intensity_minutes_max: 0 }
        tags: [fresh]

  - session_type: easy_plus_strides
    templates:
      - id: easy_strides_taper_v1
        description_key: 5k_speed_easy_strides_taper_v1
        kind: easy_with_strides
        params: { easy_mi_range: [3.0, 5.5], strides_count_range: [4, 6], stride_seconds_range: [12, 18], stride_recovery_seconds_range: [60, 90] }
        constraints: { strides_max: 6, intensity_minutes_max: 3 }
        tags: [sharpen]

  - session_type: threshold
    templates:
      - id: touch_T_v1
        description_key: 5k_speed_touch_T_v1
        kind: cruise_intervals
        params: { warmup_mi_range: [1.5, 2.5], cooldown_mi_range: [1.0, 2.0], reps_range: [2, 4], rep_minutes_range: [4, 6], float_minutes_range: [1, 2], intensity: "T" }
        constraints: { total_T_minutes_range: [10, 18], hard_minutes_max: 22 }
        tags: [keep_touch]

  - session_type: vo2
    templates:
      - id: sharpen_reps_v1
        description_key: 5k_speed_sharpen_reps_v1
        kind: vo2_distance_reps
        params: { warmup_mi_range: [2.0, 3.0], cooldown_mi_range: [1.5, 2.5], rep_distance_m_range: [200, 400], reps_range: [6, 10], recovery_seconds_range: [75, 150], intensity: "I" }
        constraints: { hard_minutes_max: 18 }
        tags: [sharp]

  - session_type: long
    templates:
      - id: long_taper_v1
        description_key: 5k_speed_long_taper_v1
        kind: long_easy
        params: { long_mi_range: [6.0, 9.0], finish_pickup_mi_range: [0.0, 1.0], finish_intensity: "steady" }
        constraints: { intensity_minutes_max: 0 }
        tags: [fresh]
```
