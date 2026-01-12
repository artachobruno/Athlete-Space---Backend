---
doc_type: session_template_set
domain: running
philosophy_id: pfitzinger

race_types: [marathon]
audience: intermediate
phase: build
session_type: marathon_specific_or_progression

priority: 100
version: "1.0"
last_reviewed: "2026-01-12"
---

```template_spec
templates:
- id: marathon_specific_or_progression_v1
  description_key: pfitzinger_marathon_specific_or_progression_v1
  kind: steady_T_block
  params:
    warmup_mi_range:
    - 2.0
    - 3.0
    cooldown_mi_range:
    - 1.0
    - 2.0
    continuous_T_minutes_range:
    - 20
    - 40
    intensity: M
  constraints:
    total_T_minutes_range:
    - 20
    - 40
    hard_minutes_max: 45
  tags:
  - marathon
  - progression
```
