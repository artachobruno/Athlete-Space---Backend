---
doc_type: session_template_set
domain: running
philosophy_id: norwegian

race_types: [marathon]
audience: intermediate
phase: build
session_type: long_progressive

priority: 100
version: "1.0"
last_reviewed: "2026-01-12"
---

```template_spec
templates:
- id: long_progressive_v1
  description_key: norwegian_long_progressive_v1
  kind: long_easy
  params:
    long_mi_range:
    - 8.0
    - 14.0
    finish_pickup_mi_range:
    - 2.0
    - 4.0
    finish_intensity: steady
  constraints:
    intensity_minutes_max: 5
  tags:
  - progressive
  - long
```
