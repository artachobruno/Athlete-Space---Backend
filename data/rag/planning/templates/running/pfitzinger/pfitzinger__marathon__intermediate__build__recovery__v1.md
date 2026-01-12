---
doc_type: session_template_set
domain: running
philosophy_id: pfitzinger

race_types: [marathon]
audience: intermediate
phase: build
session_type: recovery

priority: 100
version: "1.0"
last_reviewed: "2026-01-12"
---

```template_spec
templates:
- id: recovery_easy_v1
  description_key: pfitzinger_recovery_easy_v1
  kind: easy_continuous
  params:
    easy_mi_range:
    - 2.0
    - 5.0
  constraints:
    intensity_minutes_max: 0
  tags:
  - recovery
  - very_easy
```
