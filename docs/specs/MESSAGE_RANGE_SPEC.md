# message_range Specification

## Overview

The `message_range` structure provides **two-way linking** between the summary layer (3-5K tokens) and the full conversation layer (190K tokens). This is the core mechanism of RecCli's two-level linked retrieval.

## Structure

```json
{
  "start": "msg_042",
  "end": "msg_050",
  "start_index": 41,
  "end_index": 50
}
```

### Fields

| Field | Type | Description | Example |
|-------|------|-------------|---------|
| `start` | string | **Stable anchor**: Message ID (1-based) marking the first message in the range | `"msg_042"` |
| `end` | string | **Stable anchor**: Message ID (1-based) marking the last message in the range | `"msg_050"` |
| `start_index` | integer | **Fast lookup**: 0-based array index (inclusive) for the first message | `41` |
| `end_index` | integer | **Fast lookup**: 0-based array index (exclusive) for the position after the last message | `50` |

## Range Semantics

**Canonical form**: `[start_index, end_index)` - **inclusive-exclusive, 0-based**

This follows Python's standard slicing convention:
- `conversation[start_index:end_index]` returns the range
- `start_index` is **included**
- `end_index` is **excluded** (marks the position *after* the last message)

### Why Exclusive End?

1. **Matches Python slicing**: `conversation[41:50]` works directly
2. **Standard convention**: Most languages use exclusive end (Python, Go, Rust, etc.)
3. **Empty ranges**: `[N, N)` is empty (sensible), vs `[N, N]` having 1 element
4. **Length calculation**: `length = end_index - start_index` (simple arithmetic)
5. **Adjacent ranges**: `[0, 10)` and `[10, 20)` don't overlap

## Coordinate Systems

RecCli uses **two coordinate systems**:

### 1. Message IDs (1-based)
- **Format**: `msg_001`, `msg_002`, ..., `msg_042`, ..., `msg_999`
- **Purpose**: Stable anchors that survive compaction, edits, redactions
- **Usage**: Human-readable references, external links, cross-session refs
- **Properties**: Immutable, may have gaps after compaction

### 2. Array Indices (0-based)
- **Format**: `0`, `1`, ..., `41`, ..., `998`
- **Purpose**: Fast O(1) array lookup in Python
- **Usage**: Internal retrieval, slicing, iteration
- **Properties**: Mutable (change after compaction), no gaps

## Conversion Rules

### Message ID → Array Index

```python
# msg_042 is the 42nd message (1-based)
# Stored at index 41 in the array (0-based)
msg_id = "msg_042"
msg_num = int(msg_id.split("_")[1])  # 42
index = msg_num - 1                   # 41
```

### Array Index → Message ID

```python
# Index 41 contains the 42nd message
index = 41
msg_num = index + 1                   # 42
msg_id = f"msg_{msg_num:03d}"        # "msg_042"
```

### Range Construction

To create a range from `msg_042` to `msg_050` (inclusive both ends):

```python
start_id = "msg_042"
end_id = "msg_050"

start_num = int(start_id.split("_")[1])  # 42
end_num = int(end_id.split("_")[1])      # 50

# Convert to 0-based indices
start_index = start_num - 1   # 41 (inclusive)
end_index = end_num           # 50 (exclusive, so includes index 49 which is msg_050)

message_range = {
    "start": start_id,        # "msg_042"
    "end": end_id,            # "msg_050"
    "start_index": start_index,  # 41
    "end_index": end_index       # 50
}

# Retrieval
messages = conversation[start_index:end_index]  # conversation[41:50]
# Returns indices 41, 42, ..., 49 (9 messages)
# Which are msg_042, msg_043, ..., msg_050 ✓
```

## Examples

### Example 1: Multi-message Range

**Range**: msg_042 to msg_050 (9 messages)

```json
{
  "start": "msg_042",
  "end": "msg_050",
  "start_index": 41,
  "end_index": 50
}
```

**Retrieval**:
```python
messages = conversation[41:50]
# Returns 9 messages at indices 41-49
# Which are msg_042 through msg_050
len(messages)  # 9
```

**Verification**:
- `end_index - start_index = 50 - 41 = 9` ✓
- First message: `conversation[41]` → msg_042 ✓
- Last message: `conversation[49]` → msg_050 ✓

### Example 2: Single Message

**Range**: Only msg_042

```json
{
  "start": "msg_042",
  "end": "msg_042",
  "start_index": 41,
  "end_index": 42
}
```

**Retrieval**:
```python
messages = conversation[41:42]
# Returns 1 message at index 41
# Which is msg_042
len(messages)  # 1
```

**Verification**:
- `end_index - start_index = 42 - 41 = 1` ✓
- `conversation[41]` is msg_042 ✓

### Example 3: Empty Range (Edge Case)

**Range**: Empty (start equals end in indices)

```json
{
  "start": "msg_042",
  "end": "msg_041",
  "start_index": 41,
  "end_index": 41
}
```

**Retrieval**:
```python
messages = conversation[41:41]
# Returns empty list
len(messages)  # 0
```

Note: This is an edge case that shouldn't normally occur (end before start).

### Example 4: Full Session

**Range**: All messages in a 100-message session

```json
{
  "start": "msg_001",
  "end": "msg_100",
  "start_index": 0,
  "end_index": 100
}
```

**Retrieval**:
```python
messages = conversation[0:100]
# Returns all 100 messages
len(messages)  # 100
```

## Invariants

The following must **always** be true for a valid `message_range`:

1. **ID-Index Consistency**:
   ```python
   start_index == int(start.split("_")[1]) - 1
   end_index == int(end.split("_")[1])
   ```

2. **Non-negative Length**:
   ```python
   end_index >= start_index
   ```

3. **In-bounds**:
   ```python
   0 <= start_index < len(conversation)
   0 < end_index <= len(conversation)
   ```

4. **Retrieval Correctness**:
   ```python
   messages = conversation[start_index:end_index]
   assert messages[0] has ID == start
   assert messages[-1] has ID == end
   ```

## Common Operations

### Get Message Count

```python
count = end_index - start_index
```

### Check if Message in Range

```python
def in_range(msg_index: int, msg_range: dict) -> bool:
    return msg_range["start_index"] <= msg_index < msg_range["end_index"]
```

### Expand Range by N Messages

```python
def expand_range(msg_range: dict, n: int, max_len: int) -> dict:
    """Expand range by n messages on both sides"""
    return {
        "start_index": max(0, msg_range["start_index"] - n),
        "end_index": min(max_len, msg_range["end_index"] + n),
        # Note: start/end IDs would need to be updated too
    }
```

### Check if Two Ranges Overlap

```python
def ranges_overlap(r1: dict, r2: dict) -> bool:
    """Check if two ranges overlap"""
    return (r1["start_index"] < r2["end_index"] and
            r2["start_index"] < r1["end_index"])
```

### Merge Adjacent Ranges

```python
def merge_ranges(r1: dict, r2: dict) -> Optional[dict]:
    """Merge if adjacent or overlapping"""
    if r1["end_index"] >= r2["start_index"] and r1["start_index"] <= r2["start_index"]:
        return {
            "start": r1["start"],
            "end": r2["end"],
            "start_index": r1["start_index"],
            "end_index": max(r1["end_index"], r2["end_index"])
        }
    return None
```

## Compaction Safety

After compaction (removing messages), ranges become **invalid** because:
1. Array indices shift (messages move to different positions)
2. Message IDs may have gaps (msg_042 might be deleted)

**Solution**: Reindex all message_range structures after compaction.

See `RANGE_SEMANTICS_FIX.md` section on "Reindexing after transforms" (GPT-5 Safeguard #5).

## Multi-Span Support

For discussions that span multiple **non-contiguous** ranges:

```json
{
  "message_ranges": [
    {
      "start": "msg_010",
      "end": "msg_020",
      "start_index": 9,
      "end_index": 20
    },
    {
      "start": "msg_050",
      "end": "msg_060",
      "start_index": 49,
      "end_index": 60
    }
  ]
}
```

**Status**: Not yet implemented (GPT-5 Safeguard #3).

## Precision Quotes (char_span)

For **word-level or character-level** precision within a message:

```json
{
  "message_range": {
    "start": "msg_042",
    "end": "msg_042",
    "start_index": 41,
    "end_index": 42
  },
  "char_span": {
    "start": 120,
    "end": 245
  },
  "quoted_text": "This is the exact text from the message..."
}
```

**Status**: Not yet implemented (GPT-5 Safeguard #4).

## Related Documentation

- `RANGE_SEMANTICS_FIX.md`: Details on fixing range bugs and implementing safeguards
- `PROJECT_PLAN.md`: Phase 4 - Two-Level Linked Retrieval
- `CONTEXT_LOADING.md`: How ranges are used in memory middleware
- `summary_schema.py`: JSON schema validation for message_range

## Version

- **Spec version**: 1.0
- **Implementation**: RecCli v2 (Phase 4+)
- **Last updated**: 2025-11-02
