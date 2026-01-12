---
doc_type: session_template_pack
domain: ultra
philosophy_id: koop
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
      - id: koop_easy_aerobic_v1
        description_key: koop_easy_aerobic_v1
        kind: easy_continuous
        params: { easy_mi_range: [4.0, 10.0] }
        constraints: { intensity_minutes_max: 0 }
        tags: [aerobic, consistency]

  - session_type: long
    templates:
      - id: koop_long_time_on_feet_v1
        description_key: koop_long_time_on_feet_v1
        kind: long_time_on_feet
        params: { long_mi_range: [14.0, 26.0], hike_breaks_minutes_range: [5, 25], fueling_practice: true }
        constraints: { intensity_minutes_max: 0 }
        tags: [time_on_feet, fueling]

  - session_type: back_to_back
    templates:
      - id: koop_back_to_back_v1
        description_key: koop_back_to_back_v1
        kind: back_to_back_long_runs
        params: { day1_long_mi_range: [12.0, 20.0], day2_long_mi_range: [14.0, 22.0], intensity: "easy" }
        constraints: { intensity_minutes_max: 0 }
        tags: [b2b, fatigue_resistance]

  - session_type: hill_climb
    templates:
      - id: koop_hill_strength_v1
        description_key: koop_hill_strength_v1
        kind: hill_strength_repeats
        params: { warmup_mi_range: [1.0, 2.5], repeats_range: [5, 10], repeat_minutes_range: [3, 7], downhill_focus: "controlled", walkdown_minutes_range: [2, 6] }
        constraints: { hard_minutes_max: 45 }
        tags: [climbing, eccentric_tolerance]

  - session_type: steady
    templates:
      - id: koop_steady_state_v1
        description_key: koop_steady_state_v1
        kind: steady_state
        params: { warmup_mi_range: [1.5, 3.0], cooldown_mi_range: [1.0, 2.5], steady_minutes_range: [20, 50], intensity: "steady" }
        constraints: { hard_minutes_max: 55 }
        tags: [fatigue_resistance, aerobic_power]
```
