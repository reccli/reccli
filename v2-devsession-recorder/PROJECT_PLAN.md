# RecCli Project Plan

## Project Overview
RecCli is a terminal-based LLM chat interface with revolutionary dual-layer .devsession memory architecture, enabling persistent project memory and intelligent context management.

## Current State (Phase 7 Completed)
- ✅ Dual-layer .devsession format (summary layer + full history)
- ✅ Preemptive compaction at 150k tokens
- ✅ Claude 4.5/GPT-5 model support
- ✅ Basic terminal interface
- ⚠️ Paste detection issues with Python/prompt_toolkit

## Architecture Evolution Decision (Nov 8, 2025)

### Problem Identified
- prompt_toolkit cannot replicate Claude Code's paste behavior
- Claude Code uses TypeScript + Ink for complete terminal control
- Python's terminal libraries lack necessary rendering control

### Solution: Hybrid Architecture

```
┌─────────────────────────────────────┐
│    TypeScript + Ink UI Layer        │
│  (Terminal rendering, paste detect) │
├─────────────────────────────────────┤
│    Python Middleware Layer          │
│  (.devsession, compaction, memory)  │
├─────────────────────────────────────┤
│      LLM API Layer                  │
│   (Anthropic/OpenAI clients)        │
└─────────────────────────────────────┘
```

### Implementation Phases

#### Phase 8: TypeScript UI Foundation
- [ ] Set up TypeScript + Ink project structure
- [ ] Create basic chat interface component
- [ ] Implement paste detection that works
- [ ] Establish Python subprocess communication

#### Phase 9: Integration
- [ ] Port Python CLI commands to TypeScript
- [ ] Create JSON protocol for UI <-> Backend
- [ ] Handle streaming LLM responses
- [ ] Error handling across process boundary

#### Phase 10: Feature Parity
- [ ] Model switching (`/model` command)
- [ ] Session management
- [ ] Search functionality
- [ ] Terminal recording integration

## Technical Stack

### Frontend (New)
- **Language**: TypeScript
- **Framework**: React + Ink 4.0
- **Runtime**: Node.js 18+
- **Key Libraries**:
  - ink-text-input (input handling)
  - ink-select-input (menus)
  - ink-spinner (loading states)

### Backend (Existing)
- **Language**: Python 3.10+
- **Key Modules**:
  - devsession.py (dual-layer format)
  - preemptive_compaction.py (memory management)
  - config.py (settings)
  - llm.py (LLM communication)

### Communication Protocol
```typescript
// UI -> Backend
{
  "type": "chat",
  "message": "user input",
  "session_id": "session_123"
}

// Backend -> UI
{
  "type": "response",
  "content": "assistant message",
  "token_count": 45234,
  "session_updated": true
}
```

## Project Structure
```
reccli/
├── ui/                      # TypeScript frontend (NEW)
│   ├── src/
│   │   ├── index.tsx       # Entry point
│   │   ├── components/
│   │   │   ├── Chat.tsx    # Main chat interface
│   │   │   ├── Input.tsx   # Smart input with paste detection
│   │   │   └── Status.tsx  # Token count, session info
│   │   └── bridge/
│   │       └── python.ts   # Python process management
│   ├── package.json
│   └── tsconfig.json
├── backend/                 # Python backend (EXISTING)
│   ├── devsession.py
│   ├── preemptive_compaction.py
│   ├── config.py
│   └── server.py           # New: JSON-RPC server
├── reccli                  # Launcher script
└── docs/
    ├── PASTE_ARCHITECTURE.md
    └── PROJECT_PLAN.md

```

## Success Metrics
1. **Paste Detection**: Shows `[pasted +X lines]` during input (not after)
2. **Performance**: <10ms UI response time
3. **Compatibility**: Works on macOS, Linux, Windows terminals
4. **Feature Parity**: All Python CLI features available in new UI
5. **Developer Experience**: Single command installation and execution

## Risk Mitigation
- **Risk**: Complexity of maintaining two codebases
  - **Mitigation**: Clear API boundary, comprehensive tests
- **Risk**: Deployment complexity (Node + Python)
  - **Mitigation**: Bundle with pkg + PyInstaller, or Docker
- **Risk**: Performance overhead of IPC
  - **Mitigation**: Benchmark early, optimize protocol if needed

## Timeline
- **Week 1**: TypeScript UI skeleton, basic chat working
- **Week 2**: Paste detection, Python bridge
- **Week 3**: Full integration, streaming responses
- **Week 4**: Testing, packaging, documentation

## Long-term Vision
Once stable, consider:
- Porting Python middleware to TypeScript for single-language stack
- Web UI variant using same backend
- Plugin system for custom commands
- Multi-user/team features