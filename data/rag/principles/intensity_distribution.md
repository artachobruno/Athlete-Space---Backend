---
id: intensity_distribution
domain: running
category: principle
race_types: [5k, 10k, half, marathon, ultra]
sport_modes: [training, no_goal]
intensity_bias: low
risk_level: low
audience: all
requires: []
prohibits: []
tags: [intensity, polarized, pyramidal, guardrails, hard_days]
source: canonical
version: "1.0"
last_reviewed: "2026-01-11"
---

# Principle: Intensity Distribution

## Description
Most effective endurance and hybrid training systems maintain a large proportion of training at low intensity, with limited exposure to moderate and high intensity. Intensity distribution is a **planning constraint** used to protect consistency, manage fatigue, and guide session selection.

This document defines intensity distribution as **ranges and rules**, not workout prescriptions.

## Intensity Buckets (Conceptual)
The system uses three buckets:
1. **Low intensity**: conversational / aerobic / recovery-compatible
2. **Moderate intensity**: sustained “comfortably hard” work (threshold-adjacent)
3. **High intensity**: short hard efforts producing significant residual fatigue

Exact physiological boundaries are athlete-dependent; the planner should use the athlete’s available zones (HR, pace, power, RPE) if present. If not, default to conservative RPE-based mapping.

## Accepted Global Ranges (Default)
These apply unless a philosophy explicitly overrides them.
- **Low intensity:** 65–90%
- **Moderate intensity:** 0–20%
- **High intensity:** 0–10%

## Recommended Defaults by Athlete Type
### Beginner / Return-to-training / Injury-prone
- Low: 80–95%
- Moderate: 0–15%
- High: 0–5%

### Intermediate / General performance
- Low: 75–90%
- Moderate: 5–15%
- High: 0–10%

### Advanced / High durability (gated)
- Low: 70–85%
- Moderate: 5–20%
- High: 0–10% (rarely above)

## Session Count Guardrails (Practical)
Regardless of time-based percentages:
- **Hard sessions per week (default max): 2**
- High-intensity exposures must be separated by at least **one easy/rest day**
- Moderate-intensity density must not silently “spread” across the week

## Common Failure Modes
1. **Gray-zone accumulation**
   - Too much moderate work disguised as easy
2. **Intensity stacking**
   - Multiple hard days clustered together
3. **Compensation behavior**
   - Adding intensity to offset missed volume
4. **Distribution drift**
   - Easy days become moderate over time (“pace creep”)

## Safety Bias
When the system is uncertain (poor data, unclear zones, limited recovery signals):
- Increase low-intensity share
- Reduce moderate intensity first
- Keep high intensity minimal or omit entirely

## Enforcement Rules (Planner)
- Each plan week must compute intensity distribution estimates
- If distribution violates allowed ranges, planner must:
  1. remove or reduce high intensity first
  2. then reduce moderate intensity
  3. only then consider volume reduction
- No week should include both:
  - increased intensity density and increased long-duration stress (unless gated)
