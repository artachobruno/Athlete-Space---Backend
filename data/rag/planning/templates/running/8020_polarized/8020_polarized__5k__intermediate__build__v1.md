---
doc_type: session_template_pack
domain: running
philosophy_id: 8020_polarized
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
        description_key: 8020_easy_aerobic_v1
        kind: easy_continuous
        params: { easy_mi_range: [4.0, 10.0] }
        constraints: { intensity_minutes_max: 0 }
        tags: [z2]

  - session_type: easy_plus_strides
    templates:
      - id: easy_strides_v1
        description_key: 8020_easy_strides_v1
        kind: easy_with_strides
        params: { easy_mi_range: [4.0, 8.0], strides_count_range: [4, 8], stride_seconds_range: [15, 22], stride_recovery_seconds_range: [60, 90] }
        constraints: { strides_max: 8, intensity_minutes_max: 4 }
        tags: [economy]

  - session_type: threshold
    templates:
      - id: light_threshold_v1
        description_key: 8020_light_threshold_v1
        kind: cruise_intervals
        params: { warmup_mi_range: [1.5, 3.0], cooldown_mi_range: [1.0, 2.5], reps_range: [2, 5], rep_minutes_range: [6, 10], float_minutes_range: [1, 3], intensity: "T" }
        constraints: { total_T_minutes_range: [16, 32], hard_minutes_max: 38 }
        tags: [threshold]

  - session_type: vo2
    templates:
      - id: high_intensity_block_v1
        description_key: 8020_high_intensity_block_v1
        kind: vo2_intervals
        params: { warmup_mi_range: [2.0, 3.5], cooldown_mi_range: [1.5, 3.0], reps_range: [4, 7], rep_minutes_range: [2, 4], recovery_minutes_range: [2, 4], intensity: "I" }
        constraints: { total_I_minutes_range: [10, 20], hard_minutes_max: 25 }
        tags: [polarized]

  - session_type: long
    templates:
      - id: long_easy_v1
        description_key: 8020_long_easy_v1
        kind: long_easy
        params: { long_mi_range: [9.0, 15.0], finish_pickup_mi_range: [0.0, 2.0], finish_intensity: "steady" }
        constraints: { intensity_minutes_max: 0 }
        tags: [aerobic]
```
