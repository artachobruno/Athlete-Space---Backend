---
doc_type: session_template_set
domain: running
philosophy_id: lydiard

race_types: [5k]
audience: intermediate
phase: build
session_type: long

priority: 100
version: "1.0"
last_reviewed: "2026-01-12"
---

```template_spec
templates:
- id: long_easy_v1
  description_key: lydiard_long_easy_v1
  kind: long_easy
  params:
    long_mi_range:
    - 8.0
    - 14.0
    finish_pickup_mi_range:
    - 0.0
    - 2.0
    finish_intensity: steady
  constraints:
    intensity_minutes_max: 0
  tags:
  - aerobic
  - durability
```
