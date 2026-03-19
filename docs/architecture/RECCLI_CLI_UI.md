# RecCli Terminal UI Architecture

**Status:** Current-state architecture note.

The TypeScript terminal UI is a real part of the current chat path. It is launched from the Python CLI and talks to a packaged Python backend over JSON-RPC on stdio.

## Scope

This document describes the current terminal chat UI architecture:

- Python CLI entry into chat mode
- TypeScript + Ink terminal frontend
- Python JSON-RPC backend bridge
- streaming response and tool-call display
- known implementation gaps

This is not the architecture for the whole RecCli system. For memory, retrieval, and compaction, see:

- [ARCHITECTURE.md](./ARCHITECTURE.md)
- [CONTEXT_LOADING.md](./CONTEXT_LOADING.md)

## Current Runtime Path

The main chat flow today is:

1. User runs `reccli chat`
2. [cli.py](/Users/will/coding-projects/RecCli/packages/reccli-core/reccli/cli.py#L185) calls [chat_ui.py](/Users/will/coding-projects/RecCli/packages/reccli-core/reccli/chat_ui.py#L31)
3. [chat_ui.py](/Users/will/coding-projects/RecCli/packages/reccli-core/reccli/chat_ui.py#L31) ensures the TypeScript UI is built and launches `node dist/index.js`
4. The UI initializes [python.ts](/Users/will/coding-projects/RecCli/packages/reccli-core/ui/src/bridge/python.ts#L35)
5. The bridge spawns a Python backend process and exchanges JSON-RPC messages over stdio
6. The UI renders messages, streaming content, and input state inside Ink components

## Architecture

```text
user
  -> reccli chat
  -> Python CLI launcher
  -> TypeScript + Ink UI
  -> Python bridge process
  -> RecCli LLM/session logic
  -> .devsession output
```

### UI Layer

The UI lives under [packages/reccli-core/ui/src](/Users/will/coding-projects/RecCli/packages/reccli-core/ui/src).

Key files:

- [index.tsx](/Users/will/coding-projects/RecCli/packages/reccli-core/ui/src/index.tsx): UI entry point
- [components/Chat.tsx](/Users/will/coding-projects/RecCli/packages/reccli-core/ui/src/components/Chat.tsx): top-level chat state and streaming orchestration
- [components/InputV3.tsx](/Users/will/coding-projects/RecCli/packages/reccli-core/ui/src/components/InputV3.tsx): raw-mode input, paste annotation, history, cancellation
- [components/MessageList.tsx](/Users/will/coding-projects/RecCli/packages/reccli-core/ui/src/components/MessageList.tsx): message rendering and streaming tool-call display
- [components/Status.tsx](/Users/will/coding-projects/RecCli/packages/reccli-core/ui/src/components/Status.tsx): session/status bar

### Backend Bridge

The UI bridge is implemented in [python.ts](/Users/will/coding-projects/RecCli/packages/reccli-core/ui/src/bridge/python.ts#L35).

It is responsible for:

- spawning the Python backend process
- sending line-delimited JSON-RPC requests
- reading line-delimited JSON responses/events
- routing standard responses vs streaming events
- exposing a small frontend API:
  - `initialize()`
  - `sendMessage()`
  - `sendMessageStreaming()`
  - `getSessionInfo()`
  - `close()`

### Python Backend

The bridge currently expects a backend at:

- `packages/reccli-core/backend/server.py`

The concrete server implementation now lives at:

- [packages/reccli-core/backend/server.py](/Users/will/coding-projects/RecCli/packages/reccli-core/backend/server.py)

That server exposes the methods the UI expects:

- `ping`
- `chat`
- `chat_streaming`
- `getSessionInfo`

An older example copy remains under `examples/` for local experimentation, but the packaged runtime path above is the canonical one the UI bridge targets.

## Message Flow

### Non-streaming

For simple requests, the bridge writes a JSON object to stdin and waits for a matching response ID.

```json
{
  "id": "msg_1",
  "method": "chat",
  "params": {"content": "hello"}
}
```

### Streaming

The main chat path uses `chat_streaming`.

Streaming events include:

- `text_chunk`
- `tool_call_start`
- `tool_call_result`
- `final_response`
- `error`

[Chat.tsx](/Users/will/coding-projects/RecCli/packages/reccli-core/ui/src/components/Chat.tsx#L55) accumulates these into a temporary streaming message, then converts the finished stream into a normal assistant message in the message list.

## Input Model

The current input implementation is [InputV3.tsx](/Users/will/coding-projects/RecCli/packages/reccli-core/ui/src/components/InputV3.tsx#L1).

Implemented behaviors:

- raw terminal input handling via Ink
- command history with up/down navigation
- left/right cursor movement
- request cancellation on `Esc`
- paste chunk accumulation
- line-ending normalization from `\r` to `\n`
- hidden paste buffer plus visible annotation
- validation that discards hidden paste content if the annotation is edited

This is the current answer to the earlier paste-handling limitation in the pure Python prompt path.

## Rendering Model

[MessageList.tsx](/Users/will/coding-projects/RecCli/packages/reccli-core/ui/src/components/MessageList.tsx#L1) renders:

- persisted chat messages
- paste annotations on user messages
- in-flight streaming text
- in-flight tool calls and results
- a spinner when waiting without active streaming content

Large message content is chunked before rendering to avoid issues with oversized terminal text blocks.

## Session/Backend Coupling

The current UI is a thin frontend over Python session logic.

Responsibilities that remain in Python:

- LLM calls
- session persistence
- `.devsession` writing
- model/session initialization
- token-count reporting

Responsibilities handled in TypeScript:

- terminal interaction
- input behavior
- streaming presentation
- transient UI state

This separation is the right direction for the project: UI concerns stay in Ink, while memory and session logic remain in the existing Python core.

## Known Gaps

### Architecture vs. Packaging

The architecture is real, but the packaging surface is still transitional:

- Python launches the Node UI
- the Node UI launches a Python backend
- the documented local-dev flow still assumes both Python and Node dependencies are installed manually

### Session Info Is Lightweight

The current `getSessionInfo()` contract is minimal and the example backend reports rough token counts rather than fully hydrated middleware state.

## Practical Canon

When reasoning about the terminal UI today, use these as the source of truth:

- [cli.py](/Users/will/coding-projects/RecCli/packages/reccli-core/reccli/cli.py)
- [chat_ui.py](/Users/will/coding-projects/RecCli/packages/reccli-core/reccli/chat_ui.py)
- [python.ts](/Users/will/coding-projects/RecCli/packages/reccli-core/ui/src/bridge/python.ts)
- [Chat.tsx](/Users/will/coding-projects/RecCli/packages/reccli-core/ui/src/components/Chat.tsx)
- [InputV3.tsx](/Users/will/coding-projects/RecCli/packages/reccli-core/ui/src/components/InputV3.tsx)
- [MessageList.tsx](/Users/will/coding-projects/RecCli/packages/reccli-core/ui/src/components/MessageList.tsx)
- [packages/reccli-core/backend/server.py](/Users/will/coding-projects/RecCli/packages/reccli-core/backend/server.py)

## Summary

RecCli’s current chat UI is a hybrid system:

- Python owns session and memory logic
- TypeScript + Ink owns terminal interaction
- JSON-RPC over stdio connects them

That architecture is valid and implemented. The main remaining issue is no longer backend path mismatch; it is packaging and install polish around the Python + Node runtime.
