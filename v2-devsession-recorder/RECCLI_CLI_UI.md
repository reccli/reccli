# RecCli Paste Detection Architecture Document

## The Problem
We want to replicate Claude Code's behavior:
1. User pastes multi-line content
2. Input field shows: `[pasted +115 lines]` (NOT the full content)
3. User presses Enter
4. **Chat history shows the FULL pasted content** (CRITICAL)
5. LLM receives full content

## CURRENT STATUS: TypeScript + Ink Implementation ✅ WORKING

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
- ✅ **TypeScript + Ink UI**: Terminal UI with paste detection working perfectly
- ✅ **Python Backend**: .devsession management, LLM communication via JSON-RPC working
- ✅ **Paste Detection**: Shows `[pasted +X lines, Y chars]` annotation correctly
- ✅ **LLM Integration**: Receives full content, generates responses correctly
- ✅ **Paste Display**: Full pasted content displays cleanly after Enter (fixed with `\r` to `\n` conversion)

### File Structure:
- **Frontend**: `ui/src/components/InputV3.tsx` - Accumulates paste chunks via `useInput` hook
- **Backend**: `backend/server.py` - Python JSON-RPC server handling LLM calls
- **Bridge**: `ui/src/bridge/python.ts` - TypeScript-Python communication
- **Chat Logic**: `ui/src/components/Chat.tsx` - Manages messages state
- **Display**: `ui/src/components/MessageList.tsx` - Renders messages with Ink Text components

### What's Working ✅ ALL FEATURES
1. ✅ Paste detection via chunk accumulation (paste arrives in ~1KB chunks)
2. ✅ Annotation display: `[pasted +117 lines, 8,625 chars]`
3. ✅ Annotation is editable text - can navigate with arrow keys, delete chars breaks the paste
4. ✅ Annotation validation - if broken, paste content is discarded on submit
5. ✅ Full content displays cleanly after Enter (no corruption)
6. ✅ LLM receives full content and responds correctly
7. ✅ Token counting and status bar
8. ✅ React.memo optimizations for performance
9. ✅ Arrow key navigation through input text
10. ✅ Terminal native scrollback for infinite message history
11. ✅ Tool calling support (read_file, write_file, list_directory, glob_files)

### THE CRITICAL BUG: Text Display Corruption - SOLVED ✅

**Symptom**: When displaying pasted content in message history:
- Text appears corrupted: "Claude: Oh wow, that'syou think?" (merged words)
- Text is cut mid-word: "1. A nderstanding correctly" (split words)
- Large sections missing
- Words appear shuffled or out of order

**Evidence**:
- User pastes 8,626 chars
- InputV3 debug shows: `PASTE FINALIZED: 8,626 chars, 1 lines` (should be 108 lines!)
- Debug shows: `Contains \n: false, Contains \r: true`
- Text corruption pattern matches carriage return overwrites

**Root Cause Analysis** (SOLVED):
1. ✅ InputV3 accumulation: WORKING (ref-based, debounced)
2. ✅ Chat component storage: WORKING (stores in messages state)
3. ✅ **ROOT CAUSE IDENTIFIED**: macOS Terminal sends `\r` (carriage return) instead of `\n` (newline) during paste operations
4. ✅ **Mechanism**: `\r` moves cursor to line start, causing text overlap and corruption
5. ✅ **Initial false hypothesis**: Thought it was Ink re-rendering - actually it was character encoding

### Solution Implemented - FINAL FIX ✅

**Key Fixes Implemented:**

1. **Character Encoding Fix (`\r` to `\n` conversion)**:
```typescript
// InputV3.tsx - Normalize line endings
const normalizedContent = pasteAccumulatorRef.current.replace(/\r/g, '\n');
```
- macOS Terminal sends `\r` (carriage return) instead of `\n` during paste
- Without conversion: text overwrites itself causing corruption
- With conversion: proper newlines → clean formatting

2. **Paste Accumulator Fix**:
```typescript
// Don't clear accumulator if paste timer is active
if (pasteTimerRef.current) {
  pasteAccumulatorRef.current += input;
  // Reset timer and continue accumulating
}
```
- Small chunks (like last char "m") were clearing the accumulator
- Now checks for active timer before clearing
- Accumulates all chunks including trailing single characters

3. **Functional setState for Current Values**:
```typescript
// Use functional form to get current inputValue in timeout closure
setInputValue(currentInput => {
  const annotation = `[pasted +${lines} lines, ${chars} chars]`;
  return currentInput + annotation;
});
```
- Fixes stale closure bug where inputValue was old in setTimeout
- Preserves text typed before paste started

4. **Annotation as Editable Text**:
```typescript
// Annotation stored in inputValue, paste content in pasteBuffer
const annotation = `[pasted +${lines} lines, ${chars}]`;
setInputValue(prev => prev + annotation);
setPasteBuffer(normalizedContent);
```
- Annotation is regular editable text in input field
- Arrow keys navigate through it normally
- Deleting chars breaks the paste link

5. **Annotation Validation on Submit**:
```typescript
if (inputValue.includes(expectedAnnotation)) {
  // Intact - submit with pasteBuffer
  const fullContent = inputValue.replace(expectedAnnotation, '') + pasteBuffer;
} else {
  // Broken - just submit inputValue
  onSubmit(inputValue);
}
```
- Checks if annotation text is still intact
- If broken, paste content is discarded
- Matches Claude Code behavior

6. **Terminal Native Scrollback**:
```typescript
// Remove overflow="hidden" and flexGrow constraints
<Box flexDirection="column" paddingX={1}>
  <MessageList messages={messages} isLoading={isLoading} />
</Box>
```
- Let terminal handle scrolling naturally
- Supports infinite message history
- Uses terminal's native scroll buffer

**Results**:
- ✅ Paste detection: Shows `[pasted +117 lines, 8,625 chars]`
- ✅ Annotation is navigable and editable
- ✅ Breaking annotation discards paste
- ✅ Full content displays correctly after Enter
- ✅ LLM receives complete, properly formatted text
- ✅ Arrow keys work for navigation
- ✅ Infinite message history via terminal scrollback

### Architecture Decision Rationale
1. **Best tool for each job**: Ink for UI, Python for existing middleware
2. **Preserve investment**: Keep all Python .devsession logic
3. **Match Claude Code capabilities**: Same UI framework = same capabilities
4. **Clear separation**: UI and business logic in different layers

## Debugging Journey - Lessons Learned

### Initial Approaches (Abandoned)
- **Python prompt_toolkit**: Couldn't intercept paste before display
  - `multiline=False`: Strips newlines, can't detect multi-line pastes
  - `multiline=True`: Preserves newlines but requires Esc+Enter to submit
  - Fundamental limitation: `prompt()` is blocking, no pre-display control

### Critical Insights That Led to Success

**1. Terminal Character Encoding Issue**
- macOS Terminal sends `\r` (carriage return) instead of `\n` (newline) during paste
- Without conversion, `\r` causes cursor to return to start of line
- Text overwrites itself → appears corrupted/merged/split
- **Solution**: Single line `replace(/\r/g, '\n')` fixes everything

**2. Systematic Debugging Approach**
- Don't assume - verify with debug logging
- Check actual character content: `JSON.stringify()`, `includes('\n')`, `includes('\r')`
- Trace data through each layer: InputV3 → Chat → MessageList
- Found issue when we checked: `Contains \n: false, Contains \r: true`

**3. Ink Can Handle Large Text (When Done Right)**
- Initial hypothesis: Ink has character limits or rendering bugs
- Reality: Ink works fine, we had character encoding bug
- React.memo and fixed layouts help performance but weren't the core fix

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

## Final Implementation Summary

### Architecture Choice: TypeScript + Ink + Python
- **UI Layer**: TypeScript with React and Ink framework
- **Business Logic**: Python (LLM API, .devsession management)
- **Communication**: JSON-RPC over subprocess stdio

### Key Technical Details
1. **Paste Detection**: Chunks > 10 chars accumulated in ref with 300ms debounce
2. **Character Normalization**: `replace(/\r/g, '\n')` converts terminal paste encoding
3. **Performance**: React.memo on MessageList, fixed Box layouts
4. **Raw Mode**: `setRawMode(true)` for direct stdin access

### Code Locations
- **InputV3.tsx**: Paste detection and accumulation
- **Chat.tsx**: Message state management and Python bridge
- **MessageList.tsx**: Memoized message rendering
- **backend/server.py**: Python JSON-RPC server

### Matches Claude Code Behavior
- ✅ Shows `[pasted +X lines]` during input
- ✅ Expands full content after Enter
- ✅ Clean text display without corruption
- ✅ Works with large pastes (8,000+ characters)