---
doc_type: session_template_set
domain: running
philosophy_id: daniels

race_types: [5k]
audience: intermediate
phase: taper
session_type: race_day

priority: 100
version: "1.0"
last_reviewed: "2026-01-12"
---

```template_spec
templates:
  - id: race_5k_v1
    description_key: daniels_race_5k_v1
    kind: race
    params:
      race_distance_km: 5.0
      warmup_mi_range: [1.0, 2.0]
      cooldown_mi_range: [0.5, 1.5]
      race_intensity: "R"
    constraints:
      total_duration_max: 60
    tags: [race, 5k, target_effort]

  - id: race_5k_with_prep_v1
    description_key: daniels_race_5k_with_prep_v1
    kind: race
    params:
      race_distance_km: 5.0
      warmup_mi_range: [1.5, 2.5]
      cooldown_mi_range: [1.0, 2.0]
      race_intensity: "R"
      strides_pre_race_range: [2, 4]
    constraints:
      total_duration_max: 75
    tags: [race, 5k, prepared]
```
