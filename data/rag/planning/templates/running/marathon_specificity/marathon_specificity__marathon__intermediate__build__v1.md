---
doc_type: session_template_pack
domain: running
philosophy_id: marathon_specificity
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
        description_key: ms_easy_aerobic_v1
        kind: easy_continuous
        params: { easy_mi_range: [4.0, 10.0] }
        constraints: { intensity_minutes_max: 0 }
        tags: [aerobic]

  - session_type: easy_plus_strides
    templates:
      - id: easy_strides_v1
        description_key: ms_easy_strides_v1
        kind: easy_with_strides
        params: { easy_mi_range: [4.0, 8.0], strides_count_range: [4, 8], stride_seconds_range: [15, 22], stride_recovery_seconds_range: [60, 90] }
        constraints: { strides_max: 8, intensity_minutes_max: 4 }
        tags: [economy]

  - session_type: threshold
    templates:
      - id: marathon_pace_blocks_v1
        description_key: ms_marathon_pace_blocks_v1
        kind: marathon_pace_blocks
        params: { warmup_mi_range: [2.0, 4.0], cooldown_mi_range: [1.0, 3.0], blocks_range: [2, 4], block_mi_range: [2.0, 5.0], float_mi_range: [0.5, 1.5], intensity: "MP" }
        constraints: { total_MP_mi_range: [6.0, 14.0], hard_minutes_max: 75 }
        tags: [marathon_specific]

  - session_type: vo2
    templates:
      - id: aerobic_power_touch_v1
        description_key: ms_aerobic_power_touch_v1
        kind: vo2_intervals
        params: { warmup_mi_range: [2.0, 3.5], cooldown_mi_range: [1.5, 3.0], reps_range: [4, 7], rep_minutes_range: [2, 4], recovery_minutes_range: [2, 4], intensity: "I" }
        constraints: { total_I_minutes_range: [10, 18], hard_minutes_max: 22 }
        tags: [support]

  - session_type: long
    templates:
      - id: long_with_mp_finish_v1
        description_key: ms_long_with_mp_finish_v1
        kind: long_with_mp_finish
        params: { long_mi_range: [12.0, 20.0], mp_finish_mi_range: [3.0, 8.0], intensity: "MP" }
        constraints: { hard_minutes_max: 90 }
        tags: [marathon_specific]
```
