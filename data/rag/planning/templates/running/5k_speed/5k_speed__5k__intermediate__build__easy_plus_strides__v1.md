---
doc_type: session_template_set
domain: running
philosophy_id: 5k_speed

race_types: [5k]
audience: intermediate
phase: build
session_type: easy_plus_strides

priority: 100
version: "1.0"
last_reviewed: "2026-01-12"
---

```template_spec
templates:
- id: easy_with_strides_v1
  description_key: 5k_speed_easy_with_strides_v1
  kind: easy_with_strides
  params:
    easy_mi_range:
    - 3.0
    - 8.0
    strides_count_range:
    - 4
    - 10
    stride_seconds_range:
    - 15
    - 25
    stride_recovery_seconds_range:
    - 45
    - 90
  constraints:
    strides_max: 10
    intensity_minutes_max: 5
  tags:
  - strides
  - economy
```
