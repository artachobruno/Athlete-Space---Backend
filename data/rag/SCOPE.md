# RAG Knowledge Scope

## Purpose
This corpus defines the **only allowed training knowledge** that the AI coach may reference when reasoning about training plans, intensity, progression, and safety.

This is a **closed-world** knowledge base:
- The system must not rely on external sources for training theory.
- When information is missing, the system must default to conservative planning principles and/or request additional user context.

## In-Scope Knowledge
The following categories are explicitly allowed:

1. Endurance training philosophies (**running only**, 5K → ultra)
2. Training phase definitions (base, build, peak, taper; including “maintenance/no-goal mode” as a phase concept)
3. Intensity distribution principles (low/moderate/high buckets; enforcement rules; caps on hard sessions)
4. Progression and load management rules (volume/frequency/intensity/long-session progression constraints; recovery weeks)
5. Tapering strategies (load reduction levers and constraints; readiness bias)
6. Injury risk prevention principles (sequencing rules, novelty control, conservative defaults)
7. Session intent explanations (NOT prescriptions; “why a session exists” and what it targets conceptually)
8. Gating rules (when a philosophy is allowed vs disallowed; novice vs advanced constraints)
9. Anti-patterns and failure modes (what to avoid: stacking stress, gray-zone drift, panic training, etc.)

## Out-of-Scope Knowledge
The following are explicitly forbidden:

- Medical advice, diagnosis, treatment, or rehabilitation guidance
- Nutrition plans, supplement protocols, drug guidance, or dosing
- Biomechanics, physical therapy instructions, or injury-specific corrective exercises
- Strength training programs (including detailed lifting prescriptions)
- Cross-training specifics (bike, swim) beyond high-level “low-impact aerobic” mention
- Elite-only protocols that require supervision or specialized measurement (unless described only as a gated philosophy with conservative constraints and no operational instructions)
- Any advice requiring professional certification (medical, dietetic, clinical)

## Authoritativeness Rule
All documents in this corpus must:
- Be written or curated by the system owner
- Be internally consistent and stable over time
- Avoid external citations or URLs (closed-world)
- Be reviewed before ingestion
- Use consistent terminology and section headers to support deterministic chunking

## Safety Principle
When knowledge is ambiguous or conflicting, the system must:
- Prefer conservative interpretations
- Favor injury risk minimization and long-term consistency
- Fall back to lower intensity and simpler structures
- Avoid novelty and avoid stacking stress
- Recommend seeking qualified professional help for medical or injury concerns
