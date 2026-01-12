---
doc_type: session_template_set
domain: running
philosophy_id: norwegian

race_types: [marathon]
audience: intermediate
phase: build
session_type: threshold_double

priority: 100
version: "1.0"
last_reviewed: "2026-01-12"
---

```template_spec
templates:
- id: threshold_double_v1
  description_key: norwegian_threshold_double_v1
  kind: cruise_intervals
  params:
    warmup_mi_range:
    - 2.0
    - 3.0
    cooldown_mi_range:
    - 1.0
    - 2.0
    reps_range:
    - 4
    - 8
    rep_minutes_range:
    - 5
    - 10
    float_minutes_range:
    - 1
    - 2
    intensity: T
  constraints:
    total_T_minutes_range:
    - 30
    - 50
    hard_minutes_max: 55
  tags:
  - threshold
  - double
```
