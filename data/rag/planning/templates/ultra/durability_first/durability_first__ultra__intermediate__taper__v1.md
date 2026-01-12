---
doc_type: session_template_pack
domain: ultra
philosophy_id: durability_first
race_types: [ultra]
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
        description_key: df_easy_taper_v1
        kind: easy_continuous
        params: { easy_mi_range: [3.0, 7.0] }
        constraints: { intensity_minutes_max: 0 }
        tags: [fresh]

  - session_type: long
    templates:
      - id: long_taper_v1
        description_key: df_long_taper_v1
        kind: long_easy
        params: { long_mi_range: [8.0, 12.0], hike_breaks_minutes_range: [0, 10] }
        constraints: { intensity_minutes_max: 0 }
        tags: [fresh]

  - session_type: back_to_back
    templates:
      - id: b2b_taper_v1
        description_key: df_b2b_taper_v1
        kind: back_to_back_long_runs
        params: { day1_long_mi_range: [6.0, 10.0], day2_long_mi_range: [7.0, 11.0], intensity: "easy" }
        constraints: { intensity_minutes_max: 0 }
        tags: [keep_touch]

  - session_type: hill_climb
    templates:
      - id: hills_touch_v1
        description_key: df_hills_touch_v1
        kind: hill_hike_repeats
        params: { warmup_mi_range: [1.0, 2.0], repeats_range: [3, 6], repeat_minutes_range: [2, 5], walkdown_minutes_range: [2, 5] }
        constraints: { hard_minutes_max: 20 }
        tags: [sharp]
```
