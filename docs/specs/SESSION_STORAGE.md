# Session Storage Strategy

**Status:** Design document for an optional future project-layer storage model.

The live CLI currently defaults to the configured sessions directory under `~/reccli/sessions`. This document describes a richer project-root storage strategy centered on `.devproject` and `.devsessions/`, which is not yet the mainline implementation and should be treated as optional, not required for basic RecCli use.

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

### Option B: Home Directory `~/reccli/sessions/{project-name}/`

```
~/reccli/
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

## Proposed Future Default: Option A (`.devsessions/` in project root)

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

### Update Trigger: Export Only

**.devproject should be updated at:**

1. **Session Export** (when user clicks STOP)
   - Session completes normally
   - User explicitly ends recording
   - Export dialog appears
   - Single, clean save point

**NOT on:**
- Compaction (internal optimization only)
- Every message (too frequent)
- Every N minutes (not event-driven)
- Auto-save checkpoints (recovery only)

**Why only on export:**
✅ Simple mental model ("I stop, everything saves")
✅ One session = one update
✅ Clean, predictable
✅ Export is the explicit commit moment

### The Update Challenge: What's Project-Level?

**The Problem:**

```
Session summary contains:
- Implemented export dialog (project-level? Maybe - new feature)
- Fixed typo in README (session-level - trivial change)
- Decided to use sentence-transformers (project-level! - architectural decision)
- Debugged webhook signature issue (session-level - bug fix)
- Changed CSS color (session-level - minor tweak)
```

**How does AI know what's project-level vs session-level?**

### Intelligent Update System

```python
def update_devproject_on_export(session, project_dir):
    """
    Intelligently update .devproject from session summary
    Uses AI to classify and verify changes
    """
    # 1. Load current .devproject
    current_devproject = load_devproject(project_dir / '.devproject')

    # 2. Extract session summary
    session_summary = session.summary

    # 3. AI classification: What's project-level?
    proposed_updates = classify_project_level_changes(
        current_devproject,
        session_summary
    )

    # 4. Show verification UI to user
    if proposed_updates:
        user_response = show_devproject_update_dialog(
            current_devproject,
            proposed_updates
        )

        if user_response.approved:
            # Apply approved changes
            updated_devproject = apply_updates(
                current_devproject,
                user_response.approved_changes
            )
            save_devproject(updated_devproject, project_dir / '.devproject')
        elif user_response.manual_edit:
            # User wants to steer the update
            manual_prompt = user_response.manual_prompt
            updated_devproject = generate_with_steering(
                current_devproject,
                session_summary,
                manual_prompt
            )
            save_devproject(updated_devproject, project_dir / '.devproject')
    else:
        # No project-level changes detected
        print("✓ No project-level changes to update")
```

### AI Classification Prompt

```python
def classify_project_level_changes(devproject, session_summary):
    """
    Use AI to identify project-level changes from session
    """
    prompt = f"""
You are analyzing a development session to identify PROJECT-LEVEL changes
that should update the project overview.

Current Project Overview:
{json.dumps(devproject, indent=2)}

Session Summary:
- Goal: {session_summary.overview}
- Decisions: {json.dumps(session_summary.decisions, indent=2)}
- Code Changes: {json.dumps(session_summary.code_changes, indent=2)}
- Problems Solved: {json.dumps(session_summary.problems_solved, indent=2)}

Classify which items are PROJECT-LEVEL (should update .devproject):

PROJECT-LEVEL items are:
✓ Architectural decisions (tech stack, design patterns)
✓ New major features/components
✓ Technology additions (new dependencies, frameworks)
✓ Significant architecture changes
✓ Project phase transitions (alpha → beta, etc.)
✓ Milestone completions

SESSION-LEVEL items are (do NOT include):
✗ Bug fixes
✗ Minor code refactors
✗ Typo corrections
✗ Small UI tweaks
✗ Debugging specific issues
✗ Routine maintenance

Output JSON:
{{
  "project_level_decisions": [
    {{
      "item": "Decision: Use sentence-transformers for embeddings",
      "reasoning": "Architectural decision affecting embedding strategy",
      "action": "add_to_key_decisions",
      "impact": "high"
    }}
  ],
  "tech_stack_additions": [
    {{
      "item": "sentence-transformers",
      "category": "key_dependencies",
      "reasoning": "New core dependency for embedding generation"
    }}
  ],
  "milestone_completions": [
    {{
      "milestone": "MVP - Export Dialog",
      "reasoning": "Export dialog was completed this session",
      "next_milestone": "Vector Embeddings"
    }}
  ],
  "phase_transitions": [],
  "session_level_items": [
    "Fixed typo in README",
    "Debugged webhook signature",
    "Changed button color"
  ]
}}

Be conservative - when in doubt, classify as session-level.
"""

    response = claude_api.generate(prompt)
    return json.loads(response)
```

### Verification UI: Diff View with Accept/Reject

```
┌────────────────────────────────────────────────────────────┐
│  Update Project Overview                                   │
├────────────────────────────────────────────────────────────┤
│  Session completed! Review proposed changes to .devproject:│
│                                                            │
│  ✅ KEY DECISIONS (1 addition)                            │
│  ┌────────────────────────────────────────────────────┐   │
│  │  + decision_004:                                   │   │
│  │    "Use sentence-transformers for embeddings"     │   │
│  │    Reasoning: Faster than API calls, runs locally │   │
│  │    Impact: high                                    │   │
│  │                                             [✓][✗] │   │
│  └────────────────────────────────────────────────────┘   │
│                                                            │
│  ✅ TECH STACK (1 addition)                               │
│  ┌────────────────────────────────────────────────────┐   │
│  │  + key_dependencies:                               │   │
│  │    - sentence-transformers                         │   │
│  │                                             [✓][✗] │   │
│  └────────────────────────────────────────────────────┘   │
│                                                            │
│  ✅ MILESTONES (1 update)                                 │
│  ┌────────────────────────────────────────────────────┐   │
│  │  - Next: "MVP - Export Dialog"                     │   │
│  │  + Next: "Vector Embeddings"                       │   │
│  │    (Export dialog completed)                       │   │
│  │                                             [✓][✗] │   │
│  └────────────────────────────────────────────────────┘   │
│                                                            │
│  ℹ️  Session-level items (not included):                  │
│  • Fixed typo in README                                   │
│  • Debugged webhook signature                             │
│  • Changed button color                                   │
│                                                            │
│  ┌────────────────────────────────────────────────────┐   │
│  │  ✏️  Manual Steering (optional):                   │   │
│  │  [Also update the architecture section to...     ] │   │
│  │                                                    │   │
│  │  [Generate]                                        │   │
│  └────────────────────────────────────────────────────┘   │
│                                                            │
│  [ Accept All ]  [ Accept Selected ]  [ Skip Update ]     │
└────────────────────────────────────────────────────────────┘
```

### UI Behavior

**Green indicators (additions):**
```
+ decision_004: "Use sentence-transformers"
+ key_dependencies: "sentence-transformers"
```

**Red indicators (removals):**
```
- Next: "MVP - Export Dialog"  (completed)
```

**Yellow indicators (changes):**
```
~ current_phase: "Architecture" → "Implementation"
```

**Individual Accept/Reject:**
- Each proposed change has [✓] [✗] buttons
- Check/uncheck to approve/reject
- Default: All checked (trust AI classification)

**Manual Steering:**
- Text box for verbal instructions
- Example: "Also update the architecture section to mention the new embedding pipeline"
- Click "Generate" → AI applies additional changes based on instruction
- Shows diff again for approval

### Complete Flow

```
1. User clicks STOP
2. RecCli generates session summary
3. AI analyzes summary:
   - Classifies project-level vs session-level
   - Proposes specific .devproject updates
4. Shows diff dialog with green/red/yellow changes
5. User reviews:
   Option A: Click "Accept All" (trust AI)
   Option B: Uncheck items, click "Accept Selected"
   Option C: Add manual steering, click "Generate", review again
   Option D: Click "Skip Update" (keep .devproject unchanged)
6. Approved changes applied to .devproject
7. .devsession file saved to .devsessions/
8. Done!
```

### Safety Features

**Conservative by default:**
- AI errs on side of classifying as session-level
- When in doubt, don't update .devproject
- User can always manually add later

**Transparent:**
- Show exactly what's being changed
- Show reasoning for each classification
- List session-level items that are NOT included

**Controllable:**
- User has final say on every change
- Can reject individual items
- Can add manual instructions
- Can skip update entirely

**Reversible:**
- .devproject is in git (if tracked)
- Can revert changes
- Previous versions accessible

### Example Scenarios

#### Scenario 1: Clear Project-Level Work

```
Session: Implemented authentication system
AI Classifies:
✅ Project-level:
  - New component: "Authentication System"
  - Tech stack: "passport.js", "bcrypt"
  - Decision: "Use JWT tokens (stateless scaling)"

✗ Session-level:
  - Fixed login button styling
  - Debugged password validation

User: Clicks "Accept All" (takes 2 seconds)
```

#### Scenario 2: Mixed Work

```
Session: Fixed bugs and added feature
AI Classifies:
✅ Project-level:
  - New feature: "Export to PDF"
  - Tech stack: "puppeteer"

✗ Session-level:
  - Fixed memory leak in webhook handler
  - Updated dependencies (routine maintenance)
  - Changed log format

User: Reviews, accepts PDF addition, done
```

#### Scenario 3: No Project-Level Changes

```
Session: Bug fixes and refactoring
AI Classifies:
✗ All session-level:
  - Fixed null pointer exception
  - Refactored database queries
  - Updated tests

Dialog: "No project-level changes detected. Skip update?"
User: Clicks "Yes" (no .devproject update needed)
```

#### Scenario 4: Manual Steering

```
Session: Architecture redesign
AI Classifies:
✅ Project-level:
  - Decision: "Move to microservices architecture"
  - New components: "API Gateway", "User Service", "Order Service"

User reviews, then adds steering:
"Also update the architecture overview to explain the service
communication pattern and mention we're using message queues"

Clicks "Generate" → AI applies additional changes → Shows new diff
User: Approves → Done
```

### Implementation Priority

**Phase 1: Basic Update (MVP)**
```python
# Simple update without verification
# Trust AI classification, auto-apply
update_devproject_automatically(session)
```

**Phase 2: Verification UI**
```python
# Show diff dialog
# Accept all / Skip update buttons
show_diff_dialog(proposed_changes)
```

**Phase 3: Granular Control**
```python
# Individual accept/reject per item
# Show reasoning for each classification
show_granular_dialog(proposed_changes)
```

**Phase 4: Manual Steering**
```python
# Text box for verbal instructions
# Re-generate with steering
# Show updated diff
allow_manual_steering(session)
```

## Complete Storage Flow

### First Session in Project

```
1. User clicks REC in ~/projects/RecCli/
2. RecCli detects project, initializes:
   - Creates .devproject (if needed)
   - Creates .devsessions/ folder
   - Adds both to .gitignore
3. Records session (auto-save checkpoints every 20 messages)
4. On stop & export:
   - Generates session summary
   - AI classifies project-level changes
   - Shows verification dialog
   - User approves updates
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
6. Records continuation (auto-save checkpoints)
7. On stop & export:
   - Generates session summary
   - AI classifies changes
   - Shows verification dialog
   - Updates .devproject
   - Saves to .devsessions/session-20241027-160230-b8c1.devsession
```

### Mid-Session Compaction (Internal Only)

```
1. Session ongoing (150K tokens)
2. User keeps working (180K tokens)
3. Compaction triggered at 190K:
   - Generate interim summary (for compaction only)
   - Save checkpoint to .devsessions/.checkpoint.devsession
   - Compact context (summary + recent + vectors)
   - Continue recording
   - NO .devproject update (wait for export)
4. Session ends (export):
   - Generate final summary (from full session)
   - AI classifies project-level changes
   - Shows verification dialog
   - Update .devproject ← Single update point
   - Save final .devsession
   - Clean up checkpoint
```

**Key:** Compaction is internal optimization, export is the explicit save point.

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
│  ○ Home directory (~/reccli/sessions/)     │
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
- Storage: Home directory (`~/reccli/sessions/`)
- Naming: Timestamp format
- Gitignore: N/A (not in project)
- Encryption: Yes (optional password)
- Retention: Keep last 20 sessions

**Why:**
- Can't accidentally commit
- Extra security layer
- Clean project directory

## Summary

**Storage Location:** Current CLI uses `~/reccli/sessions/`; proposed project-root strategy uses `.devsessions/`
**Naming:** `session-{timestamp}-{id}.devsession`
**.gitignore:** Project-root strategy would manage this explicitly or by user confirmation
**.devproject updates:** Planned project-layer behavior, not current CLI default
**Continuation:** Load most recent session, compact intelligently

**Result:** Frictionless continuation with intelligent context management.
