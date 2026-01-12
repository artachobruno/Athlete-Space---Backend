---
doc_type: session_template_pack
domain: ultra
philosophy_id: durability_first
race_types: [ultra]
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
      - id: easy_durable_v1
        description_key: df_easy_durable_v1
        kind: easy_continuous
        params: { easy_mi_range: [4.0, 10.0] }
        constraints: { intensity_minutes_max: 0 }
        tags: [durability]

  - session_type: long
    templates:
      - id: long_easy_ultra_v1
        description_key: df_long_easy_ultra_v1
        kind: long_easy
        params: { long_mi_range: [12.0, 22.0], hike_breaks_minutes_range: [0, 20] }
        constraints: { intensity_minutes_max: 0 }
        tags: [time_on_feet]

  - session_type: back_to_back
    templates:
      - id: back_to_back_v1
        description_key: df_back_to_back_v1
        kind: back_to_back_long_runs
        params: { day1_long_mi_range: [10.0, 18.0], day2_long_mi_range: [12.0, 20.0], intensity: "easy" }
        constraints: { intensity_minutes_max: 0 }
        tags: [b2b]

  - session_type: hill_climb
    templates:
      - id: hiking_hills_v1
        description_key: df_hiking_hills_v1
        kind: hill_hike_repeats
        params: { warmup_mi_range: [1.0, 2.0], repeats_range: [4, 10], repeat_minutes_range: [3, 8], walkdown_minutes_range: [3, 8] }
        constraints: { hard_minutes_max: 40 }
        tags: [climbing]
```
