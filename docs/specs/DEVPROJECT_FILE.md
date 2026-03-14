# .devproject File

**Status:** Design spec for an optional project-layer companion format.

The current RecCli codebase does not yet create or persist `.devproject` in the main CLI path, though middleware can opportunistically read one if present. Treat this document as a forward-looking contract for an optional project-outline layer, not as a description of required startup behavior.

## Overview

The `.devproject` file is the intended optional container for project-level context in the RecCli ecosystem. It would typically live at the root of a git repository and contain structured project memory that can be created manually, inferred from repo state, or incrementally refined from session history.

## Location

```
~/projects/RecCli/
├── .devproject              # ← Project overview (gitignored by default)
├── .gitignore
├── README.md
├── LICENSE
└── ...
```

**File path:** `<project-root>/.devproject`

**Git tracking:** Gitignored by default for privacy, but can be tracked if desired

## Purpose

The `.devproject` file serves multiple purposes:

1. **Automatic Documentation** - Self-writing project overview that updates each session
2. **Context Persistence** - Carries project context across sessions
3. **Project Outline Cache** - A compact project-level layer above individual sessions
4. **Team Sharing** - Optional git tracking for team-wide context (if not gitignored)
5. **Onboarding** - Instant project understanding for new contributors or after breaks

## File Format

`.devproject` files are JSON documents containing the project overview:

```json
{
  "format": "devproject",
  "version": "1.0.0",
  "last_updated": "session-003",
  "updated_at": "2024-10-27T18:30:00Z",

  "project": {
    "name": "RecCli",
    "description": "Temporal memory engine for coding agents",
    "purpose": "Enable developers and coding agents to preserve reasoning history across sessions",
    "value_proposition": "Keeps active context small while preserving exact recoverability from summary and full conversation history",
    "repository": "https://github.com/willluecke/RecCli",
    "license": "MIT",
    "status": "active_development"
  },

  "tech_stack": {
    "languages": ["Python", "TypeScript"],
    "frameworks": ["Ink"],
    "key_dependencies": ["anthropic", "openai", "sentence-transformers"],
    "embedding_model": "text-embedding-3-small",
    "llm_model": "claude-sonnet-4.5"
  },

  "architecture": {
    "overview": "Memory engine built around .devsession, retrieval, compaction, and an optional project-outline layer",
    "components": [
      {
        "name": "RecCli",
        "purpose": "Session ingestion, recording, retrieval, and compaction",
        "tech": "Python, native PTY/WAL recording, memory middleware"
      },
      {
        "name": ".devsession Format",
        "purpose": "Time-linked memory: optional project outline + session summary + full conversation with vectors",
        "tech": "JSON, vector embeddings, hybrid retrieval"
      }
    ],
    "key_patterns": [
      "Dual-layer UI (simple recorder + intelligent export)",
      "Preemptive compaction (190K threshold before Claude Code's 200K limit)",
      "Vector search for context retrieval (cosine similarity)",
      "Incremental embedding generation (build as you go)"
    ]
  },

  "key_decisions": [
    {
      "id": "decision_001",
      "date": "2024-10-27",
      "session": "session-001",
      "decision": "Make RecCli open source (MIT license)",
      "reasoning": "Build developer credibility, monetize other projects instead",
      "impact": "high",
      "alternatives_considered": ["Freemium model", "One-time purchase"],
      "current_status": "implemented"
    },
    {
      "id": "decision_002",
      "date": "2024-10-27",
      "session": "session-002",
      "decision": "Use linked memory layers with summary-to-source recovery",
      "reasoning": "Better than flat compaction or flat retrieval because it preserves exact drill-down paths",
      "impact": "high",
      "alternatives_considered": ["Simple markdown logs", "Use Claude Code's built-in compaction"],
      "current_status": "implemented"
    }
  ],

  "project_phases": {
    "current_phase": "Architecture & Documentation",
    "completed_phases": [
      {
        "phase": "Open Source Conversion",
        "completed": "2024-10-27",
        "sessions": ["session-001"],
        "summary": "Removed payment infrastructure, added MIT license, updated README"
      },
      {
        "phase": "Format Design",
        "completed": "2024-10-27",
        "sessions": ["session-002"],
        "summary": "Designed .devsession format with dual-layer architecture and vector embeddings"
      }
    ],
    "next_milestones": [
      {
        "milestone": "MVP - Export Dialog",
        "target": "Q4 2024",
        "description": "Add export dialog to RecCli with .devsession format support",
        "priority": "high"
      },
      {
        "milestone": "Vector Embeddings",
        "target": "Q1 2025",
        "description": "Generate embeddings during recording for semantic search",
        "priority": "medium"
      }
    ]
  },

  "sessions": [
    {
      "id": "session-001",
      "date": "2024-10-27",
      "duration_hours": 1.5,
      "focus": "Open source conversion",
      "key_outcomes": ["Removed payment code", "Added MIT license", "Updated documentation"]
    },
    {
      "id": "session-002",
      "date": "2024-10-27",
      "duration_hours": 2.0,
      "focus": ".devsession format design",
      "key_outcomes": ["Designed three-layer architecture", "Created format specification", "Built example files"]
    },
    {
      "id": "session-003",
      "date": "2024-10-27",
      "duration_hours": 1.5,
      "focus": "Context loading & compaction strategy",
      "key_outcomes": ["Defined implicit goal approach", "Designed preemptive compaction", "Updated documentation"]
    }
  ],

  "statistics": {
    "total_sessions": 3,
    "total_duration_hours": 5.0,
    "files_created": 12,
    "files_modified": 8,
    "lines_of_code": 2400,
    "documentation_pages": 6
  }
}
```

## Lifecycle

### Session Start

When starting a new recording session, RecCli should be able to operate even if no `.devproject` exists:

```python
def start_recording(project_dir):
    # 1. Try to load .devproject from repo root
    devproject_path = project_dir / '.devproject'

    if devproject_path.exists():
        # Load existing project overview
        project_overview = load_devproject(devproject_path)
    else:
        # Continue without project overview for now
        project_overview = None

    # 2. Create new session with whatever project overview is available
    session = create_session(project_overview)

    return session
```

### During Session

If present, the project overview is **carried along** in the session but not modified during recording. It remains optional enrichment rather than a prerequisite.

### Session End

When ending a session:

```python
def stop_recording(session, project_dir):
    # 1. Update project overview based on this session
    updated_overview = update_project_overview(
        current_overview=session.project_overview,
        session_summary=session.summary,
        session_metadata=session.metadata
    )

    # 2. Save updated overview back to .devproject
    devproject_path = project_dir / '.devproject'
    save_devproject(updated_overview, devproject_path)

    # 3. Save full session to .devsession file
    save_session(session, '~/sessions/session_004.devsession')
```

### Update Logic

The AI automatically extracts project-level information from each session:

```python
def update_project_overview(current_overview, session_summary, session_metadata):
    """
    Incrementally update project overview based on session
    """
    overview = current_overview.copy()

    # Add this session to history
    overview['sessions'].append({
        'id': session_metadata.session_id,
        'date': session_metadata.created_at,
        'duration_hours': session_metadata.duration / 3600,
        'focus': infer_session_focus(session_summary),
        'key_outcomes': extract_key_outcomes(session_summary)
    })

    # Extract project-level decisions (AI classifies high-impact decisions)
    for decision in session_summary.decisions:
        if decision.impact == 'high':
            overview['key_decisions'].append({
                'id': f"decision_{len(overview['key_decisions']) + 1:03d}",
                'date': session_metadata.created_at,
                'session': session_metadata.session_id,
                'decision': decision.decision,
                'reasoning': decision.reasoning,
                'impact': decision.impact,
                'current_status': 'implemented'
            })

    # Update tech stack if new technologies added
    new_tech = extract_new_technologies(session_summary)
    if new_tech:
        overview['tech_stack']['key_dependencies'].extend(new_tech)

    # Update statistics
    overview['statistics']['total_sessions'] += 1
    overview['statistics']['total_duration_hours'] += session_metadata.duration / 3600
    overview['statistics']['files_modified'] += count_files_modified(session_summary)

    # Update metadata
    overview['last_updated'] = session_metadata.session_id
    overview['updated_at'] = session_metadata.created_at

    return overview
```

## Automatic .gitignore Management

### Auto-Adding to .gitignore

When RecCli creates a `.devproject` file for the first time, it **automatically adds it to your project's `.gitignore`**:

```python
def auto_update_gitignore(project_dir):
    """
    Automatically add .devproject to project's .gitignore
    Called on first session in a project
    """
    gitignore_path = project_dir / '.gitignore'

    # Create .gitignore if it doesn't exist
    if not gitignore_path.exists():
        gitignore_path.touch()

    # Read current contents
    with open(gitignore_path, 'r') as f:
        contents = f.read()

    # Check if .devproject already in .gitignore
    if '.devproject' in contents:
        return  # Already there

    # Add .devproject to .gitignore
    addition = """
# DevProject file (AI-generated project overview)
# Remove this line to track .devproject for team-wide context sharing
.devproject
"""

    with open(gitignore_path, 'a') as f:
        f.write(addition)

    print("✓ Added .devproject to .gitignore (privacy by default)")
```

**What gets added:**
```gitignore
# DevProject file (AI-generated project overview)
# Remove this line to track .devproject for team-wide context sharing
.devproject
```

**User notification:**
```
┌─────────────────────────────────────────────────┐
│  First Session Setup                            │
├─────────────────────────────────────────────────┤
│  ✓ Created .devproject                          │
│  ✓ Added .devproject to .gitignore              │
│                                                 │
│  Your project overview will be saved to:       │
│  ~/projects/YourProject/.devproject            │
│                                                 │
│  Private by default (gitignored).              │
│  Remove from .gitignore to share with team.    │
│                                                 │
│  [ OK ]                                         │
└─────────────────────────────────────────────────┘
```

## Git Tracking

### Default: Gitignored (Private)

By default, `.devproject` is **automatically gitignored** for privacy:

**Why gitignore by default?**
- Projects may contain sensitive information (architecture decisions, business logic)
- Users can opt-in to tracking if they want team-wide context
- Personal projects stay private
- No accidental commits of project context

### Optional: Git Track for Team Sharing

If you want to share project context with your team:

```bash
# Remove .devproject from .gitignore
sed -i '/.devproject/d' .gitignore

# Track it
git add .devproject
git commit -m "Add project overview for team context"
git push
```

**Benefits of git tracking:**
- Team members get instant project context
- New contributors onboard faster
- Everyone sees architecture decisions and reasoning
- Project evolution is version controlled

**When to track:**
- Open source projects (public context)
- Team projects where everyone should have context
- Projects where architecture decisions need visibility

**When NOT to track:**
- Private projects with sensitive information
- Personal projects
- Projects with proprietary architecture

## Use Cases

### Use Case 1: Return After 3 Months

```bash
# You: Haven't touched RecCli in 3 months
# You: Start new session

RecCli reads .devproject:
- Project name: RecCli
- Current phase: Architecture & Documentation
- Next milestone: MVP - Export Dialog
- 3 previous sessions
- Key decisions: Open source, three-layer format, preemptive compaction

AI: "Welcome back to RecCli! You're in the Architecture & Documentation phase.
     Last session was 3 months ago working on context loading strategy.
     Next milestone is implementing the MVP export dialog.
     Want to pick up where you left off?"
```

### Use Case 2: New Team Member

```bash
# New developer: Clones repo (if .devproject is tracked)
# New developer: Starts session

RecCli reads .devproject:
- Full project overview
- Architecture components
- Key decisions with reasoning
- Current development phase
- Next milestones

AI: "RecCli is an open-source CLI recorder with .devsession format.
     Built with Python/tkinter. Three-layer architecture for context management.
     Currently in Architecture phase, next milestone is MVP export dialog.

     What would you like to work on?"
```

### Use Case 3: Mid-Session Context Switch

```bash
# You: Deep in debugging webhooks (190K tokens)
# RecCli: Triggers preemptive compaction

Compaction process:
1. Read .devproject (always have current project state)
2. Generate session summary (what happened today)
3. Compact to 2K tokens
4. Continue with full context

Result: After compaction, AI still knows:
- What RecCli is (from .devproject)
- What you did in this session (from summary)
- What you're working on right now (from recent messages)
```

## Schema

See `DEVSESSION_FORMAT.md` for the complete Project Overview Object schema.

## File Size

Typical `.devproject` file size:
- Small project (1-5 sessions): ~5 KB
- Medium project (10-50 sessions): ~15 KB
- Large project (100+ sessions): ~50 KB

The file grows slowly as sessions are added, but remains compact due to summarization.

## Best Practices

### 1. Let It Update Automatically

Don't manually edit `.devproject`. Let RecCli update it based on session summaries. The AI extracts project-level information automatically.

### 2. Review After Major Milestones

Occasionally review `.devproject` to ensure accuracy:
```bash
cat .devproject | jq .
```

### 3. Version Control Decision

Decide early: git track or gitignore?
- **Track:** Team projects, open source
- **Ignore:** Personal projects, sensitive work

### 4. Backup

`.devproject` is generated, but if you gitignore it, consider backing up:
```bash
# Backup to your session directory
cp .devproject ~/sessions/RecCli.devproject.backup
```

## Future Enhancements

### v1.1: Cross-Project Intelligence
```json
{
  "related_projects": [
    {"name": "MyAPI", "relationship": "RecCli uses MyAPI for embeddings"},
    {"name": "EstimatePro", "relationship": "Similar AI-powered approach"}
  ]
}
```

### v1.2: Team Collaboration
```json
{
  "contributors": [
    {"name": "Will", "sessions": 15, "focus_areas": ["architecture", "UI"]},
    {"name": "Jane", "sessions": 8, "focus_areas": ["testing", "docs"]}
  ]
}
```

### v1.3: Auto-README Generation
```bash
reccli generate-readme --from-devproject

# Creates README.md from .devproject automatically
```

---

**The `.devproject` file is the foundation of persistent project context in the .devsession ecosystem.**
