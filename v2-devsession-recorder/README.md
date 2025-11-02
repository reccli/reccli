# RecCli v2.0 - Pure Python .devsession Recorder + Native LLM

**Status**: ✅ Phase 0 + Native LLM + GUI Complete
**Date**: 2025-11-01
**Location**: `/Users/will/coding-projects/RecCli/v2-devsession-recorder/`

## What This Is

RecCli v2.0 provides **three powerful features**:

1. **Floating Button GUI** ⭐ NEW - One-click recording with macOS floating button
2. **Native LLM CLI** - Chat with Claude/GPT directly, auto-saves to .devsession
3. **Terminal Recorder** - Record ANY terminal session to .devsession format

**No external dependencies for recording. Just Python stdlib + optional LLM packages.**

## Files

```
v2-devsession-recorder/
├── reccli/                    # Main package
│   ├── __init__.py           # Package initialization
│   ├── llm.py                # LLMSession - Native LLM interface
│   ├── config.py             # API key management
│   ├── recorder.py           # DevsessionRecorder + GUI ⭐ NEW
│   ├── devsession.py         # DevSession file manager
│   └── cli.py                # CLI commands
│
├── reccli-v2.py              # CLI entry point
├── reccli-gui.py             # GUI entry point ⭐ NEW
├── requirements.txt          # Optional: anthropic, openai
├── NATIVE_LLM_GUIDE.md       # Native LLM quick start guide
└── README.md                 # This file
```

## Quick Start

### Option 1: Floating Button GUI (Easiest) ⭐

```bash
# Launch floating button (macOS only)
./reccli-gui.py
```

A floating "RecCli" button will appear in the top-right of your terminal window:
- **Click** to start/stop recording
- **Drag** to reposition
- **Right-click** for menu (Stats, Sessions folder, Quit)

### Option 2: Native LLM

```bash
# Install LLM packages
pip3 install anthropic openai

# Set API key
./reccli-v2.py config --anthropic-key sk-ant-YOUR_KEY

# Start chatting!
./reccli-v2.py chat

# Or ask one question
./reccli-v2.py ask "explain .devsession format"
```

**See [NATIVE_LLM_GUIDE.md](NATIVE_LLM_GUIDE.md) for complete documentation.**

### Option 3: Terminal Recorder (CLI)

```bash
# Record a session (no dependencies needed)
./reccli-v2.py record

# Record specific command
./reccli-v2.py record -n my-session
```

## Usage Examples

### Floating Button GUI
```bash
# Launch GUI
./reccli-gui.py

# The floating button will:
# - Follow your terminal window
# - Show "RecCli" when idle (with red record dot)
# - Show "Rec" when recording (with red stop square)
# - Auto-hide when terminal is minimized (keeps recording)
```

**How it works:**
1. Click button → Recording starts in a nested shell
2. Type commands as normal
3. Click button again (now showing stop icon) → Recording stops
4. Session saved to `~/.reccli/sessions/session_TIMESTAMP.devsession`

### Native LLM Chat
```bash
# Interactive chat with Claude
./reccli-v2.py chat --model claude

# Chat with GPT-5
./reccli-v2.py chat --model gpt5

# One-shot question
./reccli-v2.py ask "how do I use JWT tokens?"
```

### Terminal Recording (CLI)
```bash
# Record terminal session
./reccli-v2.py record

# List all sessions
./reccli-v2.py list

# Show session details
./reccli-v2.py show my-session

# Export to different formats
./reccli-v2.py export my-session-name           # Markdown (default)
./reccli-v2.py export my-session-name -f txt    # Plain text
./reccli-v2.py export my-session-name -f cast   # Asciinema .cast format
```

## .devsession Format

Output files are in `.devsession` format with this structure:

```json
{
  "format": "devsession",
  "version": "1.0",
  "session_id": "session_20251101_210500",
  "created": "2025-11-01T21:05:00",

  "terminal_recording": {
    "version": 2,
    "width": 80,
    "height": 24,
    "shell": "/bin/bash",
    "events": [
      [0.123, "o", "$ echo hello\r\n"],
      [0.456, "o", "hello\r\n"],
      [1.234, "i", "exit\r\n"]
    ]
  },

  "conversation": [],      // Parsed LLM conversation (Phase 2)
  "summary": null,         // AI-generated summary (Phase 4)
  "vector_index": null,    // Embeddings (Phase 5)
  "checksums": {},         // Event integrity
  "compaction_history": [] // Token management (Phase 7)
}
```

## Features Implemented

### Floating Button GUI ⭐ NEW
✅ **One-click recording** - Click to start/stop
✅ **Floating button** - Follows terminal window (macOS only)
✅ **Dark mode support** - Auto-detects system appearance
✅ **Terminal window tracking** - Locks to specific terminal
✅ **Auto-hide on minimize** - Hides when terminal minimized (keeps recording)
✅ **Drag to reposition** - Move button anywhere
✅ **Right-click menu** - Stats, sessions folder, quit
✅ **Visual feedback** - Different states for idle/recording/stopped

### Terminal Recorder
✅ **PTY-based terminal capture** - Records all I/O
✅ **Auto-save every 50 events** - Crash protection
✅ **Terminal resize handling** - Window size changes tracked
✅ **Multiple export formats** - .txt, .md, .cast
✅ **Session management** - List, show, export
✅ **Clean shutdown** - Proper terminal restoration

### Native LLM
✅ **Direct API calls** - Claude (Anthropic) and GPT (OpenAI)
✅ **Interactive chat mode** - Multi-turn conversations
✅ **One-shot query mode** - Single question/answer
✅ **API key management** - Secure local storage
✅ **Multiple models** - Claude Sonnet/Opus/Haiku, GPT-5/4
✅ **Auto-save to .devsession** - Clean conversation objects

## What's Next (Future Phases)

- **Phase 1**: DevSession file management improvements
- **Phase 2**: Conversation parser (extract LLM messages)
- **Phase 3**: Token counting
- **Phase 4**: AI summary generation
- **Phase 5**: Vector embeddings for semantic search
- **Phase 6**: Memory middleware (context hydration)
- **Phase 7**: Preemptive compaction at 190K tokens

## Differences from v1.0 (reccli-public)

| Feature | v1.0 (reccli-public) | v2.0 (this folder) |
|---------|----------------------|---------------------|
| Recording | Uses asciinema Python | Pure Python PTY |
| Output | .cast files | .devsession files |
| GUI | Tkinter floating button | ✅ Ported to v2 |
| Native LLM | Not available | ✅ Claude + GPT support |
| Dependencies | asciinema package | None (stdlib only) |
| Status | Production ready | Phase 0.5 complete |

## Testing

### Test GUI (Recommended)
```bash
# Launch floating button
./reccli-gui.py

# You should see:
# - Floating "RecCli" button in top-right of terminal
# - Button follows window when you move terminal
# - Click to start recording, click again to stop
```

### Test CLI Recorder
```bash
# Record a quick session
./reccli-v2.py record -n test-session

# Type a few commands in the spawned shell
$ echo "Hello from .devsession"
$ ls
$ exit

# List sessions
./reccli-v2.py list

# Show details
./reccli-v2.py show test-session

# Export to markdown
./reccli-v2.py export test-session
```

### Test Native LLM
```bash
# Set API key first
./reccli-v2.py config --anthropic-key YOUR_KEY

# Test chat
./reccli-v2.py chat --model claude

# Test one-shot
./reccli-v2.py ask "what is .devsession?"
```

## Technical Details

### How It Works

1. **PTY Spawning**: Uses Python's `pty.spawn()` to create a pseudo-terminal
2. **Event Capture**: Intercepts all I/O through custom read handlers
3. **Timestamp Recording**: Each event gets precise timestamp (seconds since start)
4. **Incremental Save**: Auto-saves every 50 events to prevent data loss
5. **Signal Handling**: Catches SIGWINCH for terminal resize events
6. **Clean Shutdown**: Restores terminal attributes on exit

### Event Types

- `"o"` - Output (from shell to terminal)
- `"i"` - Input (from user to shell)
- `"r"` - Resize (terminal window size change)

### File Location

Sessions are saved to: `~/.reccli/sessions/`

## Advantages of Pure Python

1. **No external dependencies** - Just Python stdlib
2. **Easy to modify** - All Python, no Rust/C to deal with
3. **Fast iteration** - No compilation step
4. **Portable** - Works anywhere Python 3.7+ runs
5. **Maintainable** - You can fix bugs yourself

## Known Limitations

- **GUI**: macOS only (uses AppleScript for terminal control)
- **Complex ANSI**: Basic terminal capture (advanced ANSI codes may not render perfectly)
- **Cloud sync**: Sessions stored locally only (no cloud sync yet)

These will be addressed in future phases.

## Troubleshooting

### GUI Issues

**"Tkinter not available"**
```bash
# macOS
brew install python-tk

# Ubuntu
sudo apt install python3-tk
```

**Button doesn't appear**
- Make sure you're running from a Terminal.app window (macOS only)
- Check if Tkinter is installed: `python3 -c "import tkinter"`

**Button doesn't follow terminal**
- This is expected behavior on non-macOS systems
- The GUI uses AppleScript for macOS Terminal.app

**Recording doesn't start**
- Check that `reccli-v2.py` is executable: `chmod +x reccli-v2.py`
- Verify path in error message

### CLI Recorder Issues

**Recording freezes**
- Press Ctrl+D to exit the nested shell
- Check `~/.reccli/sessions/` for .tmp files (incomplete saves)

**Events not captured**
- Some programs bypass PTY (e.g., password prompts)
- This is expected behavior for security

### Native LLM Issues

**"API key not found"**
```bash
./reccli-v2.py config --anthropic-key YOUR_KEY
# or
./reccli-v2.py config --openai-key YOUR_KEY
```

**"anthropic package not installed"**
```bash
pip3 install anthropic openai
```

## Integration Path

This v2 recorder is ready for production use:
1. ✅ Phase 0 complete - Pure Python PTY recording
2. ✅ Phase 0.5 complete - Native LLM + GUI
3. ⏳ Phase 1-7 - Intelligence layers (next)

---

**Project Plan**: See `/Users/will/coding-projects/RecCli/PROJECT_PLAN.md`
**Documentation**: See `/Users/will/coding-projects/RecCli/devsession/`
**Main Repository**: `/Users/will/coding-projects/RecCli/`
