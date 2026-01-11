# Testing Guide: Orchestrator-Guided, Extractor-Authoritative Architecture

## Quick Test Queries

### Test 1: Initial Race Planning (Orchestrator declares attributes)
**Query:** `"I'm training for a marathon"`

**Expected behavior:**
- Orchestrator sets `required_attributes: ["race_distance", "race_date"]`
- Orchestrator sets `optional_attributes: []` (or may include `["target_time", "weekly_mileage"]`)
- Extractor extracts `race_distance: "Marathon"` from the message
- Extractor marks `race_date` in `missing_fields`
- Result: `missing_slots: ["race_date"]`, `should_execute: False`
- Response: Question asking for race date

**What to check:**
- `result.required_attributes` should contain `["race_distance", "race_date"]`
- `result.filled_slots` should have `{"race_distance": "Marathon"}`
- `result.missing_slots` should contain `["race_date"]`
- Message should be a question about the race date

---

### Test 2: Follow-up with Date (Extractor merges context)
**Query:** `"April 25th"` (in same conversation)

**Expected behavior:**
- Orchestrator sets same `required_attributes` as before
- Extractor uses conversation context (known `race_distance: "Marathon"`)
- Extractor extracts `race_date: "2026-04-25"` (infers year)
- All required attributes now filled
- Result: `missing_slots: []`, `should_execute: True`
- Response: Executes `plan_race_build` tool

**What to check:**
- `result.filled_slots` should have both `race_distance` and `race_date`
- `result.missing_slots` should be empty
- `result.should_execute` should be `True`

---

### Test 3: Complete Query (All attributes at once)
**Query:** `"Marathon on April 25, aiming for sub-3. Running ~55 mpw."`

**Expected behavior:**
- Orchestrator sets `required_attributes: ["race_distance", "race_date"]`
- Orchestrator sets `optional_attributes: ["target_time", "weekly_mileage"]`
- Extractor extracts all mentioned attributes:
  - `race_distance: "Marathon"`
  - `race_date: "2026-04-25"`
  - `target_time: "03:00:00"` (sub-3 → 3 hours)
  - `weekly_mileage: 55`
- All required attributes filled
- Result: `missing_slots: []`, `should_execute: True`

**What to check:**
- `result.filled_slots` should contain all extracted values
- `result.missing_slots` should be empty
- All values should be in canonical form (date as date object, time as HH:MM:SS)

---

### Test 4: Ambiguous Query (Extractor flags ambiguity)
**Query:** `"I want to run a race in spring"`

**Expected behavior:**
- Orchestrator sets `required_attributes: ["race_distance", "race_date"]`
- Extractor marks `race_date` in `ambiguous_fields` (spring is too vague)
- Extractor marks `race_distance` in `missing_fields` (not specified)
- Result: `missing_slots: ["race_distance", "race_date"]`, `should_execute: False`
- Response: Question asking for specific race date and distance

**What to check:**
- Extractor should mark ambiguous fields (check logs for `ambiguous_fields`)
- `result.missing_slots` should include ambiguous fields
- Message should ask for clarification

---

## How to Run Tests

### Option 1: Using the Test Script
```bash
python scripts/test_extractor_architecture.py
```

### Option 2: Using pytest (with proper fixtures)
```bash
pytest tests/mcp/test_mcp_smoke.py::test_plan_race_build -v
```

### Option 3: Using the CLI
```bash
python -m cli.cli chat "I'm training for a marathon" --athlete-id 1
```

### Option 4: Via API (if server is running)
```bash
curl -X POST http://localhost:8000/api/coach/chat \
  -H "Content-Type: application/json" \
  -d '{
    "message": "I'\''m training for a marathon",
    "user_id": "test_user",
    "athlete_id": 1
  }'
```

---

## What to Verify

### 1. Orchestrator Output
Check that `OrchestratorAgentResponse` has:
- ✅ `required_attributes`: List of attributes the orchestrator says are needed
- ✅ `optional_attributes`: List of optional attributes
- ✅ `filled_slots`: **Empty** (orchestrator does NOT extract values)

### 2. Extractor Output (check logs)
Look for log entries showing:
- ✅ `extract_attributes` being called with `attributes_requested`
- ✅ `ExtractedAttributes` with:
  - `values`: Dictionary of extracted values
  - `confidence`: Overall confidence (0.0-1.0)
  - `evidence`: Evidence spans from user message
  - `missing_fields`: Attributes requested but not found
  - `ambiguous_fields`: Attributes that are unclear

### 3. Final Result
- ✅ `filled_slots`: Populated from extractor output (after normalization)
- ✅ `missing_slots`: Computed from extractor's `missing_fields` and `ambiguous_fields`
- ✅ `should_execute`: `True` only when all required attributes are extracted
- ✅ Persistence: Slots are saved to `conversation_progress` with conversation ID

---

## Debugging Tips

1. **Check orchestrator output first:**
   ```python
   print(f"Required attributes: {result.required_attributes}")
   print(f"Optional attributes: {result.optional_attributes}")
   print(f"Filled slots: {result.filled_slots}")  # Should be empty initially
   ```

2. **Check extractor logs:**
   Look for log entries with:
   - `"Extracting attributes"`
   - `"Attribute extraction completed"`
   - `missing_count`, `ambiguous_count`, `confidence`

3. **Check final merged slots:**
   ```python
   print(f"Merged slots: {result.filled_slots}")  # Should have extracted values
   print(f"Missing slots: {result.missing_slots}")  # Should match extractor output
   ```

4. **Verify persistence:**
   Check database or logs for `conversation_progress` entries:
   - Slots should be serialized (dates as ISO strings in DB)
   - `awaiting_slots` should match `missing_slots`

---

## Sample Test Cases

### Happy Path (Complete in one turn)
```python
query = "Marathon on April 25, aiming for sub-3"
# Expected: should_execute=True, all slots filled
```

### Multi-turn (Progressive slot filling)
```python
turn1 = "I'm training for a marathon"
# Expected: missing_slots=["race_date"], should_execute=False

turn2 = "April 25th"  # Same conversation_id
# Expected: missing_slots=[], should_execute=True
```

### Ambiguous Input
```python
query = "I want to run a race in the spring"
# Expected: ambiguous_fields=["race_date"], missing_fields=["race_distance"]
```

### Partial Information
```python
query = "Marathon training"
# Expected: filled_slots={"race_distance": "Marathon"}, missing_slots=["race_date"]
```
