---
doc_type: session_template_pack
domain: ultra
philosophy_id: koop
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
      - id: koop_easy_taper_v1
        description_key: koop_easy_taper_v1
        kind: easy_continuous
        params: { easy_mi_range: [3.0, 7.0] }
        constraints: { intensity_minutes_max: 0 }
        tags: [fresh]

  - session_type: long
    templates:
      - id: koop_long_taper_v1
        description_key: koop_long_taper_v1
        kind: long_time_on_feet
        params: { long_mi_range: [8.0, 14.0], hike_breaks_minutes_range: [0, 15], fueling_practice: true }
        constraints: { intensity_minutes_max: 0 }
        tags: [fresh, keep_rhythm]

  - session_type: back_to_back
    templates:
      - id: koop_b2b_taper_v1
        description_key: koop_b2b_taper_v1
        kind: back_to_back_long_runs
        params: { day1_long_mi_range: [7.0, 11.0], day2_long_mi_range: [8.0, 12.0], intensity: "easy" }
        constraints: { intensity_minutes_max: 0 }
        tags: [keep_touch]

  - session_type: hill_climb
    templates:
      - id: koop_hill_touch_v1
        description_key: koop_hill_touch_v1
        kind: hill_strength_repeats
        params: { warmup_mi_range: [1.0, 2.0], repeats_range: [3, 6], repeat_minutes_range: [2, 5], downhill_focus: "controlled", walkdown_minutes_range: [2, 5] }
        constraints: { hard_minutes_max: 22 }
        tags: [sharp]

  - session_type: steady
    templates:
      - id: koop_steady_touch_v1
        description_key: koop_steady_touch_v1
        kind: steady_state
        params: { warmup_mi_range: [1.5, 2.5], cooldown_mi_range: [1.0, 2.0], steady_minutes_range: [15, 25], intensity: "steady" }
        constraints: { hard_minutes_max: 28 }
        tags: [keep_touch]
```
