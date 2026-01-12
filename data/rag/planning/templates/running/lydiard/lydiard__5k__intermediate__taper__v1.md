---
doc_type: session_template_pack
domain: running
philosophy_id: lydiard
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
        description_key: lydiard_easy_taper_v1
        kind: easy_continuous
        params: { easy_mi_range: [3.0, 7.0] }
        constraints: { intensity_minutes_max: 0 }
        tags: [fresh]

  - session_type: easy_plus_strides
    templates:
      - id: strides_taper_v1
        description_key: lydiard_strides_taper_v1
        kind: easy_with_strides
        params: { easy_mi_range: [3.0, 6.0], strides_count_range: [4, 6], stride_seconds_range: [15, 20], stride_recovery_seconds_range: [60, 90] }
        constraints: { strides_max: 6, intensity_minutes_max: 3 }
        tags: [sharpen]

  - session_type: threshold
    templates:
      - id: short_hill_touch_v1
        description_key: lydiard_short_hill_touch_v1
        kind: hill_circuits
        params: { warmup_mi_range: [1.5, 2.5], cooldown_mi_range: [1.0, 2.0], reps_range: [4, 8], rep_seconds_range: [30, 60], jogdown_seconds_range: [60, 120], intensity: "strong" }
        constraints: { hard_minutes_max: 18 }
        tags: [keep_touch]

  - session_type: vo2
    templates:
      - id: short_speed_touch_v1
        description_key: lydiard_short_speed_touch_v1
        kind: vo2_distance_reps
        params: { warmup_mi_range: [2.0, 3.0], cooldown_mi_range: [1.5, 2.5], rep_distance_m_range: [200, 400], reps_range: [6, 10], recovery_seconds_range: [90, 150], intensity: "I" }
        constraints: { hard_minutes_max: 18 }
        tags: [sharp]

  - session_type: long
    templates:
      - id: long_taper_v1
        description_key: lydiard_long_taper_v1
        kind: long_easy
        params: { long_mi_range: [7.0, 11.0], finish_pickup_mi_range: [0.0, 1.0], finish_intensity: "steady" }
        constraints: { intensity_minutes_max: 0 }
        tags: [fresh]
```
