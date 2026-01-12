---
doc_type: session_template_set
domain: running
philosophy_id: marathon_specificity

race_types: [marathon]
audience: intermediate
phase: taper
session_type: race_day

priority: 100
version: "1.0"
last_reviewed: "2026-01-12"
---

```template_spec
templates:
- id: race_marathon_v1
  description_key: marathon_specificity_race_marathon_v1
  kind: race
  params:
    race_distance_km: 5.0
    warmup_mi_range:
    - 1.0
    - 2.0
    cooldown_mi_range:
    - 0.5
    - 1.5
    race_intensity: R
  constraints:
    total_duration_max: 60
  tags:
  - race
  - '{race}'
  - target_effort
```
