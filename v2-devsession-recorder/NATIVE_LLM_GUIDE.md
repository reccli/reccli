# RecCli Native LLM - Quick Start Guide

**Status**: ✅ Complete and Ready to Use
**Date**: 2025-11-01

## What Is This?

RecCli v2.0 now includes a **native LLM CLI** - you can chat with Claude or GPT directly through RecCli, and every conversation is automatically saved to `.devsession` format.

**No wrappers. No external tools. Just RecCli + LLM API = Clean conversations.**

---

## Quick Start (3 Steps)

### 1. Install Dependencies

```bash
cd /Users/will/coding-projects/RecCli/v2-devsession-recorder
pip3 install anthropic openai
```

### 2. Set Your API Key

```bash
# For Claude (Anthropic)
./reccli-v2.py config --anthropic-key sk-ant-YOUR_KEY_HERE

# Or for GPT (OpenAI)
./reccli-v2.py config --openai-key sk-YOUR_KEY_HERE

# Set default model (optional)
./reccli-v2.py config --default-model claude
```

### 3. Start Chatting!

```bash
# Interactive chat (default model)
./reccli-v2.py chat

# Chat with specific model
./reccli-v2.py chat --model claude
./reccli-v2.py chat --model gpt4

# One-shot question
./reccli-v2.py ask "explain what .devsession files are"
```

---

## Usage Examples

### Interactive Chat

```bash
$ ./reccli-v2.py chat --model claude

🤖 RecCli Chat - claude
📝 Recording to: chat_claude_20251101_210530.devsession
Type 'exit' or press Ctrl+D to quit

You: help me build a JWT authentication system

Claude: I'll help you build a JWT authentication system. Let's start with...

[Conversation continues...]

You: exit

✅ Session saved
   File: /Users/will/.reccli/sessions/chat_claude_20251101_210530.devsession
   Messages: 12
   Duration: 145.3s
```

### One-Shot Questions

```bash
$ ./reccli-v2.py ask "what are the benefits of .devsession format?"

Claude: The .devsession format offers several key benefits:
1. Lossless conversation preservation...
2. Intelligent summarization layer...
[...]

✅ Session saved
   File: /Users/will/.reccli/sessions/ask_20251101_210600.devsession
   Messages: 2
   Duration: 2.1s
```

---

## .devsession Output

Every chat is saved to `.devsession` format:

```json
{
  "format": "devsession",
  "version": "1.0",
  "session_id": "session_20251101_210530",
  "created": "2025-11-01T21:05:30",

  "conversation": [
    {
      "role": "user",
      "content": "help me build JWT auth",
      "timestamp": 0.0
    },
    {
      "role": "assistant",
      "content": "I'll help you build JWT authentication...",
      "timestamp": 1.234
    }
  ],

  "summary": null,         // Will be added in Phase 4
  "vector_index": null,    // Will be added in Phase 5
  "terminal_recording": {  // Empty for native LLM chats
    "events": []
  }
}
```

**Clean, structured conversation** - no terminal parsing needed!

---

## Available Models

### Claude (Anthropic)
- `claude` - Claude 3.5 Sonnet (default, best for coding)
- `claude-sonnet` - Same as above
- `claude-opus` - Claude 3 Opus (most capable)
- `claude-haiku` - Claude 3.5 Haiku (fastest)

### GPT (OpenAI)
- `gpt4` - GPT-4 Turbo
- `gpt4o` - GPT-4o (latest)

---

## Configuration

### View Current Config

```bash
$ ./reccli-v2.py config

📋 Current Configuration

Sessions directory: /Users/will/.reccli/sessions
Default model: claude

API Keys:
  Anthropic: ✓ Set
  OpenAI: ✗ Not set
```

### Set API Keys

```bash
# Anthropic (for Claude)
./reccli-v2.py config --anthropic-key sk-ant-api03-...

# OpenAI (for GPT)
./reccli-v2.py config --openai-key sk-...
```

### Set Default Model

```bash
./reccli-v2.py config --default-model claude

# Now can just run:
./reccli-v2.py chat  # Uses claude automatically
```

---

## Session Management

### List All Sessions

```bash
$ ./reccli-v2.py list

📁 Sessions in /Users/will/.reccli/sessions

Name                           Duration     Events     Created
---------------------------------------------------------------------------
chat_claude_20251101_210530    145.3s       12         2025-11-01 21:05
ask_20251101_210600            2.1s         2          2025-11-01 21:06
```

### Show Session Details

```bash
$ ./reccli-v2.py show chat_claude_20251101_210530

📋 Session: session_20251101_210530
   File: /Users/will/.reccli/sessions/chat_claude_20251101_210530.devsession
   Created: 2025-11-01T21:05:30
   Updated: 2025-11-01T21:07:55

💬 Conversation: 12 messages
```

### Export Session

```bash
# Export to markdown
./reccli-v2.py export chat_claude_20251101_210530

# Export to plain text
./reccli-v2.py export chat_claude_20251101_210530 -f txt
```

---

## Advanced Usage

### Custom Session Names

```bash
./reccli-v2.py chat -n jwt-auth-discussion --model claude
```

### Custom Output Path

```bash
./reccli-v2.py chat -o ~/my-sessions/auth.devsession
```

### Override API Key (One-Time)

```bash
./reccli-v2.py chat --api-key sk-ant-temporary-key-...
```

---

## Comparison: Wrapper vs Native

### Old Way (Wrapper Mode)
```bash
$ reccli record -- claude
# RecCli spawns claude CLI
# Records terminal I/O (messy with ANSI codes)
# Need to parse terminal output to extract conversation
# .devsession has terminal_recording + conversation
```

### New Way (Native LLM) ⭐
```bash
$ reccli chat
# RecCli IS the LLM interface
# Calls API directly
# Already have clean conversation objects
# .devsession just has conversation (clean!)
```

**Native is simpler, cleaner, and better for .devsession.**

---

## Troubleshooting

### "anthropic package not installed"

```bash
pip3 install anthropic
```

### "openai package not installed"

```bash
pip3 install openai
```

### "Anthropic API key not found"

```bash
./reccli-v2.py config --anthropic-key sk-ant-YOUR_KEY
```

### "OpenAI API key not found"

```bash
./reccli-v2.py config --openai-key sk-YOUR_KEY
```

---

## What's Next?

This is **Phase 0.5** of the RecCli project. Coming soon:

- **Phase 4**: AI-powered summary generation for .devsession files
- **Phase 5**: Vector embeddings for semantic search across conversations
- **Phase 6**: Memory middleware (context hydration from .devsession)
- **Phase 7**: Preemptive compaction at 190K tokens

**The native LLM is just the beginning!**

---

## Files Created

```
v2-devsession-recorder/
├── reccli/
│   ├── llm.py          # LLMSession class (300 lines)
│   ├── config.py       # Config management (65 lines)
│   └── cli.py          # Updated with chat/ask/config commands
│
├── requirements.txt    # anthropic, openai
└── NATIVE_LLM_GUIDE.md # This file
```

---

**You're all set! Start chatting with:**

```bash
./reccli-v2.py chat
```

🎉
