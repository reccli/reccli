# Phase 7: Preemptive Compaction - Walkthrough

## What Is This?

Phase 7 adds **automatic intelligent compaction** at 190K tokens. When you're chatting with Claude and approach the context limit, RecCli automatically:

1. Saves your full conversation to `.devsession` format
2. Generates a summary using your custom prompt
3. Uses vector search to find relevant context
4. Builds a compact 25-30K token context
5. Continues the chat seamlessly

**Result**: Never hit Claude Code's 200K limit. Never lose context. Always use YOUR custom compaction strategy.

---

## Quick Start: Test Compaction

### 1. Start a Chat with Compaction Enabled

```bash
cd /Users/will/coding-projects/RecCli

# Start chat (compaction auto-enabled)
./reccli-v2.py chat --model claude
```

You'll see:
```
🤖 RecCli Chat - claude
📝 Recording to: session_20251107_143045.devsession
🔄 Preemptive compaction enabled (triggers at 190K tokens)
Type 'exit' or press Ctrl+D to quit

You:
```

### 2. Monitor Token Count

While chatting (or after), check your token status:

```bash
./reccli-v2.py check-tokens session_20251107_143045
```

Output:
```
📊 Token Count Status - session_20251107_143045

Current tokens: 45,234
Warn threshold: 180,000 (90%)
Compact threshold: 190,000 (95%)
Percentage: 23.8%
Remaining: 144,766 tokens
Status: OK
Compaction count: 0

✅ OK: Token count is healthy
```

### 3. Watch Automatic Compaction

When you hit 180K tokens, you'll see warnings:

```
⚠️  Context Warning: 180,000 tokens (94.7% of limit)
   Compaction will trigger at 190,000 tokens (10,000 remaining)
```

At 190K, compaction automatically triggers:

```
============================================================
🔄 PREEMPTIVE COMPACTION TRIGGERED
============================================================
📊 Context approaching limit: 190,234 tokens
📦 Compacting with .devsession strategy...

💾 Backup created: session-compaction-log-backup-compact_20251107_143422.json
📝 Generating summary with custom prompt...
   ✓ Summary generated (23 decisions, 15 code changes)
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

You: [continues seamlessly]
```

---

## Manual Compaction

Want to compact before hitting the limit?

```bash
# Manually trigger compaction at any time
./reccli-v2.py compact session_20251107_143045
```

---

## Checkpoints: Mark Milestones

### Create a Checkpoint

```bash
# Mark current state as a checkpoint
./reccli-v2.py checkpoint add "pre-release" -c "all tests passing"
```

Output:
```
✅ Checkpoint created: CP_01
   Label: pre-release
   Criteria: all tests passing
   Time: 2025-11-07T14:34:22
   Message index: 142
   Tokens: 45,234
```

### List Checkpoints

```bash
./reccli-v2.py checkpoint list
```

Output:
```
📍 Checkpoints in session_20251107_143045

CP_01: pre-release
   Time: 2025-11-07T14:34:22
   Criteria: all tests passing
   Message index: 142

CP_02: after-refactor
   Time: 2025-11-07T15:12:10
   Message index: 218
```

### See What Changed Since Checkpoint

```bash
./reccli-v2.py checkpoint diff-since CP_01
```

Output:
```
📍 Changes since CP_01: pre-release
⏱️  Created: 2025-11-07T14:34:22
⌛ Time elapsed: 2h 15m
📊 23 changes

🎯 Decisions (5)
   • Switch from modal to sidebar for export dialog
   • Use RAG for context loading instead of full conversation
   • Implement preemptive compaction at 190K tokens
   • Add checkpoint system for manual milestones
   • Use vector search for relevant span retrieval

💻 Code Changes (12)
   • Added preemptive_compaction.py (new module)
   • Updated llm.py to integrate compaction
   • Modified cli.py with new commands
   ...

✅ Problems Solved (4)
   • Fixed memory leak in recorder
   • Resolved embedding dimension mismatch
   ...

⚠️  Open Issues (2)
   • Need to test with GPT-5 model
   • Documentation needs updating
```

---

## How It Works: The Magic

### Before Compaction (190K tokens)
```
Full conversation:
- Message 1: "Let's build authentication"
- Message 2: "Sure, here's the plan..."
- ...
- Message 287: "Almost done, just need to test..."

Token count: 190,234
Status: 🔴 CRITICAL
```

### After Compaction (28K tokens)
```
Compacted context:
1. Summary (5K tokens):
   - "Built authentication with JWT tokens"
   - "Added export dialog with format selection"
   - "Implemented preemptive compaction"

2. Recent messages (20K tokens):
   - Message 268-287 (last 20 messages)

3. Relevant spans (2K tokens):
   - msg_42-50: "JWT token decision"
   - msg_134-142: "Export dialog bug fix"
   - msg_201-210: "Compaction strategy"

4. WPC predictions (1K tokens):
   - "Likely to work on: test_auth.py"
   - "Related files: auth.py, middleware.py"

Token count: 27,845
Status: ✅ OK
```

### The Full Conversation Is Saved

Even though the LLM only sees 28K tokens, your full 190K conversation is saved in the `.devsession` file with:
- All 287 messages
- AI-generated summary
- Vector embeddings for semantic search
- Compaction history
- Checkpoints

**You lose NOTHING.** You can always:
- Search the full history
- Load more context
- Export to different formats
- Resume from any checkpoint

---

## Troubleshooting

### "Module not found" Error

Make sure you're in the right directory:
```bash
cd /Users/will/coding-projects/RecCli
```

### Compaction Doesn't Trigger

Check if you have enough tokens:
```bash
./reccli-v2.py check-tokens session-name
```

Need at least 190K tokens to trigger auto-compaction.

### Want to Test Without 190K Tokens?

Manually trigger compaction:
```bash
./reccli-v2.py compact session-name
```

---

## What's Different from File Compaction?

RecCli has TWO types of compaction:

### 1. **Storage Compaction** (OLD - Phase 2.5)
```bash
# This removes terminal events to save disk space
# 189KB → 2KB file size
```

This is for **storage**, not for LLM context.

### 2. **Context Compaction** (NEW - Phase 7)
```bash
# This reduces token count for LLM continuation
# 190K tokens → 28K tokens
```

This is for **beating Claude Code's 200K limit**.

**Phase 7 is the breakthrough** - it's what lets you have infinite conversations without hitting limits.

---

## Next Steps

1. **Use it for real work**: Start a long coding session
2. **Hit 190K tokens**: Keep chatting until compaction triggers
3. **Verify it works**: Check that chat continues seamlessly
4. **Report issues**: Note any rough edges

The system is ready to test! 🚀

---

## Commands Summary

```bash
# Start chat with compaction
./reccli-v2.py chat --model claude

# Check token status
./reccli-v2.py check-tokens <session>

# Manual compaction
./reccli-v2.py compact <session>

# Create checkpoint
./reccli-v2.py checkpoint add "<label>" -c "<criteria>"

# List checkpoints
./reccli-v2.py checkpoint list

# Diff since checkpoint
./reccli-v2.py checkpoint diff-since CP_01
```

---

**Built**: 2025-11-07
**Phase**: 7 of 12 (Preemptive Compaction)
**Status**: ✅ Ready for Testing
