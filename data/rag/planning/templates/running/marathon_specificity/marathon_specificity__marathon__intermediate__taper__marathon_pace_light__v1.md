---
doc_type: session_template_set
domain: running
philosophy_id: marathon_specificity

race_types: [marathon]
audience: intermediate
phase: taper
session_type: marathon_pace_light

priority: 100
version: "1.0"
last_reviewed: "2026-01-12"
---

```template_spec
templates:
- id: marathon_pace_light_taper_v1
  description_key: marathon_specificity_marathon_pace_light_taper_v1
  kind: steady_T_block
  params:
    warmup_mi_range:
    - 1.5
    - 2.5
    cooldown_mi_range:
    - 1.0
    - 2.0
    continuous_T_minutes_range:
    - 10
    - 20
    intensity: M
  constraints:
    total_T_minutes_range:
    - 10
    - 20
    hard_minutes_max: 25
  tags:
  - taper
  - marathon_pace
  - light
```
