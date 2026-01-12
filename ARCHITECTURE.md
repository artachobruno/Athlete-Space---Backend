# Architecture Documentation

This document describes the production-grade structure of the AthleteSpace backend, with clear ownership per domain, enforced boundaries, and reduced cognitive load.

## Table of Contents

1. [Folder Structure](#folder-structure)
2. [Import Rules](#import-rules)
3. [Domain Ownership](#domain-ownership)
4. [How to Add a New Planner Feature](#how-to-add-a-new-planner-feature)
5. [Where LLMs Are Allowed](#where-llms-are-allowed)
6. [File Size Rules](#file-size-rules)
7. [Naming Conventions](#naming-conventions)

---

## Folder Structure

```
app/
├── api/                    # API routes (thin layer, no business logic)
│   ├── training/          # Training-related endpoints
│   ├── calendar/          # Calendar endpoints
│   ├── coach/             # Coach endpoints
│   └── admin/             # Admin endpoints
│
├── domains/               # Domain logic (pure business rules)
│   ├── training_plan/     # Training plan generation domain
│   │   ├── models.py      # Data models (immutable)
│   │   ├── macro_plan.py # Macro plan generation
│   │   ├── week_structure.py # Week structure loading
│   │   ├── volume_allocator.py # Volume allocation
│   │   ├── session_template_selector.py # Template selection
│   │   ├── session_text_generator.py # Session text generation
│   │   ├── observability.py # Metrics and logging
│   │   ├── guards.py     # Invariant checks
│   │   └── errors.py     # Domain-specific errors
│   ├── calendar/         # Calendar domain (future)
│   └── metrics/          # Metrics domain (future)
│
├── services/              # Service layer (orchestration + side effects)
│   ├── training_plan_service.py # Training plan service
│   ├── calendar_service.py # Calendar service (future)
│   └── coach_service.py  # Coach orchestration
│
├── infra/                # Infrastructure (external dependencies)
│   ├── llm/              # LLM clients and calls
│   │   ├── macro_plan.py # Macro plan LLM
│   │   └── session_text.py # Session text LLM
│   ├── db/               # Database connections
│   ├── redis/            # Redis clients
│   └── queues/           # Queue clients
│
├── rag/                  # RAG runtime
│   ├── retrieve/         # Retrieval logic
│   ├── embed/            # Embedding logic
│   └── pipeline.py       # RAG pipeline
│
├── planner/              # ⚠️ DEPRECATED - Legacy planner (being phased out)
│   └── plan_race_simple.py # Canonical planner entry point
│
└── workers/              # Background workers
    └── persistence_retry_worker.py
```

---

## Import Rules

### Direction of Dependencies

```
api → services → domains → (NO FURTHER)
                ↓
              infra
```

### Explicit Bans

**`domains/` MUST NOT import:**
- FastAPI
- DB sessions
- Redis
- LLM clients
- Any infrastructure

**`api/` MUST NOT import:**
- `infra/llm` directly
- Raw RAG loaders
- Domain models directly (use services)

**`services/` MUST NOT import:**
- FastAPI
- API schemas
- Domain internals (only public interfaces)

### Example Import Patterns

✅ **Correct:**
```python
# In app/services/training_plan_service.py
from app.domains.training_plan.models import PlanContext
from app.infra.llm.macro_plan import generate_macro_plan_llm

# In app/api/training/training.py
from app.services.training_plan_service import plan_race
```

❌ **Incorrect:**
```python
# In app/domains/training_plan/macro_plan.py
from app.infra.llm.macro_plan import generate_macro_plan_llm  # ❌ Domain importing infra

# In app/api/training/training.py
from app.domains.training_plan.models import PlanContext  # ❌ API importing domain directly
```

---

## Domain Ownership

Each domain has **exactly one owner folder**. No parallel implementations.

| Domain              | Allowed Location                |
| ------------------- | ------------------------------- |
| Training planning   | `app/domains/training_plan/`    |
| Calendar            | `app/domains/calendar/`         |
| Metrics             | `app/domains/metrics/`          |
| Coach orchestration | `app/services/coach_service.py` |
| API routes          | `app/api/**`                    |
| LLM calls           | `app/infra/llm/**`              |
| RAG runtime         | `app/rag/**`                    |
| RAG data            | `data/rag/**`                   |

**Rules:**
- ❌ No parallel implementations
- ❌ No shadow planners
- ❌ No logic in API routes

---

## How to Add a New Planner Feature

### Step 1: Determine the Layer

- **Business logic?** → `app/domains/training_plan/`
- **Orchestration?** → `app/services/training_plan_service.py`
- **LLM call?** → `app/infra/llm/`
- **API endpoint?** → `app/api/training/`

### Step 2: Add Domain Logic (if needed)

If adding new planning logic:

1. Create or update files in `app/domains/training_plan/`
2. Follow naming conventions:
   - `models.py` - Data models only
   - `errors.py` - Domain errors
   - `guards.py` - Invariant checks
   - `observability.py` - Metrics/logging

3. **Never import:**
   - FastAPI
   - DB sessions
   - LLM clients
   - Redis

### Step 3: Add LLM Infrastructure (if needed)

If adding a new LLM call:

1. Create file in `app/infra/llm/`
2. Export a single function: `async def generate_<feature>_llm(...) -> <Schema>`
3. Return Pydantic schemas, not domain models
4. Domain layer converts schemas to models

### Step 4: Update Service Layer

1. Add method to `app/services/training_plan_service.py`
2. Call domain functions
3. Handle persistence
4. Emit events

### Step 5: Update API (if needed)

1. Add endpoint in `app/api/training/`
2. Call service method only
3. No business logic in route

### Example: Adding a New Planning Stage

```python
# 1. Domain logic (app/domains/training_plan/new_stage.py)
from app.domains.training_plan.models import PlanContext

def process_new_stage(ctx: PlanContext) -> ProcessedData:
    """Pure domain logic, no side effects."""
    # ... business logic ...
    return ProcessedData(...)

# 2. Service layer (app/services/training_plan_service.py)
from app.domains.training_plan.new_stage import process_new_stage

async def plan_with_new_stage(...):
    # ... orchestration ...
    processed = process_new_stage(ctx)
    # ... persistence ...
    return result
```

---

## Where LLMs Are Allowed

### ✅ Allowed Locations

**Only in `app/infra/llm/`:**

- `macro_plan.py` - Macro plan generation (B2)
- `session_text.py` - Session text generation (B6)
- `fallback.py` - Fallback text generation

### ❌ Forbidden Locations

- `app/domains/**` - **NEVER** import LLM clients
- `app/services/**` - **NEVER** call LLM directly
- `app/api/**` - **NEVER** call LLM directly

### LLM Rules

1. **One call per stage** - No nested LLM calls
2. **Output validated immediately** - Schema validation required
3. **Fallback required** - Must have deterministic fallback
4. **No structure selection** - LLMs generate text only, never select structure

### Example LLM Module

```python
# app/infra/llm/macro_plan.py
from app.domains.training_plan.schemas import MacroPlanSchema

async def generate_macro_plan_llm(
    ctx: PlanContext,
    athlete_state: AthleteState,
) -> MacroPlanSchema:
    """Generate macro plan via LLM.
    
    Returns:
        MacroPlanSchema (Pydantic model, not domain model)
    """
    # ... LLM call ...
    return parsed_schema
```

---

## File Size Rules

### Soft Max: 300 lines
### Hard Max: 500 lines

**If over limit:**
1. Split by responsibility
2. Extract helper functions
3. Create submodules

**Example:**
```
# Before: macro_plan.py (600 lines)
# After:
#   macro_plan.py (200 lines) - Main logic
#   macro_plan_helpers.py (150 lines) - Helpers
#   macro_plan_validation.py (100 lines) - Validation
```

---

## Folder Size Rules

### Max 15 files per folder

**If over limit:**
1. Create subfolders by feature
2. Group related files

**Example:**
```
# Before: app/domains/training_plan/ (20 files)
# After:
#   app/domains/training_plan/
#     core/ (models.py, errors.py, enums.py)
#     stages/ (macro_plan.py, volume_allocator.py, ...)
#     support/ (guards.py, observability.py, ...)
```

---

## Naming Conventions

| Name         | Meaning                         |
| ------------ | ------------------------------- |
| `models.py`  | Dataclasses / Pydantic only     |
| `schemas.py` | Validation / serialization only |
| `errors.py`  | Domain-specific errors          |
| `service.py` | Orchestration + side effects    |
| `guards.py`  | Invariants / safety checks      |

**Rules:**
- ❌ No `utils/` unless unavoidable
- ❌ No generic helpers
- ✅ Use descriptive, specific names

---

## Canonical Planner

**Entry Point:** `app/planner/plan_race_simple.py`

This is the **ONLY** planner entry point. All planning traffic must flow through this function.

**Pipeline Stages:**
- B2: Macro plan generation (LLM-based, single call)
- B2.5: Philosophy selection (deterministic)
- B3: Week structure loading (RAG-backed, deterministic)
- B4: Volume allocation (deterministic)
- B5: Template selection (deterministic, RAG-backed)
- B6: Session text generation (LLM-based, cached)
- B7: Calendar persistence (idempotent)

**Rules:**
- No recursion
- No repair
- No retries
- No mutations after generation

---

## Legacy Code

### Deprecated Paths

The following paths are **DEPRECATED** and will be removed:

- `app/orchestrator/planner_v2/` - Legacy orchestrator
- `app/planning/` - Legacy planning module
- `app/coach/tools/plan_race.py::plan_race_build_legacy` - Legacy planner function

**Do not:**
- Import from deprecated paths
- Add new features to deprecated paths
- Fix bugs in deprecated paths (unless critical)

**Migration:**
- Use `app/services/training_plan_service.plan_race` instead

---

## Observability

### Standard Events

The planner emits these events:

- `macro_plan_generated` - Macro plan created
- `week_skeleton_loaded` - Week structure loaded
- `volume_allocated` - Volume distributed across days
- `template_selected` - Session templates selected
- `session_text_generated` - Session descriptions generated
- `calendar_persisted` - Plan saved to calendar

### Metrics

- Stage-level timing (`planner.stage.<stage>`)
- Success/failure rates (`planner_<stage>_success`, `planner_<stage>_failure`)
- Funnel tracking (stage progression)

### Invariant Checks

Guards enforce:
- Volume sums match expected totals
- Week count matches plan duration
- Session count is reasonable (2-10 per week)
- All distances are positive
- Sequential week indices

---

## Testing

### Test Structure

```
tests/
├── domains/
│   └── training_plan/     # Domain logic tests
├── services/             # Service layer tests
├── infra/                # Infrastructure tests
└── api/                  # API integration tests
```

### Test Rules

- Domain tests: Pure unit tests, no mocks
- Service tests: Mock domain and infra
- API tests: Integration tests with test client

---

## Questions?

**"Where do I add training logic?"**
→ `app/domains/training_plan/`

**"Where do I add an LLM call?"**
→ `app/infra/llm/`

**"Where do I add an API endpoint?"**
→ `app/api/training/` (calls service)

**"Where do I add orchestration?"**
→ `app/services/training_plan_service.py`

---

## Summary

This architecture provides:
- ✅ Clear ownership per domain
- ✅ Single planner implementation
- ✅ Enforced boundaries
- ✅ Reduced cognitive load
- ✅ Zero behavior change (pure refactor)

**New engineers can answer "Where do I add training logic?" in <10 seconds.**
