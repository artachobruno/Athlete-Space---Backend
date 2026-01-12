---
doc_type: session_template_set
domain: running
philosophy_id: pfitzinger

race_types: [marathon]
audience: intermediate
phase: build
session_type: medium_long

priority: 100
version: "1.0"
last_reviewed: "2026-01-12"
---

```template_spec
templates:
- id: medium_long_v1
  description_key: pfitzinger_medium_long_v1
  kind: long_easy
  params:
    long_mi_range:
    - 6.0
    - 10.0
    finish_pickup_mi_range:
    - 0.0
    - 1.5
    finish_intensity: steady
  constraints:
    intensity_minutes_max: 2
  tags:
  - medium
  - long
```
