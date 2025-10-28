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

## Project Initialization: Three Strategies

### Strategy A: Smart Scan (Recommended)

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
1. Concise description (1 sentence)
2. Project purpose (2-3 sentences explaining why it exists)
3. Architecture overview (high-level system design)
4. Current development status (alpha/beta/production/maintenance)

Output as JSON matching the .devproject schema.
"""

    response = claude_api.generate(prompt)
    return json.loads(response)
```

### Strategy B: Guided Manual Setup

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
│  ☑ Scan codebase automatically           │
│  ☑ Analyze existing documentation        │
│  ☑ Use AI to generate overview (faster)  │
│                                          │
│  [ Cancel ]  [ Create ]                  │
└──────────────────────────────────────────┘
```

### Strategy C: Lazy Initialization (Mid-Conversation)

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
