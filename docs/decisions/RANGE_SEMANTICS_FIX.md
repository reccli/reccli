# Range Semantics Fix - GPT-5 Safeguard #2

## Problem Statement

Our `message_range` structure has **inconsistent and undocumented** range semantics, causing bugs in two-level linked retrieval. This violates GPT-5's Safeguard #2: "Define range semantics (inclusive vs exclusive)".

## Current Issues

### Issue 1: Conflicting Comments vs Code

**File**: `summarizer.py:265-268`
```python
start_idx = span["start_index"] - 1  # Convert to 0-based
end_idx = span["end_index"]  # Inclusive end

return conversation[start_idx:end_idx]  # ← BUG: Python slice is EXCLUSIVE on end!
```

**Problem**: Comment says "Inclusive end" but Python's `[start:end]` is **exclusive** on `end`.

### Issue 2: Different Logic in extract_temporal_bounds

**File**: `summarizer.py:285-286`
```python
start_idx = message_range.get("start_index", 1) - 1  # Convert to 0-based
end_idx = message_range.get("end_index", len(conversation)) - 1  # ← Subtracts 1!
```

**Problem**: This function subtracts 1 from `end_index`, treating it as **inclusive** (1-based), while `extract_span_messages` doesn't subtract 1.

### Issue 3: Inconsistent Usage in retrieval.py

**File**: `retrieval.py:51-59`
```python
start_idx = msg_range.get("start_index", 1) - 1  # Convert to 0-based
end_idx = msg_range.get("end_index", len(self.conversation))  # ← No -1!

expanded_end = min(len(self.conversation), end_idx + expand_context)
messages = self.conversation[expanded_start:expanded_end]  # ← EXCLUSIVE end
```

**Problem**: Doesn't subtract 1 from `end_index`, inconsistent with `extract_temporal_bounds`.

### Issue 4: Undocumented Semantics in Schema

**File**: `summary_schema.py` (implied)

The `message_range` structure has no documentation on whether ranges are:
- **Inclusive-inclusive**: `[start, end]` - both ends included
- **Inclusive-exclusive**: `[start, end)` - Python standard
- **1-based or 0-based**: Are indices 1-based (like msg_001) or 0-based (like Python arrays)?

## Root Cause Analysis

The confusion stems from mixing **two different coordinate systems**:

1. **Message IDs**: 1-based (msg_001, msg_002, ..., msg_042)
2. **Python indices**: 0-based (array[0], array[1], ..., array[41])

Our `message_range` stores BOTH:
- `start` and `end`: Message IDs (stable anchors, 1-based)
- `start_index` and `end_index`: Array indices (for O(1) lookup, ???-based)

**Question**: Are `start_index` and `end_index`:
- **1-based** (matching message IDs)? → Would need to convert to 0-based for Python slicing
- **0-based** (matching Python)? → Directly usable but don't match message IDs

## Proposed Solution

### Design Decision: Follow Python Conventions

**Recommendation**: Use **inclusive-exclusive** ranges `[start_index, end_index)` with **0-based indices**.

**Rationale**:
1. **Matches Python slicing**: `conversation[start_index:end_index]` works directly
2. **Standard convention**: Most programming languages use exclusive end
3. **Empty ranges**: `[N, N)` is empty (sensible), vs `[N, N]` having 1 element
4. **Length calculation**: `length = end_index - start_index` (simple)

### Concrete Changes

#### 1. Document in Schema

Add to `summary_schema.py` or create `MESSAGE_RANGE_SPEC.md`:

```markdown
## message_range Semantics

Fields:
- `start`: Message ID (stable anchor, 1-based), e.g., "msg_042"
- `end`: Message ID (stable anchor, 1-based), e.g., "msg_050"
- `start_index`: Array index (0-based, inclusive), e.g., 41
- `end_index`: Array index (0-based, EXCLUSIVE), e.g., 50

Range semantics: **[start_index, end_index)** - inclusive-exclusive

Examples:
- msg_042 to msg_050 (inclusive):
  - start: "msg_042", end: "msg_050"
  - start_index: 41, end_index: 50
  - Retrieval: conversation[41:50] → returns messages 42-50 (9 messages)

- Single message msg_042:
  - start: "msg_042", end: "msg_042"
  - start_index: 41, end_index: 42
  - Retrieval: conversation[41:42] → returns message 42 (1 message)

- Empty range (edge case):
  - start_index: 41, end_index: 41
  - Retrieval: conversation[41:41] → returns [] (0 messages)
```

#### 2. Fix extract_span_messages

**File**: `summarizer.py:265-268`

**Current (BROKEN)**:
```python
start_idx = span["start_index"] - 1  # Convert to 0-based
end_idx = span["end_index"]  # Inclusive end

return conversation[start_idx:end_idx]
```

**Fixed**:
```python
# start_index and end_index are already 0-based
# Range is [start_index, end_index) - inclusive-exclusive
start_idx = span["start_index"]
end_idx = span["end_index"]

return conversation[start_idx:end_idx]
```

#### 3. Fix extract_temporal_bounds

**File**: `summarizer.py:285-286`

**Current (INCONSISTENT)**:
```python
start_idx = message_range.get("start_index", 1) - 1  # Convert to 0-based
end_idx = message_range.get("end_index", len(conversation)) - 1
```

**Fixed**:
```python
# Indices are 0-based, range is [start, end) - inclusive-exclusive
start_idx = message_range.get("start_index", 0)
end_idx = message_range.get("end_index", len(conversation))  # Exclusive end

# For temporal bounds, we want INCLUSIVE end message
# So access [start_idx] and [end_idx - 1]
```

Then later (lines 292-293, 296-297):
```python
# Get first message timestamp
if 0 <= start_idx < len(conversation):
    first_msg = conversation[start_idx]
    # ... get t_first

# Get last message timestamp (INCLUSIVE end of range)
last_idx = end_idx - 1  # Convert exclusive end to inclusive
if 0 <= last_idx < len(conversation):
    last_msg = conversation[last_idx]
    # ... get t_last
```

#### 4. Fix retrieval.py

**File**: `retrieval.py:51-59`

**Current (INCONSISTENT)**:
```python
start_idx = msg_range.get("start_index", 1) - 1  # Convert to 0-based
end_idx = msg_range.get("end_index", len(self.conversation))
```

**Fixed**:
```python
# Indices are 0-based, range is [start, end) - inclusive-exclusive
start_idx = msg_range.get("start_index", 0)
end_idx = msg_range.get("end_index", len(self.conversation))
```

#### 5. Update code_change_detector.py

**File**: `code_change_detector.py:206-223`

**Current (UNCLEAR)**:
```python
first_msg = info["first_seen"]  # e.g., "msg_042"
last_msg = info["last_seen"]    # e.g., "msg_050"
first_idx = int(first_msg.split("_")[1])  # 42
last_idx = int(last_msg.split("_")[1])    # 50

changes.append({
    "message_range": {
        "start": first_msg,
        "end": last_msg,
        "start_index": first_idx,
        "end_index": last_idx
    }
})
```

**Problem**: Message IDs are 1-based ("msg_042" = 42nd message), so:
- first_idx = 42 → should be 41 in 0-based Python
- last_idx = 50 → should be... 49? or 50 (exclusive)?

**Fixed**:
```python
first_msg = info["first_seen"]  # e.g., "msg_042"
last_msg = info["last_seen"]    # e.g., "msg_050"

# Convert 1-based message IDs to 0-based array indices
first_msg_num = int(first_msg.split("_")[1])  # 42
last_msg_num = int(last_msg.split("_")[1])    # 50

first_idx = first_msg_num - 1  # 42 → 41 (0-based)
last_idx = last_msg_num        # 50 (exclusive end, so 50 means "up to but not including 50")

# BUT WAIT: If last_msg is "msg_050", we want to INCLUDE message 50!
# So end_index should be 50 (exclusive) which includes message at index 49... NO!
#
# Actually: msg_050 is the 50th message (1-based), stored at index 49 (0-based)
# To INCLUDE msg_050 in an exclusive range, end_index = 50
# Because conversation[41:50] includes indices 41-49, which are messages 42-50

changes.append({
    "message_range": {
        "start": first_msg,           # "msg_042"
        "end": last_msg,               # "msg_050"
        "start_index": first_idx,      # 41 (0-based, inclusive)
        "end_index": last_idx          # 50 (0-based, exclusive) → includes msg 42-50
    }
})
```

**Verification**:
- msg_042 is 42nd message (1-based) → index 41 (0-based)
- msg_050 is 50th message (1-based) → index 49 (0-based)
- Range [41, 50) includes indices 41, 42, ..., 49
- That's messages 42, 43, ..., 50 ✓

## Validation Rules

Add to `summary_verification.py`:

```python
def validate_message_range_semantics(message_range: Dict, conversation: List[Dict]) -> Tuple[bool, str]:
    """
    Validate message_range semantics

    Checks:
    1. start_index and end_index are 0-based
    2. start_index is inclusive, end_index is exclusive
    3. start/end IDs match start_index/end_index positions
    4. Range is non-negative length
    """
    start_id = message_range.get("start")
    end_id = message_range.get("end")
    start_index = message_range.get("start_index")
    end_index = message_range.get("end_index")

    # Extract message numbers from IDs (1-based)
    start_num = int(start_id.split("_")[1])
    end_num = int(end_id.split("_")[1])

    # Verify indices match IDs
    # msg_042 (42nd message) should have start_index = 41 (0-based)
    expected_start_index = start_num - 1
    expected_end_index = end_num  # Exclusive end: to include msg_050, end_index = 50

    if start_index != expected_start_index:
        return False, f"start_index {start_index} doesn't match start ID {start_id} (expected {expected_start_index})"

    if end_index != expected_end_index:
        return False, f"end_index {end_index} doesn't match end ID {end_id} (expected {expected_end_index})"

    # Verify non-negative range
    if end_index < start_index:
        return False, f"Invalid range: end_index {end_index} < start_index {start_index}"

    # Verify indices in bounds
    if start_index < 0 or end_index > len(conversation):
        return False, f"Range [{start_index}, {end_index}) out of bounds for conversation of length {len(conversation)}"

    return True, ""
```

## Migration Plan

1. **Update documentation** (this file + schema comments)
2. **Fix code** (4 files: summarizer.py, retrieval.py, code_change_detector.py, +validation)
3. **Add validation** to catch future errors
4. **Test with existing .devsession files** to ensure backward compatibility
5. **Regenerate any corrupt summaries** if needed

## Backward Compatibility

**Risk**: If existing .devsession files have message_range with different semantics, this change could break them.

**Mitigation**:
1. Add migration script to detect and fix old format
2. Add version field to message_range: `"range_version": "1.0"`
3. Support both old and new formats during transition

## Next Steps

After implementing this fix, proceed to GPT-5's remaining safeguards:
- ✓ Safeguard #1: Stable anchors (already have start/end IDs)
- ✓ Safeguard #2: Define range semantics (THIS FIX)
- ⏸ Safeguard #3: Multi-span support
- ⏸ Safeguard #4: Precision quotes (char_span)
- ⏸ Safeguard #5: Reindexing after transforms
- ⏸ Safeguard #6: Monotonic time & UTC
- ⏸ Safeguard #7: Validation pass
- ⏸ Safeguard #8: Compaction safety net
