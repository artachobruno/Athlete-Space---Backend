---
doc_type: session_template_set
domain: running
philosophy_id: daniels

race_types: [5k]
audience: intermediate
phase: taper
session_type: easy_or_shakeout

priority: 100
version: "1.0"
last_reviewed: "2026-01-12"
---

```template_spec
templates:
  - id: easy_or_shakeout_taper_v1
    description_key: daniels_easy_or_shakeout_taper_v1
    kind: easy_continuous
    params:
      easy_mi_range: [2.0, 5.0]
    constraints:
      intensity_minutes_max: 0
      total_duration_max: 45
    tags: [freshness, recovery, optional_shakeout]

  - id: shakeout_short_v1
    description_key: daniels_shakeout_short_v1
    kind: easy_continuous
    params:
      easy_mi_range: [1.5, 3.0]
    constraints:
      intensity_minutes_max: 0
      total_duration_max: 30
    tags: [shakeout, very_light, freshness]
```
