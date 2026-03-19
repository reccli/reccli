# GPT-5 Safeguards Implementation Status

**Status:** Mixed implementation/historical note. The safeguards described here are still relevant, but the canonical range-semantics contract now lives in `docs/specs/MESSAGE_RANGE_SPEC.md`.

This document tracks our implementation of the 8 safeguards recommended by GPT-5 for robust two-level linked retrieval.

## Safeguard #1: Stable Anchors ✅ COMPLETE

**Requirement**: Use stable IDs (not just indices) to survive compaction.

**Implementation**:
- ✅ We have BOTH stable IDs (`start`, `end`) AND array indices (`start_index`, `end_index`)
- ✅ Message IDs format: `msg_001`, `msg_002`, ..., `msg_042`
- ✅ IDs are 1-based and immutable
- ✅ Indices are 0-based and mutable (change after compaction)

**Files**:
- `summary_verification.py:27-31` - Message ID lookup table
- `MESSAGE_RANGE_SPEC.md` - Full documentation

**Status**: ✅ Already implemented

---

## Safeguard #2: Define Range Semantics ✅ COMPLETE

**Requirement**: Clearly define whether ranges are inclusive-inclusive or inclusive-exclusive.

**Implementation**:
- ✅ Adopted `[start_index, end_index)` - **inclusive-exclusive, 0-based**
- ✅ Follows Python slicing convention
- ✅ Fixed bugs in `summarizer.py`, `retrieval.py`, `code_change_detector.py`
- ✅ Added comprehensive documentation
- ✅ Updated validation to enforce semantics

**Changes Made**:
1. `summarizer.py:268-272` - Fixed `extract_span_messages()` to not subtract 1
2. `summarizer.py:292-325` - Fixed `extract_temporal_bounds()` to handle exclusive end
3. `retrieval.py:53-55` - Fixed `retrieve_full_context()` to use 0-based indices
4. `code_change_detector.py:206-217` - Fixed range construction to use exclusive end
5. `summary_verification.py:37-113` - Added comprehensive validation with new semantics

**Files**:
- `MESSAGE_RANGE_SPEC.md` - Complete specification
- `RANGE_SEMANTICS_FIX.md` - Detailed fix documentation

**Status**: ✅ Fully implemented

---

## Safeguard #3: Multi-Span Support ⏸️ EVALUATED - NOT YET NEEDED

**Requirement**: Support discussions that span multiple non-contiguous ranges.

**Use Case**: A decision discussed in messages 10-20, then referenced again in 50-60.

**Current Limitation**:
We only support single `message_range` per summary item:
```json
{
  "decision": "Use PostgreSQL",
  "message_range": {
    "start": "msg_010",
    "end": "msg_020",
    "start_index": 9,
    "end_index": 20
  }
}
```

**Proposed Design** (not yet implemented):
```json
{
  "decision": "Use PostgreSQL",
  "message_ranges": [
    {
      "start": "msg_010",
      "end": "msg_020",
      "start_index": 9,
      "end_index": 20,
      "span_role": "initial_discussion"
    },
    {
      "start": "msg_050",
      "end": "msg_060",
      "start_index": 49,
      "end_index": 60,
      "span_role": "follow_up"
    }
  ]
}
```

**Evaluation**:
- **Frequency**: Low - most discussions are contiguous
- **Workaround**: Can create separate summary items for each span
- **Complexity**: Medium - needs schema change + retrieval logic update
- **Priority**: Low - not blocking current use cases

**Recommendation**: Defer until we observe actual need in practice.

**Status**: ⏸️ Evaluated but not implemented

---

## Safeguard #4: Precision Quotes (char_span) ⏸️ EVALUATED - FUTURE ENHANCEMENT

**Requirement**: Support character-level or word-level precision within messages.

**Use Case**: Quote a specific sentence from a long message.

**Current Limitation**:
We can only reference entire messages or message ranges.

**Proposed Design** (not yet implemented):
```json
{
  "decision": "Use PostgreSQL",
  "message_range": {
    "start": "msg_042",
    "end": "msg_042",
    "start_index": 41,
    "end_index": 42
  },
  "char_span": {
    "start": 120,
    "end": 245,
    "text": "PostgreSQL offers better JSON support and we need JSONB..."
  }
}
```

**Evaluation**:
- **Frequency**: Medium - would be useful for long messages
- **Workaround**: Current message-level granularity is acceptable
- **Complexity**: Medium - needs retrieval UI changes to highlight spans
- **Priority**: Medium - nice-to-have for UX

**Use Cases**:
1. **Long error messages**: Highlight the specific error line
2. **Code blocks**: Reference specific functions within a large code block
3. **Multi-topic messages**: Extract only the relevant topic

**Recommendation**: Implement in Phase 7 when building retrieval UI.

**Status**: ⏸️ Evaluated, planned for Phase 7

---

## Safeguard #5: Reindexing After Transforms ✅ COMPLETE

**Requirement**: After compaction, edits, or redactions, reindex all message_range structures.

**Problem**: After removing messages, array indices shift but message_range isn't updated.

### Example of the Problem

**Before compaction** (100 messages):
```json
{
  "decision": "Use PostgreSQL",
  "message_range": {
    "start": "msg_042",
    "end": "msg_050",
    "start_index": 41,
    "end_index": 50
  }
}
```

**After compaction** (messages 1-30 removed, now 70 messages):
- msg_042 is now at index 11 (was 41)
- msg_050 is now at index 19 (was 49)
- But `message_range` still says `start_index: 41, end_index: 50` ❌

**Result**: `conversation[41:50]` returns wrong messages!

### Implementation

**File**: `reccli/reindexing.py` (373 lines)

**Main API**:
```python
def reindex_summary_after_compaction(
    summary: Dict,
    conversation: List[Dict]
) -> Tuple[Dict, List[str]]:
    """
    Reindex all message_range structures after compaction

    Args:
        summary: Summary dict to reindex
        conversation: New conversation (after compaction)

    Returns:
        (reindexed_summary, warnings)
    """
```

**Features**:
- ✅ `build_id_to_index_mapping()` - Build {msg_id: index} from new conversation
- ✅ `reindex_message_range()` - Update indices for single range
- ✅ `reindex_summary_item()` - Reindex all ranges in summary item
- ✅ `validate_reindexing()` - Verify reindexing succeeded
- ✅ `auto_remove_invalid_items()` - Remove items referencing deleted messages
- ✅ `tag_messages_with_ids()` - Ensure messages have _message_id for tracking
- ✅ `create_reindexing_report()` - Human-readable report

**Usage**:
```python
from reccli.reindexing import reindex_summary_after_compaction, tag_messages_with_ids

# Before compaction
tag_messages_with_ids(session.conversation)

# After compaction
reindexed_summary, warnings = reindex_summary_after_compaction(
    session.summary,
    compacted_conversation
)

session.summary = reindexed_summary
session.save()
```

**Status**: ✅ Complete - ready for Phase 7

---

## Safeguard #6: Monotonic Time & UTC ✅ COMPLETE

**Requirement**: Ensure timestamps are monotonic and in UTC timezone.

### Implementation

**File**: `reccli/timestamp_validation.py` (240 lines)

**Features**:
- ✅ `validate_monotonic_timestamps()` - Check timestamps always increase
- ✅ `validate_timezone_utc()` - Verify timestamps are UTC
- ✅ `normalize_timestamps_to_utc()` - Convert all to Unix timestamps
- ✅ `repair_non_monotonic_timestamps()` - Auto-fix out-of-order timestamps
- ✅ `add_monotonic_validation_to_verifier()` - Extend SummaryVerifier

**Validation**:
```python
from reccli.timestamp_validation import validate_monotonic_timestamps

is_valid, errors = validate_monotonic_timestamps(conversation)
# Returns (False, ["Message 2: timestamp 1.5 < previous 2.0"]) if non-monotonic
```

**Repair**:
```python
from reccli.timestamp_validation import repair_non_monotonic_timestamps

repaired, warnings = repair_non_monotonic_timestamps(conversation)
# Interpolates missing/wrong timestamps
```

**How Parser Generates Timestamps**:
- Timestamps come from terminal events: `[timestamp, type, data]`
- Parser extracts timestamp from first event in each message
- User messages: `timestamp = user_input_start_time`
- Assistant messages: `timestamp = events[pending_output_start][0]`
- Already monotonic by construction (events are sequential)

**Status**: ✅ Complete - validation + repair ready

---

## Safeguard #7: Validation Pass on Write ✅ COMPLETE

**Requirement**: Run comprehensive validation before writing .devsession file.

### Implementation

**Status**: ✅ Already implemented in summarizer.py, now added to save path

**Integrated In**:

1. **`summarizer.py:summarize_session()`** - Already had validation:
```python
# Step 6: Verification pass
verifier = SummaryVerifier(conversation)
is_valid, errors = verifier.verify_summary(summary)

if not is_valid:
    print("⚠️  Summary verification found issues:")
    # ... print errors ...

    # Auto-fix
    summary, warnings = verifier.auto_fix_summary(summary)
```

2. **`devsession.py:save()`** - ✅ NEW: Added validation on save:
```python
def save(self, path: Path, skip_validation: bool = False) -> None:
    """Save with validation"""
    if not skip_validation and self.summary and self.conversation:
        from .summary_verification import SummaryVerifier
        from .reindexing import tag_messages_with_ids

        # Ensure messages have IDs
        tag_messages_with_ids(self.conversation)

        # Validate
        verifier = SummaryVerifier(self.conversation)
        is_valid, errors = verifier.verify_summary(self.summary)

        if not is_valid:
            # Try auto-fix
            fixed_summary, warnings = verifier.auto_fix_summary(self.summary)

            # Validate fixed
            is_valid_after_fix, errors_after_fix = verifier.verify_summary(fixed_summary)

            if is_valid_after_fix:
                self.summary = fixed_summary
            else:
                raise ValueError(f"Cannot save: validation failed")
```

**Features**:
- ✅ Validates before every save
- ✅ Auto-fixes recoverable errors
- ✅ Raises exception if unfixable
- ✅ Can skip with `skip_validation=True` (emergency use only)

**Status**: ✅ Complete - full validation pipeline integrated

---

## Safeguard #8: Compaction Safety Net ✅ COMPLETE

**Requirement**: Log compaction operations to enable recovery.

### Implementation

**File**: `reccli/compaction_log.py` (407 lines)

**Features**:

```python
class CompactionLog:
    """Manage compaction safety log"""

    # Core logging
    def log_compaction_start(session_data_before, plan) -> operation_id
    def create_backup(operation_id, session_data) -> Path
    def log_reindexing(operation_id, items_updated, warnings)
    def log_validation(operation_id, passed, errors)
    def log_compaction_complete(operation_id, session_data_after, success, error)

    # Recovery
    def rollback_to_backup(operation_id) -> Path
    def get_last_successful_compaction() -> Optional[Dict]
    def get_compaction_history(limit=10) -> List[Dict]

    # Maintenance
    def cleanup_old_backups(keep=5) -> List[Path]
    def print_history(limit=10)  # Human-readable output
```

**Log Format**: `.devsession-compaction-log.jsonl` (JSONL)

```jsonl
{"timestamp": "2025-11-02T10:30:00Z", "operation": "compaction_start", "operation_id": "compact_20251102_103000", "checksum_before": "abc123", "plan": {...}}
{"timestamp": "2025-11-02T10:30:05Z", "operation": "backup_created", "operation_id": "compact_20251102_103000", "backup_path": "session-backup-compact_20251102_103000.devsession"}
{"timestamp": "2025-11-02T10:30:10Z", "operation": "reindexing", "operation_id": "compact_20251102_103000", "summary_items_updated": 15, "warnings": []}
{"timestamp": "2025-11-02T10:30:12Z", "operation": "validation", "operation_id": "compact_20251102_103000", "validation_passed": true, "errors": []}
{"timestamp": "2025-11-02T10:30:15Z", "operation": "compaction_complete", "operation_id": "compact_20251102_103000", "success": true, "checksum_after": "def456"}
```

**Compaction Workflow** (with safety):

```python
from reccli.compaction_log import CompactionLog

log = CompactionLog(session_path)

# 1. Log start
operation_id = log.log_compaction_start(session.to_dict(), plan)

# 2. Create backup
backup_path = log.create_backup(operation_id, session.to_dict())

# 3. Perform compaction
compacted_conversation = remove_messages(session.conversation, plan)

# 4. Reindex
from reccli.reindexing import reindex_summary_after_compaction
reindexed_summary, warnings = reindex_summary_after_compaction(
    session.summary,
    compacted_conversation
)
log.log_reindexing(operation_id, len(reindexed_summary), warnings)

# 5. Validate
from reccli.summary_verification import SummaryVerifier
verifier = SummaryVerifier(compacted_conversation)
is_valid, errors = verifier.verify_summary(reindexed_summary)
log.log_validation(operation_id, is_valid, errors)

if not is_valid:
    # ROLLBACK!
    log.rollback_to_backup(operation_id)
    raise Exception("Compaction failed validation")

# 6. Commit
session.conversation = compacted_conversation
session.summary = reindexed_summary
session.save()
log.log_compaction_complete(operation_id, session.to_dict(), True)
```

**Recovery**:
```bash
# View history
log.print_history()

# Rollback last compaction
log.rollback_to_backup("compact_20251102_103000")
```

**Status**: ✅ Complete - full safety net with rollback ready

---

## Summary Matrix

| # | Safeguard | Status | Priority | Blocking |
|---|-----------|--------|----------|----------|
| 1 | Stable Anchors | ✅ Complete | - | - |
| 2 | Range Semantics | ✅ Complete | - | - |
| 3 | Multi-Span Support | ⏸️ Evaluated | Low | No |
| 4 | Precision Quotes (char_span) | ⏸️ Evaluated | Medium | No |
| 5 | Reindexing After Transforms | ✅ Complete | - | - |
| 6 | Monotonic Time & UTC | ✅ Complete | - | - |
| 7 | Validation on Write | ✅ Complete | - | - |
| 8 | Compaction Safety Net | ✅ Complete | - | - |

## Next Steps

### Immediate (Before Compaction Feature)
1. ✅ Safeguard #1: Complete
2. ✅ Safeguard #2: Complete
3. 🚧 Safeguard #5: Implement reindexing logic
4. 🚧 Safeguard #7: Integrate validation into write path
5. 🚧 Safeguard #8: Design and implement compaction safety net

### Near-Term (Phase 7)
6. ⏸️ Safeguard #6: Verify monotonic timestamps
7. ⏸️ Safeguard #4: Consider implementing char_span for retrieval UI

### Long-Term (As Needed)
8. ⏸️ Safeguard #3: Implement multi-span if use cases emerge

## Testing Plan

### Range Semantics Tests (Safeguards #1, #2)
- ✅ Test message_range construction from msg IDs
- ✅ Test retrieval with exclusive end
- ✅ Test validation catches ID/index mismatches
- ✅ Test edge cases (single message, full session, empty range)

### Reindexing Tests (Safeguard #5)
- 🚧 Test reindexing after removing first 30 messages
- 🚧 Test reindexing after removing middle messages
- 🚧 Test reindexing after removing last messages
- 🚧 Test reindexing with gaps in message IDs
- 🚧 Test validation passes after reindexing

### Compaction Tests (Safeguard #8)
- 🚧 Test full compaction workflow
- 🚧 Test rollback on validation failure
- 🚧 Test recovery from compaction log
- 🚧 Test checksum verification

## Version

- **Document version**: 1.0
- **RecCli version**: v2 Phase 4+
- **Last updated**: 2025-11-02
- **Contributors**: Claude Code, GPT-5 (advisor)
