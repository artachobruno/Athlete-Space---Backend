---
doc_type: session_template_set
domain: running
philosophy_id: threshold_heavy

race_types: [marathon]
audience: intermediate
phase: taper
session_type: pre_race_shakeout

priority: 100
version: "1.0"
last_reviewed: "2026-01-12"
---

```template_spec
templates:
- id: pre_race_shakeout_v1
  description_key: threshold_heavy_pre_race_shakeout_v1
  kind: easy_continuous
  params:
    easy_mi_range:
    - 1.0
    - 3.0
  constraints:
    intensity_minutes_max: 0
    total_duration_max: 25
  tags:
  - pre_race
  - shakeout
  - very_light
  - activation
```
