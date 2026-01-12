---
doc_type: session_template_set
domain: running
philosophy_id: daniels

race_types: [5k]
audience: intermediate
phase: taper
session_type: long

priority: 100
version: "1.0"
last_reviewed: "2026-01-12"
---

```template_spec
templates:
  - id: long_easy_taper_v1
    description_key: daniels_long_easy_taper_v1
    kind: long_easy
    params:
      long_mi_range: [6.0, 10.0]
      finish_pickup_mi_range: [0.0, 1.0]
      finish_intensity: "steady"
    constraints:
      intensity_minutes_max: 0
    tags: [taper, freshness]
```
