# Phase 7: Testing Guide

**Status note:** Historical testing guide from the Phase 7 delivery pass. Some commands and dependency examples reflect the phase-era workflow rather than the current CLI surface. Use [PROJECT_PLAN.md](/Users/will/coding-projects/RecCli/PROJECT_PLAN.md), [README.md](/Users/will/coding-projects/RecCli/README.md), and the live CLI help for the current runtime contract.

**Date**: 2025-11-07 (Post-Audit)
**Status**: ✅ Bugs Fixed - Ready for Real Testing

---

## What Was Fixed

### 🔧 Critical Bugs Fixed

1. **LLM Client Integration** ✅
   - Added `llm_client` parameter to `PreemptiveCompactor.__init__`
   - `llm.py` now passes `self.client` to compactor
   - SessionSummarizer receives the client correctly

2. **API Signature Corrections** ✅
   - Changed `generate_summary()` → `summarize_session()` (correct method name)
   - Removed invalid `single_stage` parameter
   - Added graceful handling when no LLM client available

3. **CLI Command Updates** ✅
   - `reccli compact` and `reccli check-tokens` work without LLM client
   - Clear warnings when running without AI summary generation
   - No crashes when client is None

---

## Prerequisites

### 1. Install Dependencies

```bash
cd /Users/will/coding-projects/RecCli

# Install required packages
pip3 install anthropic openai tiktoken jsonschema rank-bm25
```

### 2. Configure API Key

**For Anthropic (Claude)**:
```bash
./reccli-v2.py config --anthropic-key sk-ant-YOUR_KEY_HERE
```

**For OpenAI (GPT)**:
```bash
./reccli-v2.py config --openai-key sk-YOUR_KEY_HERE
```

**Verify configuration**:
```bash
./reccli-v2.py config
```

You should see:
```
🔑 RecCli Configuration

API Keys:
  Anthropic: sk-ant-...abc (configured ✅)
  OpenAI: (not set)

Default Model: claude
```

---

## Testing Strategy

### Level 1: Quick Smoke Tests (5 minutes)

**Test 1: Check Token Status**
```bash
# Create a small test session first
./reccli-v2.py chat --model claude
# (Type a few messages, then exit)

# Check tokens on that session
./reccli-v2.py check-tokens session_TIMESTAMP
```

**Expected Output**:
```
📊 Token Count Status - session_20251107_XXXXXX

Current tokens: 1,234
Warn threshold: 180,000 (90%)
Compact threshold: 190,000 (95%)
Percentage: 0.6%
Remaining: 188,766 tokens
Status: OK
Compaction count: 0

✅ OK: Token count is healthy
```

**Test 2: Manual Compaction (No AI Summary)**
```bash
./reccli-v2.py compact session_TIMESTAMP
```

**Expected Output**:
```
⚠️  Note: Manual compaction from CLI runs without AI summary generation
   (Use 'reccli chat' for full compaction with AI summaries)

============================================================
🔄 PREEMPTIVE COMPACTION TRIGGERED
============================================================
📊 Context approaching limit: 1,234 tokens
📦 Compacting with .devsession strategy...

💾 Backup created: session-compaction-log-backup-compact_20251107_XXXXXX.json
📝 Generating summary with custom prompt...
   ⚠️  No LLM client available - cannot generate summary
   ⚠️  Summary generation skipped (no LLM client)
...
✅ Compaction complete
```

This should work WITHOUT errors (just warnings about no AI summary).

**Test 3: Checkpoints**
```bash
# Add a checkpoint
./reccli-v2.py checkpoint add "test-checkpoint" -c "testing Phase 7"

# List checkpoints
./reccli-v2.py checkpoint list

# Check diff (should be empty)
./reccli-v2.py checkpoint diff-since CP_01
```

### Level 2: Real Chat Test (30 minutes)

**Test 4: Chat with Compaction Enabled**

```bash
./reccli-v2.py chat --model claude
```

**What to Watch For**:
1. Should see: `🔄 Preemptive compaction enabled (triggers at 190K tokens)`
2. Have a normal conversation (10-20 message exchanges)
3. Exit chat
4. Check session was saved

**Test 5: Long Conversation (Stress Test)**

This is THE big test, but requires patience:

```bash
./reccli-v2.py chat --model claude

# Now have a LONG conversation
# Keep going until you hit 180K tokens
# (This will take a while - maybe ask Claude to help you write a large codebase)
```

**Watch for**:
- ⚠️  Warning at 180K tokens
- 🔄 Auto-compaction at 190K tokens
- ✅ Compaction completes successfully
- Chat continues seamlessly

### Level 3: Full End-to-End Test (2+ hours)

**The Ultimate Test**: Build up a session to 190K+ tokens naturally.

**Approach**:
```bash
# Start a real coding session
./reccli-v2.py chat --model claude

# Work on a real project with Claude
# Ask complex questions
# Generate code
# Debug issues
# Keep going...
```

**Compaction should trigger automatically at 190K.**

---

## Expected Behavior at Each Stage

### Before 180K Tokens
- ✅ Normal chat
- ✅ No warnings
- ✅ All messages recorded

### At 180K Tokens
```
⚠️  Context Warning: 180,000 tokens (94.7% of limit)
   Compaction will trigger at 190,000 tokens (10,000 remaining)
```

### At 190K Tokens
```
============================================================
🔄 PREEMPTIVE COMPACTION TRIGGERED
============================================================
📊 Context approaching limit: 190,234 tokens
📦 Compacting with .devsession strategy...

💾 Backup created: ...
📝 Generating summary with custom prompt...
   ✓ Summary generated (15 decisions, 8 code changes)
🔢 Ensuring embeddings are up to date...
   ✓ Embeddings ready
📌 Extracted 20 recent messages as implicit goal
🔍 Searching for relevant context spans...
   ✓ Found 3 relevant spans
🔮 Generating Work Package Continuity predictions...
   ✓ 3 artifacts predicted
🎯 Building compacted context...
   ✓ Compacted context: 27,845 tokens
💾 Saving .devsession file...
   ✓ Saved to session_20251107_143045.devsession

============================================================
✅ COMPACTION COMPLETE
============================================================
📉 Reduction: 190,234 → 27,845 tokens
💰 Saved: 162,389 tokens (85.4% reduction)
📄 Full session saved with 287 messages
🔍 Context ready: Summary + 20 recent + 3 relevant spans
💬 Continuing conversation with focused context...
============================================================

You: [Chat continues normally]
```

### After Compaction
- ✅ Chat continues seamlessly
- ✅ Full session saved to .devsession file
- ✅ Can keep chatting (builds up to 190K again)
- ✅ Can compact multiple times

---

## Common Issues & Solutions

### Issue: "No module named 'anthropic'"

**Solution**:
```bash
pip3 install anthropic
```

### Issue: "API key not found"

**Solution**:
```bash
./reccli-v2.py config --anthropic-key YOUR_KEY
```

### Issue: "tiktoken not installed"

**Solution**:
```bash
pip3 install tiktoken
```

This is recommended but not critical. Token counting will be less accurate without it.

### Issue: Manual compaction says "no LLM client"

**This is expected!** CLI commands don't have LLM clients. They show:
```
⚠️  Note: Manual compaction from CLI runs without AI summary generation
   (Use 'reccli chat' for full compaction with AI summaries)
```

To get full compaction with AI summaries, you must trigger it during a `reccli chat` session.

### Issue: Compaction fails with error

**Check**:
1. Do you have API key configured?
2. Is the API key valid?
3. Do you have internet connection?
4. Check error message for details

**Emergency Recovery**:
```bash
# Check compaction log
ls ~/reccli/sessions/*-compaction-log.jsonl

# Rollback if needed (implementation has rollback support)
```

---

## Success Criteria

✅ **Phase 7 is working correctly if**:

1. `reccli check-tokens` shows correct token count
2. `reccli compact` completes without crashing (even if it skips AI summary)
3. `reccli chat` shows "Preemptive compaction enabled" message
4. Chat session saves successfully
5. Checkpoints can be created and listed
6. **BONUS**: Compaction actually triggers at 190K and chat continues

---

## What to Report Back

After testing, please share:

1. **Which tests passed?** (Level 1, 2, or 3)
2. **Did compaction trigger automatically?** (if you got to 190K)
3. **Any errors or crashes?**
4. **Session file location** (so we can inspect the .devsession file)
5. **Your impressions** - Does it work as expected?

---

## Quick Reference

```bash
# Configure
./reccli-v2.py config --anthropic-key YOUR_KEY

# Start chat (compaction auto-enabled)
./reccli-v2.py chat --model claude

# Check status
./reccli-v2.py check-tokens SESSION_NAME

# Manual compaction (testing only)
./reccli-v2.py compact SESSION_NAME

# Checkpoints
./reccli-v2.py checkpoint add "label"
./reccli-v2.py checkpoint list
./reccli-v2.py checkpoint diff-since CP_01
```

---

## Next Steps After Testing

1. **If it works**: Move to production use! 🎉
2. **If bugs found**: Report them, we'll fix
3. **If it works well**: Consider Phase 8/9 for polish
4. **If it's good enough**: Ship it as-is

---

**Status**: All critical bugs fixed, ready for real-world testing with API key.

**Last Updated**: 2025-11-07 (Post-Audit)
