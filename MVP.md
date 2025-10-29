# RecCli MVP & Roadmap

**Project:** RecCli - CLI terminal recorder with AI-powered session management
**Status:** Architecture & Design Complete → Ready for Implementation
**Last Updated:** October 29, 2024

---

## Product Vision

Enable developers using AI coding assistants to preserve and intelligently continue coding sessions, eliminating context loss and reducing time spent re-explaining project context.

**Core Value Proposition:** Automatic context preservation with smart vector search that makes AI coding assistants genuinely strategic partners, not just tactical code generators.

---

## Two-Phase Release Strategy

### Why Two Phases?

**Phase 1** ships a valuable standalone tool quickly (4-6 weeks):
- Terminal recording with simple export
- Immediate value: Share session logs
- Validates user demand
- No complex AI integration needed

**Phase 2** adds the intelligent context layer (8-12 weeks):
- .devsession format with vector embeddings
- .devproject with smart onboarding
- Context continuation with vector search
- Full strategic AI capability

This approach:
- ✅ Gets to market faster with Phase 1
- ✅ Validates demand before heavy investment
- ✅ Allows user feedback to inform Phase 2
- ✅ Generates early adopters and community
- ✅ Can pivot if Phase 1 shows different needs

---

## Phase 1: Recording & Export MVP

**Goal:** Ship a simple, useful terminal recorder with multiple export formats

**Timeline:** 4-6 weeks
**Effort:** ~160-240 hours
**Release Target:** Q4 2024

### Core Features

#### 1. Basic Recording

**UI Design:**
- Floating overlay button attached to top-right corner of terminal window
- Follows terminal window when moved (updates position every 500ms)
- Circular green button with red dot (ready to record)
- Transforms to red square with white stop icon (while recording)
- Timer displayed below button during recording
- Always-on-top, semi-transparent overlay

**Functionality:**
- Click [● REC] → Start recording terminal session in new window
- Click [■ STOP] → Stop recording, show export dialog
- Uses asciinema for terminal capture
- Display recording duration in real-time
- Right-click menu for settings, stats, and recordings folder
- Draggable if needed (manual repositioning)
- No project dropdown (Phase 2)
- No context management (Phase 2)

**Technical:**
- Python + tkinter UI (floating overlay)
- AppleScript integration for terminal window position tracking
- asciinema subprocess for recording
- Simple session storage in memory
- Auto-generate session ID
- Supports both Terminal.app and iTerm2

**Success Criteria:**
- Button attaches to terminal window top-right corner
- Button follows terminal when window is moved
- Record terminal session without crashes
- Accurate capture of terminal output
- Recording duration displayed in real-time
- Clean start/stop UX

#### 2. Export Dialog

```
┌──────────────────────────────────────────────┐
│  Export Session                         [x]  │
├──────────────────────────────────────────────┤
│                                              │
│  Session: session-20241029-143045            │
│  Duration: 1h 23m                            │
│                                              │
│  Export Format:                              │
│    ( ) Plain Text (.txt)                     │
│    (•) Markdown (.md)                        │
│    ( ) JSON (.json)                          │
│    ( ) HTML (.html)                          │
│    ( ) Asciinema Cast (.cast)                │
│                                              │
│  Save Location:                              │
│  [~/Documents/sessions/] [Browse...]         │
│                                              │
│  Filename:                                   │
│  [session-20241029-143045    ] [.md]         │
│                                              │
│  [ Cancel ]              [ Export ]          │
│                                              │
└──────────────────────────────────────────────┘
```

**Export Formats:**

**Plain Text (.txt):**
```
Session: session-20241029-143045
Duration: 1h 23m
Date: 2024-10-29 14:30:45

[Full terminal output as plain text]
```

**Markdown (.md):**
```markdown
# Session: session-20241029-143045

**Duration:** 1h 23m
**Date:** 2024-10-29 14:30:45

## Terminal Output

```
[Terminal output in code block]
```
```

**JSON (.json):**
```json
{
  "session_id": "session-20241029-143045",
  "duration_seconds": 4980,
  "created_at": "2024-10-29T14:30:45Z",
  "terminal_output": "[full output]",
  "metadata": {
    "shell": "bash",
    "terminal": "xterm-256color"
  }
}
```

**HTML (.html):**
```html
<!DOCTYPE html>
<html>
<head><title>Session session-20241029-143045</title></head>
<body>
  <h1>Session: session-20241029-143045</h1>
  <div class="metadata">Duration: 1h 23m</div>
  <pre class="terminal-output">[styled terminal output]</pre>
</body>
</html>
```

**Asciinema Cast (.cast):**
```
Native asciinema format - can replay with asciinema play
```

**Success Criteria:**
- All 5 formats export correctly
- Files saved to chosen location
- Filename editable before export
- Exports complete in < 2 seconds
- File opens correctly in respective viewers

#### 3. Settings

```
┌──────────────────────────────────────────────┐
│  Settings                               [x]  │
├──────────────────────────────────────────────┤
│                                              │
│  Default Export Format:                      │
│    [Markdown (.md)        ▼]                 │
│                                              │
│  Default Save Location:                      │
│    [~/Documents/sessions/] [Browse...]       │
│                                              │
│  Recording:                                  │
│    [✓] Show recording indicator              │
│    [✓] Show duration timer                   │
│    [ ] Auto-pause on idle (5 min)            │
│                                              │
│  [ Cancel ]              [ Save ]            │
│                                              │
└──────────────────────────────────────────────┘
```

**Success Criteria:**
- Settings persist between sessions
- Default format applied to export dialog
- Default location used for exports
- Settings validate (e.g., directory exists)

#### 4. Installation

**Linux/macOS:**
```bash
curl -fsSL https://raw.githubusercontent.com/willluecke/RecCli/main/install.sh | bash
```

**Manual:**
```bash
git clone https://github.com/willluecke/RecCli
cd RecCli
pip install -r requirements.txt
chmod +x reccli.py
./reccli.py
```

**Success Criteria:**
- One-line install works on major platforms
- Requirements installed automatically
- Desktop/launcher shortcut created
- Easy to uninstall

### Phase 1 Scope: What's NOT Included

**Explicitly OUT of scope for Phase 1:**
- ❌ .devsession format (Phase 2)
- ❌ .devproject format (Phase 2)
- ❌ AI summarization (Phase 2)
- ❌ Vector embeddings (Phase 2)
- ❌ Context loading/compaction (Phase 2)
- ❌ Project management (Phase 2)
- ❌ Smart onboarding (Phase 2)
- ❌ API key management (Phase 2)
- ❌ Session continuation (Phase 2)

**Keep it simple:** Just recording and exporting to common formats.

### Phase 1 Technical Architecture

```
┌─────────────────────────────────────────────┐
│               RecCli UI (tkinter)           │
│  ┌────────┐  ┌────────┐  ┌────────────┐    │
│  │  REC   │  │  STOP  │  │  Settings  │    │
│  └────────┘  └────────┘  └────────────┘    │
└──────────────┬──────────────────────────────┘
               │
               v
    ┌─────────────────────┐
    │ asciinema (subprocess) │
    │  Terminal recording    │
    └──────────┬─────────────┘
               │
               v
    ┌──────────────────────┐
    │   Session Manager     │
    │  - Start/stop         │
    │  - Duration tracking  │
    └──────────┬────────────┘
               │
               v
    ┌──────────────────────┐
    │   Export Engine       │
    │  - .txt formatter     │
    │  - .md formatter      │
    │  - .json formatter    │
    │  - .html formatter    │
    │  - .cast passthrough  │
    └──────────┬────────────┘
               │
               v
         File System
```

**Dependencies:**
- Python 3.8+
- tkinter (GUI)
- asciinema (recording)
- Standard library only (no AI deps)

**File Structure:**
```
RecCli/
├── reccli.py              # Main entry point
├── src/
│   ├── ui/
│   │   ├── main_window.py
│   │   ├── export_dialog.py
│   │   └── settings_dialog.py
│   ├── recording/
│   │   ├── session.py
│   │   └── asciinema_wrapper.py
│   └── export/
│       ├── txt_exporter.py
│       ├── md_exporter.py
│       ├── json_exporter.py
│       ├── html_exporter.py
│       └── cast_exporter.py
├── install.sh
├── requirements.txt       # asciinema only
├── README.md
└── LICENSE
```

### Phase 1 Success Metrics

**Technical:**
- Recording success rate > 99%
- Export time < 2 seconds for sessions up to 2 hours
- Zero crashes during recording
- Works on Ubuntu, macOS, Debian

**Product:**
- 100+ GitHub stars in first month
- 50+ organic installs
- 10+ positive feedback comments
- No major bugs reported

**Validation:**
- Users actually use it (track exports)
- Users want more features (feature requests)
- Interest in .devsession format (gauge from feedback)

### Phase 1 Go-to-Market

**Launch Strategy:**
1. Post to Hacker News: "RecCli: Terminal recorder with multi-format export"
2. Post to r/programming, r/commandline
3. Tweet thread showing simple demo
4. Add to awesome-cli-apps lists

**Messaging:**
- "Record your terminal sessions and export to any format"
- "Dead simple terminal recorder for developers"
- "Share your dev sessions as markdown, HTML, or JSON"

**Demo GIF:**
```
1. Click [REC]
2. Type some commands
3. Click [STOP]
4. Select format
5. Export
6. Share
```

---

## Phase 2: Intelligent Context System

**Goal:** Add AI-powered context management with .devsession and .devproject

**Timeline:** 8-12 weeks after Phase 1
**Effort:** ~320-480 hours
**Release Target:** Q1 2025

### Phase 2 Overview

Phase 2 transforms RecCli from a simple recorder into an intelligent AI context management system. This is where the real innovation happens.

### Core Features (Phase 2)

#### 1. .devsession Format

**Full specification:** See [DEVSESSION_FORMAT.md](DEVSESSION_FORMAT.md)

**Capabilities:**
- Three-layer architecture (project overview + summary + full conversation)
- Vector embeddings for semantic search
- Chronological indexing for timeline navigation
- Session summaries with AI extraction
- Message-level metadata

**Implementation:**
- AI summarization using Claude Sonnet 4.5
- Vector embeddings using sentence-transformers
- JSON storage format
- Incremental embedding generation

#### 2. .devproject File

**Full specification:** See [DEVPROJECT_FILE.md](DEVPROJECT_FILE.md)

**Capabilities:**
- Project-level context (architecture, tech stack, decisions)
- Automatic updates from sessions
- Self-writing documentation
- Version control friendly
- Gitignored by default (privacy first)

**Implementation:**
- JSON format
- AI classification of project-level vs session-level changes
- Verification UI for user review
- Manual steering with verbal instructions

#### 3. Smart Project Initialization

**Full specification:** See [PROJECT_INITIALIZATION.md](PROJECT_INITIALIZATION.md) and [PROJECT_ONBOARDING.md](PROJECT_ONBOARDING.md)

**Capabilities:**
- Empty project → Conversational onboarding
- Existing project → Smart scan or interview
- Three complexity levels (Quick/Standard/Comprehensive)
- AI-guided scoping interview
- Pre-filling from codebase analysis

**Implementation:**
- Project detection (git, package files)
- Codebase scanning (README, dependencies, structure)
- Conversational AI interview
- .devproject generation

#### 4. Context Management

**Full specification:** See [CONTEXT_LOADING.md](CONTEXT_LOADING.md)

**Capabilities:**
- Preemptive compaction at 190K tokens
- Vector search for relevant past context
- Conditional project overview loading
- Chronological range queries
- Hybrid semantic + timeline search

**Implementation:**
- Token counting with tiktoken
- Embedding generation with sentence-transformers
- Cosine similarity search
- Smart context assembly (summary + recent + relevant)

#### 5. Session Continuation

**Capabilities:**
- Load previous session's context
- Resume with full project overview
- Automatic context loading
- Session history and timeline

**Implementation:**
- Session cache in .devsessions/ folder
- Chronological session listing
- Context compaction and loading
- Seamless continuation dialog

#### 6. Authentication & API Keys

**Full specification:** See [SETTINGS_AND_AUTH.md](SETTINGS_AND_AUTH.md)

**Capabilities:**
- Secure API key storage (system keychain)
- Setup wizard for first-time users
- API key testing
- Cost transparency
- Graceful degradation without keys

**Implementation:**
- keyring library for secure storage
- Setup wizard with 3 scenarios
- API validation
- Error handling

#### 7. Project Management UI

**Capabilities:**
- Project dropdown with recent projects
- Auto-detect current directory
- Project cache (~/.reccli/projects.json)
- Favorite projects
- Project switching

**Implementation:**
- Projects cache with metadata
- Git integration for detection
- Recent project tracking
- Auto-update on use

### Phase 2 Technical Architecture

```
┌────────────────────────────────────────────────────────┐
│                    RecCli UI                           │
│  [Project ▼]  [● REC]  [⚙️]                           │
└───────┬────────────────────────────────────────────────┘
        │
        v
┌───────────────────────┐
│  Project Manager      │
│  - Detection          │
│  - Initialization     │
│  - Context loading    │
└──────┬────────────────┘
       │
       ├─→ Empty Project ──→ [Conversational Onboarding]
       │                        │
       │                        v
       │                  [AI Interview]
       │                        │
       │                        v
       │                  [.devproject created]
       │
       └─→ Existing Code ──→ [Give User Choice]
                               │
                ┌──────────────┼──────────────┐
                │              │              │
                v              v              v
          [Smart Scan]  [Interview]  [Minimal]
                │              │              │
                └──────────────┴──────────────┘
                               │
                               v
                      [.devproject created]
                               │
                               v
┌──────────────────────────────────────────────────────┐
│                Recording Session                     │
│  (asciinema + metadata collection)                   │
└──────────────────┬───────────────────────────────────┘
                   │
                   v
┌──────────────────────────────────────────────────────┐
│              Token Monitoring                        │
│  Watch for 190K token threshold                      │
└──────────────────┬───────────────────────────────────┘
                   │
                   v (if > 190K)
┌──────────────────────────────────────────────────────┐
│          Preemptive Compaction                       │
│  1. Generate embeddings (sentence-transformers)      │
│  2. Generate summary (Claude API)                    │
│  3. Vector search for relevant context               │
│  4. Load compacted context                           │
└──────────────────┬───────────────────────────────────┘
                   │
                   v
┌──────────────────────────────────────────────────────┐
│              Export (Session End)                    │
│  1. Generate full session summary (Claude API)       │
│  2. Complete embeddings                              │
│  3. Classify project-level changes                   │
│  4. Show .devproject update verification             │
│  5. Save .devsession file                            │
│  6. Update .devproject (if approved)                 │
└──────────────────┬───────────────────────────────────┘
                   │
                   v
┌──────────────────────────────────────────────────────┐
│          .devsession & .devproject Files             │
│  - .devsessions/session-{timestamp}-{id}.devsession  │
│  - .devproject (at project root)                     │
└──────────────────────────────────────────────────────┘
```

**Additional Dependencies (Phase 2):**
- anthropic (Claude API)
- sentence-transformers (embeddings)
- tiktoken (token counting)
- keyring (secure storage)
- numpy (vector operations)

### Phase 2 Success Metrics

**Technical:**
- Compaction time < 10 seconds
- Context loading < 3 seconds
- Vector search accuracy > 85%
- Session continuation success rate > 95%
- Zero data loss

**Product:**
- 500+ GitHub stars
- 200+ active users
- 50+ .devsession files created
- Positive feedback on context quality
- Feature requests for enhancements

**Business:**
- Recognition from Anthropic
- Mentioned in AI coding tool reviews
- Integration requests from other tools
- Potential acquisition interest

### Phase 2 Rollout Plan

**Beta Release (Week 1-2):**
- Invite 20-30 early Phase 1 users
- Gather feedback on .devsession format
- Iterate on onboarding UX
- Fix critical bugs

**Public Release (Week 3):**
- Launch announcement
- Demo videos showing full workflow
- Documentation site
- Tutorial blog posts

**Post-Launch (Week 4+):**
- Monitor usage patterns
- Gather feature requests
- Plan Phase 3 features
- Community building

---

## Development Roadmap

### Phase 1 Timeline (4-6 weeks)

**Week 1-2: Core Recording**
- [ ] Set up project structure
- [ ] Implement basic UI (REC/STOP buttons)
- [ ] Integrate asciinema
- [ ] Session management
- [ ] Duration tracking

**Week 3-4: Export System**
- [ ] Export dialog UI
- [ ] Implement 5 export formats
- [ ] File save functionality
- [ ] Format validation
- [ ] Error handling

**Week 5: Settings & Polish**
- [ ] Settings dialog
- [ ] Settings persistence
- [ ] Default preferences
- [ ] UI polish
- [ ] Bug fixes

**Week 6: Launch Prep**
- [ ] Installation script
- [ ] README with examples
- [ ] Demo GIF/video
- [ ] Test on all platforms
- [ ] Launch!

### Phase 2 Timeline (8-12 weeks)

**Week 1-2: Foundation**
- [ ] API key management
- [ ] Project detection
- [ ] .devproject schema
- [ ] Basic project initialization

**Week 3-4: Smart Onboarding**
- [ ] Conversational interview AI
- [ ] Codebase scanning
- [ ] Pre-filling logic
- [ ] .devproject generation

**Week 5-6: Session Format**
- [ ] .devsession schema
- [ ] Message collection
- [ ] Metadata tracking
- [ ] JSON serialization

**Week 7-8: AI Integration**
- [ ] Session summarization
- [ ] Embedding generation
- [ ] Vector search
- [ ] Classification

**Week 9-10: Context Management**
- [ ] Token monitoring
- [ ] Preemptive compaction
- [ ] Context loading
- [ ] Session continuation

**Week 11: Verification UI**
- [ ] .devproject update dialog
- [ ] Diff view
- [ ] Accept/reject interface
- [ ] Manual steering

**Week 12: Polish & Launch**
- [ ] Beta testing
- [ ] Bug fixes
- [ ] Documentation
- [ ] Launch!

---

## Technical Requirements

### Phase 1
- **Language:** Python 3.8+
- **UI:** tkinter
- **Recording:** asciinema
- **Storage:** Local filesystem
- **Platforms:** Linux, macOS

### Phase 2 (Additional)
- **AI:** Anthropic Claude API
- **Embeddings:** sentence-transformers
- **Vector Ops:** numpy, scipy
- **Security:** keyring
- **Token Count:** tiktoken

---

## Cost Analysis

### Phase 1: Free
- No API calls
- No cloud services
- Open source (MIT)

### Phase 2: API Costs

**Per Session (estimated):**
- Session summary: ~$0.40
- Compaction (if triggered): ~$1.50
- Project initialization: ~$0.05
- Classification: ~$0.03

**Typical User (5 sessions/week):**
- Average: ~$2-3 per week
- Maximum: ~$10 per week (if every session needs compaction)

**User pays their own API costs** - we don't run hosted service.

---

## Go-to-Market Strategy

### Phase 1: Simple Recorder
**Positioning:** "Dead simple terminal recorder for developers"
**Channels:** Hacker News, Reddit, dev communities
**Message:** Share your dev sessions in any format

### Phase 2: AI Context System
**Positioning:** "The missing context layer for AI coding assistants"
**Channels:** AI/ML communities, Claude/OpenAI communities, tech press
**Message:** Never lose context again - AI that remembers your project

**Key Differentiators:**
- Only tool with project-aware context
- Open format (.devsession standard)
- Privacy-first (local by default)
- Works with any AI coding tool
- Automatic living documentation

---

## Risk Mitigation

### Phase 1 Risks

**Risk:** Nobody wants a simple recorder
**Mitigation:** Validate demand with Phase 1 launch, quick to pivot

**Risk:** Competition from existing tools
**Mitigation:** Focus on export formats, simplicity as differentiator

**Risk:** Platform compatibility issues
**Mitigation:** Test on major platforms, asciinema is proven

### Phase 2 Risks

**Risk:** AI summarization quality issues
**Mitigation:** Use Claude Sonnet 4.5 (best quality), include verification UI

**Risk:** Cost barrier for users
**Mitigation:** Transparent cost messaging, local models as fallback (future)

**Risk:** Complex UX confuses users
**Mitigation:** Progressive disclosure, defaults work for most cases

**Risk:** Anthropic doesn't support OAuth
**Mitigation:** Ship with API keys (MVP), request OAuth for v2.0

**Risk:** .devsession format not adopted
**Mitigation:** Make format open and well-documented, build reference impl

---

## Success Criteria

### Phase 1 Success = "Build Phase 2"
- 50+ organic installs
- Positive user feedback
- Feature requests for smarter features
- Low bug rate

### Phase 2 Success = "Market Leader"
- 500+ GitHub stars
- 200+ active users
- Recognition from Anthropic
- Integration requests
- Community contributions
- Press coverage

---

## Post-Phase 2: Future Vision

**Phase 3 Possibilities:**
- OAuth flow (if Anthropic supports)
- Multi-tool support (Cursor, Copilot, etc.)
- Team collaboration features
- Cloud sync (optional)
- VS Code extension
- Analytics dashboard
- Local model support (Ollama)
- .devsession becoming industry standard

**Long-term Vision:**
RecCli becomes the standard way developers maintain context across AI coding sessions, adopted by major tools, potentially acquired by Anthropic to integrate natively into Claude Code.

---

## Decision: Ready to Build?

**Phase 1 is well-defined and shippable.** All features scoped, dependencies minimal, timeline realistic.

**Phase 2 architecture is complete.** All specs written, flows designed, ready to implement after Phase 1 validates demand.

**Recommendation:** Start Phase 1 immediately. 4-6 weeks to a useful product that validates the market.

---

**Questions? Concerns? Ready to start coding?**
