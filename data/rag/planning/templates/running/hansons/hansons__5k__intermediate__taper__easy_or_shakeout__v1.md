---
doc_type: session_template_set
domain: running
philosophy_id: hansons

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
  description_key: hansons_easy_or_shakeout_taper_v1
  kind: easy_continuous
  params:
    easy_mi_range:
    - 2.0
    - 5.0
  constraints:
    intensity_minutes_max: 0
    total_duration_max: 45
  tags:
  - freshness
  - recovery
  - optional_shakeout
```
