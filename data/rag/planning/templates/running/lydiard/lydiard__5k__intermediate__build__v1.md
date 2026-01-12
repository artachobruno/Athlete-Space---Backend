---
doc_type: session_template_pack
domain: running
philosophy_id: lydiard
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
      - id: aerobic_base_v1
        description_key: lydiard_aerobic_base_v1
        kind: easy_continuous
        params: { easy_mi_range: [5.0, 11.0] }
        constraints: { intensity_minutes_max: 0 }
        tags: [aerobic]

  - session_type: easy_plus_strides
    templates:
      - id: easy_strides_v1
        description_key: lydiard_easy_strides_v1
        kind: easy_with_strides
        params: { easy_mi_range: [4.0, 9.0], strides_count_range: [4, 8], stride_seconds_range: [15, 22], stride_recovery_seconds_range: [60, 90] }
        constraints: { strides_max: 8, intensity_minutes_max: 4 }
        tags: [economy]

  - session_type: threshold
    templates:
      - id: hill_circuits_v1
        description_key: lydiard_hill_circuits_v1
        kind: hill_circuits
        params: { warmup_mi_range: [2.0, 3.0], cooldown_mi_range: [1.0, 2.5], reps_range: [6, 12], rep_seconds_range: [30, 75], jogdown_seconds_range: [60, 120], intensity: "strong" }
        constraints: { hard_minutes_max: 30 }
        tags: [hills, strength]

  - session_type: vo2
    templates:
      - id: anaerobic_reps_v1
        description_key: lydiard_anaerobic_reps_v1
        kind: vo2_distance_reps
        params: { warmup_mi_range: [2.0, 3.5], cooldown_mi_range: [1.5, 3.0], rep_distance_m_range: [200, 600], reps_range: [8, 14], recovery_seconds_range: [75, 150], intensity: "I" }
        constraints: { hard_minutes_max: 26 }
        tags: [speed]

  - session_type: long
    templates:
      - id: long_aerobic_v1
        description_key: lydiard_long_aerobic_v1
        kind: long_easy
        params: { long_mi_range: [10.0, 16.0], finish_pickup_mi_range: [0.0, 2.0], finish_intensity: "steady" }
        constraints: { intensity_minutes_max: 0 }
        tags: [aerobic]
```
