---
doc_type: session_template_set
domain: running
philosophy_id: lydiard

race_types: [5k]
audience: intermediate
phase: build
session_type: hill_strength_or_fartlek

priority: 100
version: "1.0"
last_reviewed: "2026-01-12"
---

```template_spec
templates:
- id: hill_strength_or_fartlek_v1
  description_key: lydiard_hill_strength_or_fartlek_v1
  kind: vo2_intervals
  params:
    warmup_mi_range:
    - 1.5
    - 3.0
    cooldown_mi_range:
    - 1.0
    - 3.0
    reps_range:
    - 4
    - 8
    rep_minutes_range:
    - 2
    - 5
    recovery_minutes_range:
    - 2
    - 4
    intensity: I
  constraints:
    total_I_minutes_range:
    - 12
    - 24
    hard_minutes_max: 30
  tags:
  - hills
  - fartlek
```
