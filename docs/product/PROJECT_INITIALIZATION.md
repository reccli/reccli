# Project Initialization & Context Loading

## Overview

RecCli needs to know **which project** you're working on to load the right context. This document describes the complete UX flow for project detection, selection, and initialization.

## UI Design: Project Dropdown

### Location

**Left of the record button:**

```
┌─────────────────────────────────────┐
│  [RecCli ▼]  [● REC]  [⚙️]         │
└─────────────────────────────────────┘
     ↑
  Project selector
```

### Dropdown Content

```
┌─────────────────────────────────────┐
│  Recent Projects:                   │
├─────────────────────────────────────┤
│  ● RecCli                           │
│    ~/projects/RecCli                │
│    Last used: 2 hours ago           │
│                                     │
│  ● EstimatePro                      │
│    ~/projects/EstimatePro           │
│    Last used: Yesterday             │
│                                     │
│  ● MyOtherProject                   │
│    ~/projects/MyOtherProject        │
│    Last used: 3 days ago            │
│                                     │
├─────────────────────────────────────┤
│  📂 Detect Current Directory        │
│  ➕ Create New Project              │
│  ⚙️  Manage Projects...             │
└─────────────────────────────────────┘
```

## Project Cache: ~/.reccli/projects.json

### Structure

```json
{
  "recent_projects": [
    {
      "name": "RecCli",
      "path": "/home/user/projects/RecCli",
      "git_remote": "github.com/willluecke/RecCli",
      "git_branch": "main",
      "has_devproject": true,
      "last_used": "2024-10-27T18:30:00Z",
      "total_sessions": 5,
      "favorite": false
    },
    {
      "name": "EstimatePro",
      "path": "/home/user/projects/EstimatePro",
      "git_remote": "github.com/willluecke/EstimatePro",
      "git_branch": "develop",
      "has_devproject": true,
      "last_used": "2024-10-26T14:20:00Z",
      "total_sessions": 12,
      "favorite": true
    }
  ],
  "settings": {
    "auto_detect_on_startup": true,
    "max_recent_projects": 10
  }
}
```

## Startup Flow

### On RecCli Launch

```python
def on_startup():
    """
    RecCli startup - detect or select project
    """
    # 1. Load project cache
    cache = load_project_cache()

    # 2. Try to detect current directory
    cwd = os.getcwd()
    detected_project = detect_project_from_path(cwd)

    if detected_project:
        # Found project in current directory
        if detected_project in cache.recent_projects:
            # Known project - auto-select
            select_project(detected_project)
            load_project_context(detected_project)
            show_welcome_message(detected_project)
        else:
            # New project detected - offer to initialize
            prompt_initialize_new_project(detected_project)
    else:
        # No project detected - show dropdown with recent projects
        if cache.recent_projects:
            # Default to most recent
            default_project = cache.recent_projects[0]
            select_project(default_project)
        else:
            # First time user - show welcome
            show_first_time_welcome()


def detect_project_from_path(path):
    """
    Detect if path is inside a project
    """
    # Look for git repo
    git_root = find_git_root(path)
    if git_root:
        return {
            'path': git_root,
            'name': git_root.name,
            'git_remote': get_git_remote(git_root)
        }

    # Look for common project markers
    markers = ['package.json', 'requirements.txt', 'go.mod', 'Cargo.toml']
    for marker in markers:
        if (path / marker).exists():
            return {
                'path': path,
                'name': path.name
            }

    return None
```

## Project Selection Flow

### Option 1: Auto-Detect Current Directory

**User clicks "📂 Detect Current Directory"**

```python
def detect_current_directory():
    """
    Detect project from current terminal directory
    """
    cwd = os.getcwd()
    project = detect_project_from_path(cwd)

    if project:
        # Check if .devproject exists
        devproject_path = project['path'] / '.devproject'

        if devproject_path.exists():
            # Load existing project
            load_project(project)
            update_cache(project)
            show_message(f"✓ Loaded {project['name']}")
        else:
            # New project - initialize
            prompt_initialize_project(project)
    else:
        show_error("No project detected in current directory")
```

### Option 2: Select from Recent Projects

**User clicks a recent project**

```python
def select_recent_project(project):
    """
    Load a project from recent list
    """
    # 1. Load .devproject
    devproject_path = Path(project['path']) / '.devproject'

    if devproject_path.exists():
        project_overview = load_devproject(devproject_path)
    else:
        # .devproject deleted? Reinitialize
        project_overview = reinitialize_project(project)

    # 2. Update cache
    project['last_used'] = datetime.now().isoformat()
    update_cache(project)

    # 3. Show welcome message with context
    show_welcome_with_context(project, project_overview)
```

### Option 3: Create New Project

**User clicks "➕ Create New Project"**

Shows initialization dialog (see below).

## Project Initialization: Branching Logic

When RecCli detects a project without .devproject, it branches based on whether the project is **empty** or has **existing code**:

```python
def prompt_initialize_project(project):
    """
    Initialize .devproject - branch based on project state
    """
    project_path = Path(project['path'])

    # Check if project directory is empty or has minimal files
    if is_empty_project(project_path):
        # Empty project → Conversational onboarding
        show_empty_project_onboarding(project)
    else:
        # Existing codebase → Give user choice
        show_existing_project_choice(project)


def is_empty_project(project_path):
    """
    Determine if project is empty (no significant code files)
    """
    # Ignore common non-code files
    ignore = {'.git', '.gitignore', 'README.md', 'LICENSE', '.DS_Store',
              'node_modules', '__pycache__', 'venv', '.env'}

    files = list(project_path.rglob('*'))
    code_files = [f for f in files
                  if f.is_file()
                  and f.name not in ignore
                  and not any(p in ignore for p in f.parts)]

    # Empty if fewer than 3 code files
    return len(code_files) < 3


def show_empty_project_onboarding(project):
    """
    Empty project → Conversational onboarding to capture intent
    """
    show_dialog(
        title="New Project Setup",
        message=f"I don't see any code in {project['name']} yet.\n\n"
                "Let me ask you a few questions to understand what you're building.\n"
                "This helps me make better suggestions as you code!",
        buttons=["Continue with Setup", "Skip for Now"]
    )

    if user_choice == "Continue":
        # Run conversational onboarding (see PROJECT_ONBOARDING.md)
        devproject = run_conversational_onboarding(project)
        save_devproject(devproject, project_path / '.devproject')
    else:
        # Create minimal .devproject
        create_minimal_devproject(project)


def show_existing_project_choice(project):
    """
    Existing codebase → Let user choose initialization method
    """
    choice = show_dialog(
        title="Initialize RecCli",
        message=f"I see {project['name']} has existing code.\n\n"
                "How would you like to create the project overview?",
        options=[
            {
                "id": "scan",
                "title": "🔍 Scan Codebase (Recommended)",
                "description": "I'll analyze your code, README, and dependencies to "
                               "generate a project overview automatically.",
                "time": "~30 seconds"
            },
            {
                "id": "interview",
                "title": "💬 Answer Questions",
                "description": "I'll ask you questions about the project to build "
                               "a more complete overview with business context.",
                "time": "~2 minutes"
            },
            {
                "id": "minimal",
                "title": "⚡ Minimal Setup",
                "description": "Create a basic project file, I'll learn as we work.",
                "time": "instant"
            }
        ]
    )

    if choice == "scan":
        devproject = smart_scan_codebase(project)

        # Show what was found, let user review/edit
        if show_confirmation_with_edit(devproject):
            save_devproject(devproject, project_path / '.devproject')
        else:
            # User wants to answer questions instead
            show_existing_project_choice(project)

    elif choice == "interview":
        # Run conversational onboarding even though code exists
        # This captures business context that code can't reveal
        devproject = run_conversational_onboarding(project,
                                                   prefill_from_scan=True)
        save_devproject(devproject, project_path / '.devproject')

    elif choice == "minimal":
        create_minimal_devproject(project)
```

### Decision Tree

```
                        RecCli Starts
                              |
                              v
                    Check for .devproject
                              |
                    ┌─────────┴─────────┐
                    |                   |
              YES: Exists          NO: Missing
                    |                   |
                    v                   v
            Load .devproject    Detect project type
            Show welcome               |
            Start recording            |
                              ┌─────────┴─────────┐
                              |                   |
                         Empty Project      Existing Code
                        (< 3 code files)   (has code files)
                              |                   |
                              v                   v
                    ┌─────────────────┐   ┌─────────────────────────┐
                    │ Conversational  │   │  Give User Choice:      │
                    │   Onboarding    │   │                         │
                    │                 │   │  [ ] Scan Codebase      │
                    │ "What are you   │   │  [ ] Answer Questions   │
                    │  building?"     │   │  [ ] Minimal Setup      │
                    └────────┬────────┘   └───────┬─────────────────┘
                             |                     |
                             |          ┌──────────┼──────────┐
                             |          |          |          |
                             |          v          v          v
                             |     Smart Scan  Onboard  Create Basic
                             |          |          |          |
                             |          v          v          v
                             |     Review &   Pre-fill + Create .devproject
                             |     Edit?     Questions    (minimal)
                             |          |          |          |
                             |          v          |          |
                             |     Accept?        |          |
                             |     /    \         |          |
                             |   Yes    No        |          |
                             |    |      |        |          |
                             |    |   Go back     |          |
                             |    |    to menu    |          |
                             v    v               v          v
                          ┌───────────────────────────────────┐
                          │   .devproject Created & Saved     │
                          │   Auto-update .gitignore          │
                          │   Add to projects cache           │
                          └──────────────┬────────────────────┘
                                         v
                               Show welcome message
                                Start recording
                                   [● REC]
```

### Decision Flow with Context Quality

```
                    Empty Project                Existing Codebase
                         |                              |
                         v                              v
            ┌────────────────────────┐    ┌───────────────────────────┐
            │ Conversational         │    │ User Chooses Approach:    │
            │ Onboarding             │    │                           │
            │                        │    │ A: Scan (Fast)            │
            │ Captures:              │    │ B: Interview (Complete)   │
            │ • Purpose & value prop │    │ C: Minimal (Quick)        │
            │ • Target users         │    │                           │
            │ • Core features        │    │                           │
            │ • Goals & metrics      │    │                           │
            │                        │    │                           │
            │ Result:                │    │                           │
            │ ★★★★★ Rich context     │    │                           │
            │ (Strategic + Technical)│    │                           │
            └────────────────────────┘    └─────┬─────────────────────┘
                                                 |
                              ┌──────────────────┼──────────────────┐
                              |                  |                  |
                              v                  v                  v
                    ┌─────────────────┐ ┌──────────────┐ ┌─────────────┐
                    │ Option A: Scan  │ │ Option B:    │ │ Option C:   │
                    │                 │ │ Interview    │ │ Minimal     │
                    │ Captures:       │ │              │ │             │
                    │ • Tech stack    │ │ Captures:    │ │ Captures:   │
                    │ • Architecture  │ │ • Everything │ │ • Name only │
                    │ • From README   │ │ • Pre-fills  │ │             │
                    │                 │ │   tech from  │ │             │
                    │ Result:         │ │   scan       │ │ Result:     │
                    │ ★★★☆☆ Technical │ │              │ │ ★☆☆☆☆ Basic │
                    │ context only    │ │ Result:      │ │             │
                    │                 │ │ ★★★★★ Rich   │ │             │
                    └─────────────────┘ └──────────────┘ └─────────────┘

                          Fast              Complete           Instant
                        (~30 sec)          (~2 min)          (<5 sec)
```

## Project Initialization: Implementation Strategies

### Strategy A: Smart Scan (For Existing Codebases)

**Automatically analyze existing repo to generate .devproject:**

```python
def initialize_project_smart(project_path):
    """
    Smart initialization - scan repo and generate overview
    """
    print("🔍 Analyzing project...")

    # 1. Get basic info from git
    project_info = {
        'name': get_repo_name(project_path),
        'repository': get_git_remote(project_path),
        'license': detect_license_file(project_path)
    }

    # 2. Analyze tech stack
    print("  Detecting languages and frameworks...")
    tech_stack = analyze_tech_stack(project_path)
    """
    Scans:
    - package.json → Node.js, dependencies
    - requirements.txt / pyproject.toml → Python, packages
    - go.mod → Go, modules
    - Cargo.toml → Rust, crates
    - pom.xml / build.gradle → Java, Maven/Gradle
    - Gemfile → Ruby, gems
    - composer.json → PHP, Composer
    """

    # 3. Read existing documentation
    print("  Reading existing documentation...")
    readme_content = read_readme(project_path)
    if readme_content:
        project_info['description'] = extract_description(readme_content)
        project_info['purpose'] = extract_purpose(readme_content)

    # 4. Analyze file structure
    print("  Analyzing file structure...")
    structure = analyze_project_structure(project_path)
    """
    Detects:
    - src/, lib/, pkg/ → Source directories
    - test/, tests/ → Testing setup
    - docs/ → Documentation
    - .github/workflows/ → CI/CD
    - Common patterns (MVC, microservices, monorepo, etc.)
    """

    # 5. Optional: AI-powered generation
    if has_api_key():
        print("  Generating overview with AI...")
        overview = generate_overview_with_ai(
            project_info, tech_stack, readme_content, structure
        )
    else:
        print("  Creating basic overview...")
        overview = create_basic_overview(
            project_info, tech_stack, structure
        )

    # 6. Create .devproject file
    save_devproject(overview, project_path / '.devproject')

    # 7. Add to .gitignore
    auto_update_gitignore(project_path)

    print("✓ Project initialized!")
    return overview


def analyze_tech_stack(project_path):
    """
    Detect languages, frameworks, and dependencies
    """
    tech_stack = {
        'languages': [],
        'frameworks': [],
        'key_dependencies': []
    }

    # Node.js / JavaScript / TypeScript
    package_json = project_path / 'package.json'
    if package_json.exists():
        data = json.loads(package_json.read_text())
        tech_stack['languages'].append('JavaScript')

        if 'typescript' in data.get('devDependencies', {}):
            tech_stack['languages'].append('TypeScript')

        # Detect frameworks
        deps = {**data.get('dependencies', {}), **data.get('devDependencies', {})}
        if 'react' in deps:
            tech_stack['frameworks'].append('React')
        if 'next' in deps:
            tech_stack['frameworks'].append('Next.js')
        if 'express' in deps:
            tech_stack['frameworks'].append('Express')
        if 'vue' in deps:
            tech_stack['frameworks'].append('Vue')

        # Key dependencies
        tech_stack['key_dependencies'].extend(list(deps.keys())[:10])

    # Python
    requirements = project_path / 'requirements.txt'
    pyproject = project_path / 'pyproject.toml'
    if requirements.exists() or pyproject.exists():
        tech_stack['languages'].append('Python')

        if requirements.exists():
            deps = requirements.read_text().splitlines()
            tech_stack['key_dependencies'].extend([d.split('==')[0] for d in deps[:10]])

            # Detect frameworks
            if any('django' in d.lower() for d in deps):
                tech_stack['frameworks'].append('Django')
            if any('flask' in d.lower() for d in deps):
                tech_stack['frameworks'].append('Flask')
            if any('fastapi' in d.lower() for d in deps):
                tech_stack['frameworks'].append('FastAPI')

    # Go
    go_mod = project_path / 'go.mod'
    if go_mod.exists():
        tech_stack['languages'].append('Go')
        # Parse go.mod for modules

    # Rust
    cargo = project_path / 'Cargo.toml'
    if cargo.exists():
        tech_stack['languages'].append('Rust')
        # Parse Cargo.toml for crates

    return tech_stack


def generate_overview_with_ai(project_info, tech_stack, readme, structure):
    """
    Use AI to generate comprehensive project overview
    """
    prompt = f"""
Analyze this project and generate a structured overview:

Project Name: {project_info['name']}
Repository: {project_info.get('repository', 'N/A')}
License: {project_info.get('license', 'N/A')}

Tech Stack:
- Languages: {', '.join(tech_stack['languages'])}
- Frameworks: {', '.join(tech_stack['frameworks'])}
- Key Dependencies: {', '.join(tech_stack['key_dependencies'][:5])}

README Content:
{readme[:2000]}

File Structure:
{structure}

Generate a .devproject overview with:
1. Concise description (1 sentence - what it is)
2. Project purpose (2-3 sentences - what it does/enables)
3. Core value proposition (1-2 sentences - what problem it solves, why it matters)
4. Architecture overview (high-level system design)
5. Current development status (alpha/beta/production/maintenance)

Output as JSON matching the .devproject schema.
"""

    response = claude_api.generate(prompt)
    return json.loads(response)
```

### Strategy B: Conversational Onboarding (For Empty Projects or User Choice)

**See [PROJECT_ONBOARDING.md](PROJECT_ONBOARDING.md) for complete details.**

When project is empty or user chooses to answer questions instead of scan, run AI-guided interview to capture:
- Project purpose and value proposition
- Target users and use cases
- Core features planned
- Development stage and milestones
- Optional: ICP, success metrics, go-to-market strategy

**Benefit:** Captures business/strategic context that can't be inferred from code.

**Pre-filling for existing codebases:**
```python
def run_conversational_onboarding(project, prefill_from_scan=False):
    """
    Run AI interview, optionally pre-fill technical details from code scan
    """
    if prefill_from_scan:
        # Scan codebase first to pre-fill technical fields
        tech_info = quick_scan_tech_stack(project['path'])

        # Start interview with tech details already known
        # Focus questions on business/product context
        prefilled_data = {
            'tech_stack': tech_info['tech_stack'],
            'platform': tech_info['platform'],
            'existing_features': tech_info['features_detected']
        }
    else:
        prefilled_data = None

    # Run conversational interview
    devproject = ai_guided_interview(project, prefilled_data)
    return devproject
```

### Strategy B (Legacy): Guided Manual Setup

**Note:** This is the old approach - replaced by conversational onboarding above.

**User fills in project details:**

```
┌──────────────────────────────────────────┐
│  Create New Project                      │
├──────────────────────────────────────────┤
│  Project Path:                           │
│  [~/projects/RecCli        ] [Browse...] │
│                                          │
│  Project Name:                           │
│  [RecCli                   ]             │
│                                          │
│  Description:                            │
│  [CLI terminal recorder with AI-powered  │
│   session management                  ]  │
│                                          │
│  Purpose:                                │
│  [Enable developers to record, summarize │
│   and intelligently continue terminal    │
│   sessions                             ]  │
│                                          │
│  Value Proposition:                      │
│  [Solves AI context loss by building    │
│   living project documentation          │
│   automatically                        ]  │
│                                          │
│  ☑ Scan codebase automatically           │
│  ☑ Analyze existing documentation        │
│  ☑ Use AI to generate overview (faster)  │
│                                          │
│  [ Cancel ]  [ Create ]                  │
└──────────────────────────────────────────┘
```

### Strategy C: Minimal Setup (Lazy Initialization)

**Start recording without .devproject, build context from session:**

```python
def lazy_initialize_project(project_path):
    """
    Initialize project during/after first session
    """
    # User starts recording without selecting project
    # RecCli detects this is a new project

    show_message("""
    New project detected!
    I'll build a project overview as we work.
    """)

    # During session: Track context
    session.is_initialization_session = True

    # At session end: Generate overview from session
    def on_session_end(session):
        if session.is_initialization_session:
            # AI generates overview from conversation
            overview = generate_overview_from_session(
                session.conversation,
                session.summary
            )

            # Save .devproject
            save_devproject(overview, project_path / '.devproject')

            show_message("""
            ✓ Project overview created from this session!
            Next time you start, I'll have full context.
            """)
```

## Welcome Messages

### With Context (Existing Project)

```python
def show_welcome_with_context(project, overview):
    """
    Friendly welcome with project context loaded
    """
    message = f"""
👋 Welcome back to {project['name']}!

{overview['project']['description']}

Current Phase: {overview['project_phases']['current_phase']}
Next Milestone: {overview['project_phases']['next_milestones'][0]['milestone']}
Total Sessions: {len(overview['sessions'])}

Last session: {get_last_session_summary(overview)}

Ready to continue? Hit record!
"""

    show_notification(message)
```

### Without Context (New Project)

```python
def show_welcome_new_project(project):
    """
    Welcome for new project initialization
    """
    message = f"""
👋 New project detected: {project['name']}

I'll help you build a project overview as we work.
This will include:
- Architecture decisions
- Tech stack tracking
- Development phases
- Session history

Hit record to start your first session!
"""

    show_notification(message)
```

## Complete Startup Flow Diagram

```
RecCli Starts
     ↓
Check current directory
     ↓
  Project detected?
     ├─ Yes → Known project in cache?
     │         ├─ Yes → Load .devproject
     │         │         └─ Show welcome with context ✓
     │         └─ No → Has .devproject?
     │                   ├─ Yes → Load it + add to cache
     │                   │         └─ Show welcome with context ✓
     │                   └─ No → Offer initialization
     │                             └─ Show "Create Project" dialog
     │
     └─ No → Show project dropdown
               ├─ User selects recent project
               │   └─ Load .devproject
               │       └─ Show welcome with context ✓
               │
               ├─ User clicks "Detect Current Directory"
               │   └─ (back to "Project detected?" step)
               │
               └─ User clicks "Create New Project"
                   └─ Show initialization dialog
                       ├─ Smart scan (recommended)
                       ├─ Guided manual setup
                       └─ Lazy initialization
```

## Implementation Priority

### Phase 1: MVP (Now)
- ✅ Project cache structure
- ✅ Dropdown UI design
- ✅ Auto-detect current directory
- ✅ Smart scan initialization
- ✅ Welcome messages

### Phase 2: Enhanced (Q1 2025)
- AI-powered overview generation
- Guided manual setup dialog
- Project favorites
- Recent projects sorting

### Phase 3: Advanced (Q2 2025)
- Lazy initialization
- Multi-project sessions
- Project templates
- Team project sharing

## Key Design Decisions

**Q: Where to store project cache?**
A: `~/.reccli/projects.json` - Centralized, survives repo deletion

**Q: How to detect current project?**
A: Git repo root first, then project markers (package.json, etc.)

**Q: What if user changes directories mid-session?**
A: Session stays on selected project - manual switch via dropdown

**Q: How to handle monorepos?**
A: One .devproject at monorepo root, subprojects tracked in architecture

**Q: What if .devproject gets deleted?**
A: Offer to reinitialize from cache + repo scan

---

**Result:** Zero-friction project context loading with smart defaults and flexible initialization.
