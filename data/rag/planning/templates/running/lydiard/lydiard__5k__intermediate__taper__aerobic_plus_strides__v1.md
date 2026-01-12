---
doc_type: session_template_set
domain: running
philosophy_id: lydiard

race_types: [5k]
audience: intermediate
phase: taper
session_type: aerobic_plus_strides

priority: 100
version: "1.0"
last_reviewed: "2026-01-12"
---

```template_spec
templates:
- id: aerobic_with_strides_taper_v1
  description_key: lydiard_aerobic_with_strides_taper_v1
  kind: easy_with_strides
  params:
    easy_mi_range:
    - 3.0
    - 6.0
    strides_count_range:
    - 4
    - 8
    stride_seconds_range:
    - 15
    - 20
    stride_recovery_seconds_range:
    - 60
    - 90
  constraints:
    strides_max: 8
    intensity_minutes_max: 4
  tags:
  - aerobic
  - strides
  - taper
```
