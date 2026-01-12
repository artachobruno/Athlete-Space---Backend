---
doc_type: session_template_set
domain: running
philosophy_id: daniels

race_types: [5k]
audience: intermediate
phase: build
session_type: easy

priority: 100
version: "1.0"
last_reviewed: "2026-01-12"
---

```template_spec
templates:
  - id: easy_continuous_v1
    description_key: daniels_easy_continuous_v1
    kind: easy_continuous
    params:
      warmup_mi_range: [0.0, 0.0]
      cooldown_mi_range: [0.0, 0.0]
      easy_mi_range: [3.0, 10.0]
    constraints:
      intensity_minutes_max: 0
    tags: [z2, aerobic]

  - id: easy_progression_v1
    description_key: daniels_easy_progression_v1
    kind: easy_progression
    params:
      easy_mi_range: [4.0, 10.0]
      finish_fast_mi_range: [0.5, 2.0]
      finish_intensity: "steady"   # not threshold
    constraints:
      intensity_minutes_max: 0
    tags: [aerobic, controlled]
```
