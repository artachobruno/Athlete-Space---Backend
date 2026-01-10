# Execution Controller Test Checklist

## Core Principle

> **Every user message must either fill a slot, ask for a slot, or trigger execution — nothing else is allowed.**

---

## Test Categories

### ✅ 1. Initial Discovery (Slot Collection)

- [ ] Test 1.1: Vague goal → asks for race_date
- [ ] Test 1.2: Partial time info (April) → asks for exact date
- [ ] Test 1.3: Exact date provided → executes immediately

### ✅ 2. Slot Completion Edge Cases

- [ ] Test 2.1: Date first, then distance → asks for distance
- [ ] Test 2.2: Distance synonym (26.2) → resolves to Marathon

### ✅ 3. Weekly Planning Dependencies

- [ ] Test 4.1: Weekly focus before plan → asks for race date (no advice)
- [ ] Test 5.1: Weekly focus after plan → executes weekly plan

### ✅ 4. Hard Failure Guards (MUST FAIL)

- [ ] Test 7.1: Advice before execution → REJECTED
- [ ] Test 7.2: Multiple questions → REJECTED
- [ ] Test 7.3: Chatty response → REJECTED

### ✅ 5. Core Invariant

- [ ] Every message with target_action must either:
  - Fill a slot (missing_slots decreases)
  - Ask for a slot (next_question provided)
  - Trigger execution (should_execute = true)

---

## Running Tests

```bash
# Run all execution controller tests
pytest tests/coach/test_execution_controller.py -v

# Run golden dataset tests
pytest tests/coach/test_golden_dataset.py -v

# Run specific test
pytest tests/coach/test_execution_controller.py::TestExecutionControllerBehavior::test_single_question_rule -v
```

---

## Validation Rules

1. **Single-Question Rule**: When missing_slots > 0, message must contain exactly one question
2. **No Advice Rule**: When target_action exists and missing_slots > 0, no advice allowed
3. **No Chatty Rule**: When target_action exists, response must be slot-oriented
4. **Execute Immediately Rule**: When missing_slots = [] and target_action exists, should_execute = true

---

## Expected Behavior Matrix

| Missing Slots | Target Action | should_execute | Expected Behavior |
|---------------|---------------|----------------|-------------------|
| > 0           | exists        | false          | Ask single question |
| 0             | exists        | true           | Execute immediately |
| 0             | null          | false          | Informational (allowed) |
| > 0           | null          | false          | Chat (allowed) |
