---
doc_type: session_template_pack
domain: ultra
philosophy_id: mountain
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
      - id: mountain_easy_trail_v1
        description_key: mountain_easy_trail_v1
        kind: easy_trail
        params: { easy_mi_range: [4.0, 10.0], terrain: "trail", vert_ft_range: [0, 800] }
        constraints: { intensity_minutes_max: 0 }
        tags: [trail, aerobic]

  - session_type: long
    templates:
      - id: mountain_long_vert_v1
        description_key: mountain_long_vert_v1
        kind: long_with_vert
        params: { long_mi_range: [14.0, 26.0], vert_ft_range: [1000, 5000], hike_breaks_minutes_range: [10, 40], downhill_focus: "controlled" }
        constraints: { intensity_minutes_max: 0 }
        tags: [vert, durability, descending]

  - session_type: back_to_back
    templates:
      - id: mountain_b2b_vert_v1
        description_key: mountain_b2b_vert_v1
        kind: back_to_back_long_runs
        params: { day1_long_mi_range: [12.0, 20.0], day2_long_mi_range: [14.0, 22.0], intensity: "easy", vert_ft_range: [800, 3500] }
        constraints: { intensity_minutes_max: 0 }
        tags: [b2b, vert, fatigue_resistance]

  - session_type: hill_climb
    templates:
      - id: mountain_long_climbs_v1
        description_key: mountain_long_climbs_v1
        kind: sustained_climb_repeats
        params: { warmup_mi_range: [1.0, 2.5], repeats_range: [3, 7], repeat_minutes_range: [6, 15], jogdown_minutes_range: [4, 12], intensity: "strong" }
        constraints: { hard_minutes_max: 55 }
        tags: [climbing, strength, trail_specific]

  - session_type: downhill
    templates:
      - id: mountain_downhill_tolerance_v1
        description_key: mountain_downhill_tolerance_v1
        kind: downhill_repeats
        params: { warmup_mi_range: [1.0, 2.0], repeats_range: [5, 10], descent_seconds_range: [30, 90], walkup_seconds_range: [90, 180], effort: "controlled_fast" }
        constraints: { hard_minutes_max: 35 }
        tags: [eccentric, descending, form]
```
