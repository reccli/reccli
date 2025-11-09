# RecCli Paste Detection Architecture Document

## The Problem
We want to replicate Claude Code's behavior:
1. User pastes multi-line content
2. Input field shows: `[pasted +115 lines]` (NOT the full content)
3. User presses Enter
4. **Chat history shows the FULL pasted content** (CRITICAL)
5. LLM receives full content

## CURRENT STATUS: TypeScript + Ink Implementation (PARTIALLY WORKING)

**✅ WE ARE NOW USING TypeScript + Ink for UI, Python for Brain**

### Implemented Hybrid Architecture:
- **Frontend (UI Layer)**:
  - TypeScript + React + Ink framework
  - Location: `ui/src/components/`
  - Handles: Input, paste detection, message display, status bar

- **Backend (Brain Layer)**:
  - Python 3.14
  - Location: `backend/server.py`
  - Handles: .devsession management, LLM API calls, token counting

- **Communication**:
  - JSON-RPC over subprocess stdio pipes
  - TypeScript spawns Python process, exchanges messages
  - Location: `ui/src/bridge/python.ts`

### Component Status:
- ✅ **TypeScript + Ink UI**: Terminal UI with paste detection working
- ✅ **Python Backend**: .devsession management, LLM communication via JSON-RPC working
- ✅ **Paste Detection**: Shows `[pasted +X lines, Y chars]` annotation correctly
- ✅ **LLM Integration**: Receives full content, generates responses correctly
- ❌ **CRITICAL BUG**: Pasted content is TRUNCATED/CORRUPTED when displayed in message history

### File Structure:
- **Frontend**: `ui/src/components/InputV3.tsx` - Accumulates paste chunks via `useInput` hook
- **Backend**: `backend/server.py` - Python JSON-RPC server handling LLM calls
- **Bridge**: `ui/src/bridge/python.ts` - TypeScript-Python communication
- **Chat Logic**: `ui/src/components/Chat.tsx` - Manages messages state
- **Display**: `ui/src/components/MessageList.tsx` - Renders messages with Ink Text components

### What's Working
1. Paste detection via chunk accumulation (1014 + 863 + 439 = 2324 chars)
2. Annotation display: `[pasted +108 lines, 8,626 chars]`
3. LLM receives full content and responds correctly
4. Token counting and status bar

### THE CRITICAL BUG: Text Display Truncation

**Symptom**: When displaying pasted content in message history:
- Text appears corrupted: "Claude: Oh wow, that'syou think?" (merged words)
- Text is cut mid-word: "1. A nderstanding correctly" (split words)
- Large sections missing
- Words appear shuffled or out of order

**Evidence**:
- User pastes 8,626 chars
- InputV3 correctly accumulates: `PASTE FINALIZED: 8,626 chars, 108 lines`
- Chat component receives full text via `onSubmit(pasteBuffer, annotation)`
- MessageList displays corrupted/truncated version

**Root Cause Analysis** (SOLVED):
1. ✅ InputV3 accumulation: WORKING (ref-based, debounced)
2. ✅ Chat component storage: WORKING (stores in messages state)
3. ✅ **ROOT CAUSE IDENTIFIED**: Ink re-rendering entire component tree on every keystroke
4. ✅ **Symptom**: Screen jumping up/down on each key press (but not backspace)
5. ✅ **Mechanism**: Flexible Box layout recalculates heights on every render, causing terminal buffer overflow and interleaved stdout writes

### Solution Implemented

**The Fix: Prevent unnecessary re-renders and stabilize layout**

**Changes Made**:

1. **MessageList.tsx - Added React.memo**:
   - Wrapped component in `memo()` to prevent re-renders when input state changes
   - MessageList now only re-renders when `messages` or `isLoading` props actually change
   - This stops the cascade of re-renders on every keystroke

2. **Chat.tsx - Fixed Box layout with flexShrink/flexGrow**:
   - Header: `flexShrink={0}` - fixed height, never recalculates
   - Messages: `flexGrow={1} flexShrink={1} overflow="hidden"` - scrollable, stable container
   - Status bar: `flexShrink={0}` - fixed height
   - Input: `flexShrink={0}` - fixed height
   - **Result**: Ink no longer recalculates entire layout on every keystroke

**Why This Works**:
- Before: Input state change → entire component tree re-renders → Ink recalculates all Box heights → terminal buffer overflow → corrupted output
- After: Input state change → MessageList doesn't re-render (memo) → Box heights are fixed (flexShrink/flexGrow) → no layout recalculation → clean output

**Expected Results**:
- ✅ Screen no longer jumps on keystroke
- ✅ Pasted content displays completely and correctly
- ✅ No text corruption or truncation
- ✅ Performance improvement (fewer re-renders)

### Architecture Decision Rationale
1. **Best tool for each job**: Ink for UI, Python for existing middleware
2. **Preserve investment**: Keep all Python .devsession logic
3. **Match Claude Code capabilities**: Same UI framework = same capabilities
4. **Clear separation**: UI and business logic in different layers

## Current Reality Check

### What We're Using
- **Python**: 3.14
- **prompt_toolkit**: For terminal input
- **Terminal**: macOS Terminal.app
- **Input modes tried**:
  - `multiline=False`: Strips newlines, can't detect multi-line pastes
  - `multiline=True`: Preserves newlines but requires Esc+Enter to submit

### The Fundamental Issue
**prompt_toolkit's `prompt()` function is a blocking call that:**
1. Displays user input in real-time as it's typed/pasted
2. Returns the complete string only AFTER user submits
3. Does NOT give us control during input display

**This means:**
- When user pastes, prompt_toolkit IMMEDIATELY displays it
- We can't intercept and replace with `[pasted +115 lines]` during input
- We only get control AFTER Enter is pressed
- By then, the full paste has already been displayed

## Architecture Options

### Option 1: Bracketed Paste Mode (TRIED - FAILED)
```python
@kb.add(Keys.BracketedPaste, eager=True)
def handle_paste(event):
    # Replace paste with annotation
```
**Result**: Handler never fires on macOS Terminal.app
**Issue**: Terminal might not support or enable bracketed paste

### Option 2: Post-Input Detection (CURRENT - INADEQUATE)
```python
user_input = prompt("> ")
if len(user_input) > 400:
    print("[pasted +X lines]")  # Too late, already displayed
```
**Result**: Shows annotation AFTER paste already displayed
**Issue**: Can't prevent initial display

### Option 3: Custom Input Loop (NOT TRIED)
Replace prompt_toolkit with manual input handling:
```python
import sys, termios, tty

def get_input_with_paste_detection():
    # Manual character-by-character input
    # Detect rapid input = paste
    # Show annotation instead of content
```
**Pros**: Full control over display
**Cons**: Lose prompt_toolkit features (history, key bindings, etc.)

### Option 4: Application Mode (RECOMMENDED)
Use prompt_toolkit's Application class instead of simple prompt():
```python
from prompt_toolkit.application import Application
from prompt_toolkit.layout import Layout
from prompt_toolkit.widgets import TextArea

# Create full-screen app with custom rendering
# Control exactly what displays in input area
```
**Pros**: Full control while keeping prompt_toolkit features
**Cons**: More complex implementation

## The Real Claude Code Architecture

Claude Code likely uses one of these approaches:

1. **Custom Terminal Emulator**: Full control over rendering
2. **Modified readline/libedit**: Custom input handling at C level
3. **Application Framework**: Like prompt_toolkit.Application with custom widgets

## Recommended Solution

### Immediate Fix: Accept Current Limitations
1. Keep post-input detection
2. Show annotation after Enter (current behavior)
3. Document this as RecCli behavior vs Claude Code

### Proper Fix: Application Mode
Rewrite using prompt_toolkit.Application:

```python
from prompt_toolkit.application import Application
from prompt_toolkit.buffer import Buffer
from prompt_toolkit.layout.containers import Window, HSplit
from prompt_toolkit.layout.controls import BufferControl
from prompt_toolkit.layout.layout import Layout

class RecCliApp:
    def __init__(self):
        self.input_buffer = Buffer()
        self.chat_history = []
        self.paste_content = None

    def on_paste(self, paste_data):
        # Store actual content
        self.paste_content = paste_data
        # Show annotation in buffer
        self.input_buffer.text = f"[pasted +{len(paste_data.splitlines())} lines]"

    def on_enter(self):
        if self.paste_content:
            # Use actual paste content
            text = self.paste_content
            self.paste_content = None
        else:
            text = self.input_buffer.text
        # Process text...
```

## Decision Matrix

| Approach | Complexity | Control | Features | Claude-like |
|----------|-----------|---------|----------|-------------|
| Current (post-detect) | Low | Poor | Good | No |
| Custom char loop | Medium | Full | Poor | Yes |
| Application mode | High | Full | Full | Yes |
| Accept limitation | Zero | N/A | Good | No |

## Key Insight

**The paste has to display somewhere**. Our options are:
1. Let it display in prompt (current) - easiest but ugly
2. Prevent display entirely - requires low-level control
3. Display in separate area - requires Application mode

## Recommendation

1. **Short term**: Document current behavior as design choice
2. **Long term**: Implement Application mode for full control
3. **Alternative**: Research if newer terminals (iTerm2, Warp) have better bracketed paste support

## Testing Checklist

- [ ] Does bracketed paste work in iTerm2?
- [ ] Can we detect paste via timing (chars arriving <10ms apart)?
- [ ] Would Application mode actually solve this?
- [ ] Is the UX improvement worth the complexity?

## The Truth

**Claude Code is not using simple prompt() calls**. They have a custom application architecture that gives them full control over the display layer. To match their behavior exactly, we need to move beyond simple prompt() to a full Application architecture.