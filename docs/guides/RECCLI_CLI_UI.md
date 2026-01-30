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
6. ✅ LLM receives full content and responds correctly (300ms timer captures all trailing chars)
7. ✅ Token counting and status bar
8. ✅ React.memo optimizations for performance
9. ✅ Arrow key navigation through input text
10. ✅ Command history (up/down arrows) with saved input restoration
11. ✅ Escape key cancellation of LLM requests
12. ✅ Terminal native scrollback for infinite message history
13. ✅ Tool calling support (read_file, write_file, list_directory, glob_files)

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

2. **Paste Accumulator Fix - TIMER DELAY CRITICAL**:
```typescript
// Don't clear accumulator if paste timer is active OR accumulator has content
if (pasteTimerRef.current || pasteAccumulatorRef.current.length > 0) {
  pasteAccumulatorRef.current += input;
  // Reset timer and continue accumulating
}
```
- Small chunks (like last char "m") were clearing the accumulator
- Now checks for active timer OR non-empty accumulator before clearing
- Accumulates all chunks including trailing single characters
- **CRITICAL**: Increased timer from 100ms to 300ms - final characters were arriving >100ms after last chunk
- Without 300ms delay, final chars like "m" arrive after timer fires, get discarded
- 300ms gives enough buffer for all paste chunks to accumulate

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
1. **Paste Detection**: Chunks > 10 chars accumulated in ref with 300ms debounce (CRITICAL: must be 300ms, not 100ms)
2. **Character Normalization**: `replace(/\r/g, '\n')` converts terminal paste encoding
3. **Performance**: React.memo on MessageList, fixed Box layouts
4. **Raw Mode**: `setRawMode(true)` for direct stdin access
5. **Command History**: Up/down arrows with savedInput state to preserve current unsaved text
6. **Request Cancellation**: Escape key + AbortController pattern for cancelling LLM requests

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
- ✅ Tool calls stream in real-time (implemented Nov 10, 2025)

## Streaming Tool Call Display - ✅ IMPLEMENTED (Nov 10, 2025)

### What Was The Issue
- Tool calls (read_file, write_file, etc.) were processed synchronously in Python
- Backend waited for all tool calls to complete before sending response
- UI displayed entire response as one block
- User didn't see incremental progress

### How It Was Solved
Successfully implemented real-time streaming of tool calls matching Claude Code behavior:

### Target Behavior (Claude Code style)
```
You: read the config file

Claude: I'll read the config file for you.

[Tool Use: read_file]
path: config.json

[Tool Result]
{
  "model": "claude",
  "maxTokens": 150000
}

Looking at the config, I can see...
```

Each step appears incrementally as it happens, not all at once.

### Implementation Plan

#### 1. Backend Streaming Events (backend/server.py)
Add new event types in JSON-RPC protocol:
```python
# Event types
{
  "id": "msg_123",
  "type": "text_chunk",
  "content": "I'll read the config file for you."
}

{
  "id": "msg_123",
  "type": "tool_call_start",
  "tool_name": "read_file",
  "tool_input": {"path": "config.json"}
}

{
  "id": "msg_123",
  "type": "tool_call_result",
  "tool_name": "read_file",
  "result": "{\n  \"model\": \"claude\"\n}"
}

{
  "id": "msg_123",
  "type": "final_response",
  "complete": true
}
```

#### 2. Backend Changes Needed

**A. Modify `_call_anthropic()` in llm.py:**
```python
def _call_anthropic_streaming(self, on_event):
    """Stream events for tool calls and text"""
    response = self.client.messages.create(
        model=self.model,
        messages=self.messages,
        tools=self.tools,
        max_tokens=8000
    )

    # Emit text chunks
    for block in response.content:
        if block.type == "text":
            on_event("text_chunk", {"content": block.text})
        elif block.type == "tool_use":
            on_event("tool_call_start", {
                "tool_name": block.name,
                "tool_input": block.input
            })

            # Execute tool
            result = self._execute_tool(block.name, block.input)

            on_event("tool_call_result", {
                "tool_name": block.name,
                "result": result
            })

    on_event("final_response", {"complete": True})
```

**B. Update server.py to stream events:**
```python
def process_message_streaming(self, content: str, request_id: str):
    """Process message and emit streaming events"""
    if not self.session:
        self.initialize_session()

    def emit_event(event_type, data):
        event = {
            "id": request_id,
            "type": event_type,
            **data
        }
        print(json.dumps(event))
        sys.stdout.flush()

    # Stream the response
    self.session.send_message_streaming(content, on_event=emit_event)

    # Send final message
    emit_event("final_response", {"complete": True})
```

#### 3. Frontend Changes (TypeScript UI)

**A. Update PythonBridge (ui/src/bridge/python.ts):**
```typescript
async sendMessageStreaming(
  content: string,
  onEvent: (event: StreamEvent) => void
): Promise<void> {
  const id = `msg_${++this.messageId}`;

  return new Promise((resolve, reject) => {
    // Set up streaming handler
    this.streamHandlers.set(id, {
      onEvent,
      resolve,
      reject
    });

    // Send request
    this.pythonProcess.stdin.write(JSON.stringify({
      id,
      method: 'chat_streaming',
      params: {content}
    }) + '\n');
  });
}

private processBuffer() {
  const lines = this.buffer.split('\n');
  this.buffer = lines.pop() || '';

  for (const line of lines) {
    if (line.trim()) {
      const event = JSON.parse(line);

      // Check if it's a streaming event
      const handler = this.streamHandlers.get(event.id);
      if (handler && event.type !== 'final_response') {
        handler.onEvent(event);
      } else if (handler && event.type === 'final_response') {
        handler.resolve();
        this.streamHandlers.delete(event.id);
      }
    }
  }
}
```

**B. Update Chat.tsx to handle streaming:**
```typescript
const [currentStreamContent, setCurrentStreamContent] = useState<StreamingMessage | null>(null);

interface StreamingMessage {
  textChunks: string[];
  toolCalls: Array<{
    name: string;
    input: any;
    result?: string;
  }>;
}

const handleInput = async (text: string, annotation?: string) => {
  // Add user message
  setMessages(prev => [...prev, {role: 'user', content: text, annotation}]);
  setIsLoading(true);

  // Initialize streaming message
  const streamMsg: StreamingMessage = {
    textChunks: [],
    toolCalls: []
  };
  setCurrentStreamContent(streamMsg);

  try {
    await bridge.sendMessageStreaming(text, (event) => {
      if (event.type === 'text_chunk') {
        streamMsg.textChunks.push(event.content);
        setCurrentStreamContent({...streamMsg});
      } else if (event.type === 'tool_call_start') {
        streamMsg.toolCalls.push({
          name: event.tool_name,
          input: event.tool_input
        });
        setCurrentStreamContent({...streamMsg});
      } else if (event.type === 'tool_call_result') {
        const lastCall = streamMsg.toolCalls[streamMsg.toolCalls.length - 1];
        lastCall.result = event.result;
        setCurrentStreamContent({...streamMsg});
      }
    });

    // Finalize: convert streaming message to regular message
    const fullContent = formatStreamingMessage(streamMsg);
    setMessages(prev => [...prev, {
      role: 'assistant',
      content: fullContent
    }]);
    setCurrentStreamContent(null);

  } catch (error) {
    // Handle error
  } finally {
    setIsLoading(false);
  }
};
```

**C. Update MessageList.tsx to show streaming:**
```typescript
export const MessageList: React.FC<MessageListProps> = ({
  messages,
  isLoading,
  streamingContent
}) => {
  return (
    <Box flexDirection="column">
      {messages.map((message, index) => (
        <Message key={index} message={message} />
      ))}

      {/* Show streaming content */}
      {streamingContent && (
        <StreamingMessage content={streamingContent} />
      )}

      {isLoading && !streamingContent && (
        <Box>
          <Spinner type="dots" />
          <Text> Thinking...</Text>
        </Box>
      )}
    </Box>
  );
};

const StreamingMessage: React.FC<{content: StreamingMessage}> = ({content}) => {
  return (
    <Box flexDirection="column">
      {/* Show text chunks */}
      {content.textChunks.map((chunk, i) => (
        <Text key={`text-${i}`}>{chunk}</Text>
      ))}

      {/* Show tool calls */}
      {content.toolCalls.map((call, i) => (
        <Box key={`tool-${i}`} flexDirection="column" marginY={1}>
          <Text color="cyan">[Tool Use: {call.name}]</Text>
          <Text color="gray">{JSON.stringify(call.input, null, 2)}</Text>
          {call.result && (
            <>
              <Text color="cyan">[Tool Result]</Text>
              <Text color="gray">{call.result}</Text>
            </>
          )}
        </Box>
      ))}
    </Box>
  );
};
```

#### 4. Testing Plan
1. Test text-only responses (no tools)
2. Test single tool call (read_file)
3. Test multiple tool calls in sequence
4. Test tool call failures
5. Test escape key cancellation during streaming

#### 5. Implementation Complete ✅
**Files Modified:**
- `reccli/llm.py`: Added `send_message_streaming()` and `_call_anthropic_streaming()`
- `backend/server.py`: Added `process_message_streaming()` and `chat_streaming` method
- `ui/src/bridge/python.ts`: Added `sendMessageStreaming()` with StreamEvent handling
- `ui/src/components/Chat.tsx`: Updated to use streaming with StreamingMessage state
- `ui/src/components/MessageList.tsx`: Added StreamingMessageComponent with real-time display

**Benefits Achieved:**
- ✅ Matches Claude Code UX exactly
- ✅ User sees progress in real-time
- ✅ Can cancel mid-tool-call with Escape key
- ✅ Better transparency of what LLM is doing
- ✅ More professional feel
- ✅ Tool calls show `[Tool Use]` and `[Tool Result]` sections incrementally
- ✅ Spinner shows while tool is executing