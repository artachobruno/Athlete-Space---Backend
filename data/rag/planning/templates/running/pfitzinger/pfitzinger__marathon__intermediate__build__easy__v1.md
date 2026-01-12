---
doc_type: session_template_set
domain: running
philosophy_id: pfitzinger

race_types: [marathon]
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
  description_key: pfitzinger_easy_continuous_v1
  kind: easy_continuous
  params:
    warmup_mi_range:
    - 0.0
    - 0.0
    cooldown_mi_range:
    - 0.0
    - 0.0
    easy_mi_range:
    - 3.0
    - 10.0
  constraints:
    intensity_minutes_max: 0
  tags:
  - z2
  - aerobic
```
