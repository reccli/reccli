# Session Storage Strategy

## Overview

Where should .devsession files be stored? How should they be named? When should .devproject be updated? This document defines the complete storage strategy for session continuity.

## Storage Location: Three Options

### Option A: Project Root Folder `.devsessions/` (Recommended Default)

```
~/projects/RecCli/
├── .devproject                    # Project overview
├── .devsessions/                  # Session history (gitignored folder)
│   ├── session-001.devsession
│   ├── session-002.devsession
│   ├── session-003.devsession
│   └── session-004.devsession
├── .gitignore                     # Auto-updated to exclude .devsessions/
├── src/
└── README.md
```

**Pros:**
✅ Sessions travel with project (portable)
✅ Easy to find (right in project)
✅ Natural organization (one folder per project)
✅ Can selectively commit if desired (remove from .gitignore)
✅ Great for continuation (sessions are right there)

**Cons:**
❌ In project directory (some prefer external)
❌ Can grow large (mitigated by gitignore)

**When to use:**
- Default for most users
- Projects where session history is valuable
- When portability matters
- When you might want to commit sessions (open source)

### Option B: Home Directory `~/.reccli/sessions/{project-name}/`

```
~/.reccli/
├── config.json
├── projects.json
└── sessions/
    ├── RecCli/
    │   ├── session-001.devsession
    │   ├── session-002.devsession
    │   └── session-003.devsession
    ├── EstimatePro/
    │   ├── session-001.devsession
    │   └── session-002.devsession
    └── MyOtherProject/
        └── session-001.devsession
```

**Pros:**
✅ Centralized (all sessions in one place)
✅ Clean project directories
✅ Can't accidentally commit sessions
✅ Survives project deletion

**Cons:**
❌ Not portable (doesn't travel with repo)
❌ Harder to share sessions
❌ Need to lookup project path for continuation

**When to use:**
- User prefers clean project directories
- Private/sensitive work
- Multiple machines (don't want sessions in repo)

### Option C: User-Specified Location (Advanced)

```
~/Documents/RecCli-Sessions/
├── session-001.devsession
├── session-002.devsession
└── session-003.devsession
```

**User configurable in settings.**

**When to use:**
- Custom workflow needs
- Network drives / cloud sync
- Specific backup strategy

## Recommended Default: Option A (`.devsessions/` in project root)

**Why:**
- Natural organization (sessions with project)
- Portability (clone repo, optionally get sessions)
- Easy continuation (sessions are right there)
- Gitignored by default (privacy)
- Can opt-in to tracking (for open source context sharing)

## Naming Convention: Standardized Format

### Format: `session-{timestamp}-{short-id}.devsession`

**Examples:**
```
session-20241027-143045-a3f2.devsession
session-20241027-160230-b8c1.devsession
session-20241028-090015-c7d4.devsession
```

**Components:**
- `session-` : Prefix (consistent, easy to glob)
- `20241027` : Date (YYYYMMDD)
- `143045` : Time (HHMMSS)
- `a3f2` : Short ID (4-char hex, prevents collisions)
- `.devsession` : Extension

**Benefits:**
✅ Chronological sorting (natural order)
✅ Collision-resistant (timestamp + random ID)
✅ Easy to glob (`*.devsession`)
✅ Human-readable (can see date/time at glance)
✅ Consistent across projects

### Alternative Format: Sequential Numbering

**Format: `session-{number}.devsession`**

```
session-001.devsession
session-002.devsession
session-003.devsession
```

**Simpler but:**
❌ Requires counter tracking
❌ Collisions if sessions from multiple machines
❌ Less information in filename

**Verdict:** Use timestamp format as default, sequential as option.

## .gitignore Management

### Auto-Update User's .gitignore

When creating first session in a project:

```python
def ensure_session_storage(project_dir):
    """
    Set up session storage and update .gitignore
    """
    # 1. Create .devsessions folder
    sessions_dir = project_dir / '.devsessions'
    sessions_dir.mkdir(exist_ok=True)

    # 2. Update .gitignore
    gitignore_path = project_dir / '.gitignore'

    if not gitignore_path.exists():
        gitignore_path.touch()

    with open(gitignore_path, 'r') as f:
        contents = f.read()

    # Check if already added
    if '.devsessions/' not in contents and '.devsession' not in contents:
        addition = """
# RecCli session files (may contain sensitive conversations)
# Remove these lines to track sessions in git (for open source context sharing)
.devsessions/
*.devsession
"""
        with open(gitignore_path, 'a') as f:
            f.write(addition)

        print("✓ Added .devsessions/ to .gitignore (privacy by default)")
```

### What Gets Added to User's .gitignore:

```gitignore
# RecCli session files (may contain sensitive conversations)
# Remove these lines to track sessions in git (for open source context sharing)
.devsessions/
*.devsession
```

**User can opt-in to tracking by deleting these lines.**

## Session Continuation: Loading Previous Sessions

### Discovery: Find Existing Sessions

```python
def get_project_sessions(project_dir):
    """
    Get all sessions for a project, sorted by date
    """
    sessions_dir = project_dir / '.devsessions'

    if not sessions_dir.exists():
        return []

    # Find all .devsession files
    session_files = sorted(sessions_dir.glob('session-*.devsession'))

    sessions = []
    for file in session_files:
        metadata = load_session_metadata(file)  # Just metadata, not full conversation
        sessions.append({
            'file': file,
            'session_id': metadata['session_id'],
            'created_at': metadata['created_at'],
            'duration': metadata['duration_seconds'],
            'messages_count': metadata['messages_count']
        })

    return sessions
```

### Continuation Dialog

```
┌────────────────────────────────────────────┐
│  Continue Previous Session?                │
├────────────────────────────────────────────┤
│  Found existing sessions for RecCli:       │
│                                            │
│  ○ Start New Session                       │
│                                            │
│  ● Continue from:                          │
│    Session 003 (2 hours ago)               │
│    2h 14m • 187 messages                   │
│    Focus: Context loading strategy         │
│                                            │
│  Recent sessions:                          │
│  • Session 002 (Yesterday, 1h 30m)         │
│  • Session 001 (Oct 27, 2h 15m)            │
│                                            │
│  [ Cancel ]  [ Continue ]                  │
└────────────────────────────────────────────┘
```

### Loading for Continuation

```python
def continue_from_session(session_file, project_dir):
    """
    Load session for continuation
    """
    # 1. Load previous session
    previous_session = load_devsession(session_file)

    # 2. Load current .devproject (might have been updated by other sessions)
    devproject = load_devproject(project_dir / '.devproject')

    # 3. Create continuation session
    new_session = create_continuation_session(
        project_overview=devproject,
        previous_session=previous_session
    )

    # 4. Compact previous session context intelligently
    compacted_context = compact_for_continuation(
        previous_session,
        num_recent_messages=20
    )

    # 5. Load into LLM context
    return {
        'project_overview': devproject,  # Current project state
        'previous_summary': previous_session.summary,  # What happened last time
        'recent_context': compacted_context['recent'],  # Last 20 messages
        'relevant_history': compacted_context['relevant']  # Vector search results
    }
```

## Updating .devproject: When and How

### Update Triggers

**.devproject should be updated at:**

1. **Session End** (always)
   - Session completes normally
   - User stops recording

2. **Compaction** (at 190K tokens)
   - Major work has been done
   - Project likely evolved
   - Need fresh context for continuation

3. **Manual Save** (optional)
   - User clicks "Save Project State"
   - Checkpoint important decisions

**NOT on:**
- Every message (too frequent)
- Every N minutes (not event-driven)

### Update on Compaction

```python
def on_compaction_triggered(session, project_dir):
    """
    Compaction at 190K tokens - update .devproject with progress so far
    """
    print("🔄 Compacting session...")

    # 1. Generate session summary (what happened so far)
    session_summary = generate_session_summary(session.conversation)

    # 2. Update .devproject with progress
    devproject = load_devproject(project_dir / '.devproject')
    updated_devproject = update_project_overview(
        devproject,
        session_summary,
        session.metadata
    )

    # 3. Save updated .devproject
    save_devproject(updated_devproject, project_dir / '.devproject')
    print("  ✓ Updated .devproject with session progress")

    # 4. Compact conversation
    compacted_context = compact_intelligently(session)

    # 5. Continue with fresh context
    return compacted_context
```

### Why Update on Compaction?

**Scenario:**
```
Session starts: 10 AM
- .devproject: "Phase: Architecture, Next: Export Dialog"

Work for 3 hours (180K tokens):
- Implemented export dialog
- Added embedding generation
- Fixed 5 bugs
- Made decision: "Use sentence-transformers for embeddings"

Compaction at 190K (1 PM):
- Update .devproject:
  * New decision logged
  * Phase changed to "Implementation"
  * Export dialog marked as complete
  * Next milestone updated

Continue working (2 PM):
- If you load context, .devproject is current
- If you start new session tomorrow, .devproject is current
- If teammate opens project, they see latest state
```

**Without updating on compaction:**
- .devproject stays stale until session ends
- Multi-hour sessions lose context
- Continuation sees outdated project state

## Complete Storage Flow

### First Session in Project

```
1. User clicks REC in ~/projects/RecCli/
2. RecCli detects project, initializes:
   - Creates .devproject (if needed)
   - Creates .devsessions/ folder
   - Adds both to .gitignore
3. Records session
4. On stop:
   - Generates session summary
   - Updates .devproject
   - Saves to .devsessions/session-20241027-143045-a3f2.devsession
```

### Subsequent Sessions

```
1. User clicks REC in ~/projects/RecCli/
2. RecCli loads:
   - .devproject (current project state)
   - Checks .devsessions/ for previous sessions
3. Asks: "Continue from session-003 or start new?"
4. User chooses "Continue"
5. Loads compacted context from session-003
6. Records continuation
7. On stop:
   - Updates .devproject
   - Saves to .devsessions/session-20241027-160230-b8c1.devsession
```

### Mid-Session Compaction

```
1. Session ongoing (150K tokens)
2. User keeps working (180K tokens)
3. Compaction triggered at 190K:
   - Generate summary so far
   - Update .devproject ← Key: Update now, not just at end
   - Save checkpoint to .devsessions/session-...-checkpoint.devsession
   - Compact context
   - Continue recording
4. Session ends:
   - Update .devproject again (final state)
   - Save final .devsession
   - Mark checkpoint as archived
```

## Settings: User Configuration

### Storage Location Setting

```json
{
  "session_storage": {
    "location": "project_root",  // "project_root" | "home_dir" | "custom"
    "custom_path": null,
    "folder_name": ".devsessions",
    "naming_format": "timestamp",  // "timestamp" | "sequential"
    "auto_gitignore": true,
    "max_sessions_per_project": 100  // Auto-archive older sessions
  }
}
```

### UI: Storage Settings

```
┌────────────────────────────────────────────┐
│  Session Storage Settings                  │
├────────────────────────────────────────────┤
│  Storage Location:                         │
│  ● Project root (.devsessions/ folder)     │
│  ○ Home directory (~/.reccli/sessions/)    │
│  ○ Custom location: [Browse...]            │
│                                            │
│  Naming Format:                            │
│  ● Timestamp (session-20241027-143045..)   │
│  ○ Sequential (session-001, session-002..) │
│                                            │
│  Privacy:                                  │
│  ☑ Auto-add to .gitignore                  │
│  ☐ Encrypt sessions (password protected)   │
│                                            │
│  Retention:                                │
│  Max sessions per project: [100]           │
│  ☑ Auto-archive sessions older than 90 days│
│                                            │
│  [ Reset to Defaults ]  [ Save ]           │
└────────────────────────────────────────────┘
```

## Best Practices

### For Individual Developers

**Recommended:**
- Storage: Project root (`.devsessions/`)
- Naming: Timestamp format
- Gitignore: Yes (private by default)
- Retention: Keep last 50 sessions

**Why:**
- Sessions travel with project
- Easy continuation
- Private by default
- Automatic organization

### For Teams / Open Source

**Recommended:**
- Storage: Project root (`.devsessions/`)
- Naming: Timestamp format
- Gitignore: No (tracked in git)
- Retention: Keep all sessions

**Why:**
- Team members see session history
- Context shared across team
- New contributors understand evolution
- Transparency for open source

### For Sensitive Projects

**Recommended:**
- Storage: Home directory (`~/.reccli/sessions/`)
- Naming: Timestamp format
- Gitignore: N/A (not in project)
- Encryption: Yes (optional password)
- Retention: Keep last 20 sessions

**Why:**
- Can't accidentally commit
- Extra security layer
- Clean project directory

## Summary

**Storage Location:** `.devsessions/` in project root (default)
**Naming:** `session-{timestamp}-{id}.devsession`
**.gitignore:** Auto-added (privacy by default)
**.devproject updates:** Session end AND compaction (190K)
**Continuation:** Load most recent session, compact intelligently

**Result:** Frictionless continuation with intelligent context management.
