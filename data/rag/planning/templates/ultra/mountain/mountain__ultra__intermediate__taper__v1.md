---
doc_type: session_template_pack
domain: ultra
philosophy_id: mountain
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
      - id: mountain_easy_taper_v1
        description_key: mountain_easy_taper_v1
        kind: easy_trail
        params: { easy_mi_range: [3.0, 7.0], terrain: "trail", vert_ft_range: [0, 500] }
        constraints: { intensity_minutes_max: 0 }
        tags: [fresh]

  - session_type: long
    templates:
      - id: mountain_long_taper_v1
        description_key: mountain_long_taper_v1
        kind: long_with_vert
        params: { long_mi_range: [8.0, 14.0], vert_ft_range: [500, 2000], hike_breaks_minutes_range: [0, 20], downhill_focus: "controlled" }
        constraints: { intensity_minutes_max: 0 }
        tags: [fresh, keep_rhythm]

  - session_type: back_to_back
    templates:
      - id: mountain_b2b_taper_v1
        description_key: mountain_b2b_taper_v1
        kind: back_to_back_long_runs
        params: { day1_long_mi_range: [7.0, 11.0], day2_long_mi_range: [8.0, 12.0], intensity: "easy", vert_ft_range: [300, 1500] }
        constraints: { intensity_minutes_max: 0 }
        tags: [keep_touch]

  - session_type: hill_climb
    templates:
      - id: mountain_climb_touch_v1
        description_key: mountain_climb_touch_v1
        kind: sustained_climb_repeats
        params: { warmup_mi_range: [1.0, 2.0], repeats_range: [2, 4], repeat_minutes_range: [6, 10], jogdown_minutes_range: [4, 8], intensity: "strong" }
        constraints: { hard_minutes_max: 25 }
        tags: [sharp]

  - session_type: downhill
    templates:
      - id: mountain_downhill_touch_v1
        description_key: mountain_downhill_touch_v1
        kind: downhill_repeats
        params: { warmup_mi_range: [1.0, 2.0], repeats_range: [3, 6], descent_seconds_range: [30, 60], walkup_seconds_range: [90, 150], effort: "controlled_fast" }
        constraints: { hard_minutes_max: 18 }
        tags: [sharp, form]
```
