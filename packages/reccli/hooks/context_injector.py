"""
Context injection for Claude Code sessions.

Handles two injection points:
- SessionStart: auto-load .devproject if in a project, or list available projects
- PostCompact: re-inject .devproject after context window compression
"""

import os
from pathlib import Path
from typing import Optional, List

from ..project.devproject import (
    DevProjectManager,
    discover_project_root,
    generate_compact_tree,
    resolve_devproject_path,
)


def _session_rules() -> str:
    """Session rules injected at start and re-injected after compaction."""
    return (
        "SESSION RULES:\n"
        "1. SAVE ON EXIT: When the user signals they're wrapping up ('that's it', 'thanks', 'I'm done', "
        "'let's stop here'), you MUST call save_session_notes before they leave. Do not wait to be asked.\n"
        "2. CONTEXT SWITCHING: If the user starts discussing a different project, "
        "ask them: 'It sounds like you want to switch to [project]. Want me to save notes for the current "
        "session and load that project?' If they confirm, call save_session_notes for the current project, "
        "then call load_project_context for the new project."
    )


def get_post_compact_context(cwd: str) -> Optional[str]:
    """Build context to inject after Claude Code compaction."""
    project_root = discover_project_root(Path(cwd).resolve())
    if project_root is None:
        return None
    devproject_path = resolve_devproject_path(project_root)
    if not devproject_path.exists():
        return None
    context = _build_project_context(project_root, "[RecCli] Project context re-injected after compaction.")
    context += "\n\n" + _session_rules()
    return context


REGISTRY_PATH = Path.home() / ".reccli" / "projects.json"


def _load_project_registry() -> List[dict]:
    """Load the project registry from ~/.reccli/projects.json."""
    if not REGISTRY_PATH.exists():
        return []
    try:
        import json
        with open(REGISTRY_PATH) as f:
            data = json.load(f)
        return data.get("projects", [])
    except Exception:
        return []


def register_project(project_root: Path, name: str = "") -> None:
    """Add a project to the global registry. Called by project_init."""
    import json
    REGISTRY_PATH.parent.mkdir(parents=True, exist_ok=True)

    registry = _load_project_registry()
    root_str = str(project_root.resolve())

    # Update or add
    for proj in registry:
        if proj.get("path") == root_str:
            if name:
                proj["name"] = name
            return
    registry.append({"path": root_str, "name": name or project_root.name})

    with open(REGISTRY_PATH, "w") as f:
        json.dump({"projects": registry}, f, indent=2)


def _find_registered_projects() -> List[Path]:
    """Find all registered projects from ~/.reccli/projects.json.

    Falls back to scanning common directories if no registry exists.
    """
    # Check registry first
    registry = _load_project_registry()
    if registry:
        projects = []
        for entry in registry:
            p = Path(entry["path"])
            if p.exists():
                projects.append(p)
        if projects:
            return sorted(projects)

    # Fallback: scan common directories
    home = Path.home()
    search_dirs = [
        home / "coding-projects",
        home / "projects",
        home / "code",
        home / "dev",
        home / "src",
        home / "repos",
        home / "work",
    ]
    projects = []
    for search_dir in search_dirs:
        if not search_dir.exists():
            continue
        for child in search_dir.iterdir():
            if not child.is_dir():
                continue
            devprojects = list(child.glob("*.devproject"))
            if devprojects:
                projects.append(child)
    return sorted(projects)


def validate_and_note_staleness(cwd: str) -> Optional[str]:
    """Run fast file path validation on .devproject post-compaction.

    Fixes moved files, flags missing files, and detects new files
    not yet assigned to any feature. Returns a note for Claude if
    the .devproject needs attention.
    """
    project_root = discover_project_root(Path(cwd).resolve())
    if project_root is None:
        return None

    devproject_path = resolve_devproject_path(project_root)
    if not devproject_path.exists():
        return None

    manager = DevProjectManager(project_root)

    # Fast validation — filesystem only, no LLM
    try:
        result = manager.validate_and_fix_file_paths()
    except Exception:
        result = {}

    fixed = result.get("fixed", [])
    missing = result.get("missing", [])

    # Check for new files not assigned to any feature
    document = manager.load_or_create()
    all_feature_files = set()
    for f in document.get("features", []):
        all_feature_files.update(f.get("files_touched", []))
    for f in document.get("shared_infrastructure", []):
        if isinstance(f, str):
            all_feature_files.add(f)
    for f in document.get("hub_files", []):
        if isinstance(f, str):
            all_feature_files.add(f)

    IGNORED = {".git", "node_modules", "venv", ".venv", "__pycache__", ".next",
               "dist", "build", ".claude", "devsession", ".DS_Store"}
    CODE_EXTS = {".py", ".ts", ".tsx", ".js", ".jsx", ".rs", ".go", ".java",
                 ".rb", ".swift", ".kt", ".c", ".cpp", ".h", ".cs"}

    new_files = []
    for path in project_root.rglob("*"):
        if not path.is_file():
            continue
        if path.suffix not in CODE_EXTS:
            continue
        if any(part in IGNORED for part in path.relative_to(project_root).parts):
            continue
        rel = path.relative_to(project_root).as_posix()
        if rel not in all_feature_files:
            new_files.append(rel)

    if not fixed and not missing and not new_files:
        return None

    notes = ["## .devproject Staleness Check"]
    if fixed:
        notes.append(f"Auto-fixed {len(fixed)} moved file(s):")
        for f in fixed[:5]:
            notes.append(f"  - {f.get('old_path')} → {f.get('new_path')}")
    if missing:
        notes.append(f"{len(missing)} file(s) no longer exist:")
        for m in missing[:5]:
            notes.append(f"  - {m.get('path')} (was in {m.get('feature', '?')})")
    if new_files:
        notes.append(f"{len(new_files)} new code file(s) not in any feature:")
        for nf in new_files[:10]:
            notes.append(f"  - {nf}")
        if len(new_files) > 10:
            notes.append(f"  ... and {len(new_files) - 10} more")
        notes.append("Consider running project_init with force=True to re-scan, or manually assign these files.")

    return "\n".join(notes)


def get_session_start_context(cwd: str) -> Optional[str]:
    """Build context to inject at Claude Code session start.

    If cwd is inside a project with .devproject, auto-loads it.
    If not, lists available registered projects the user can switch to.
    """
    project_root = discover_project_root(Path(cwd).resolve())

    # In a project with .devproject — auto-inject
    if project_root is not None:
        devproject_path = resolve_devproject_path(project_root)
        if devproject_path.exists():
            context = _build_project_context(project_root, "[RecCli] Project context loaded on session start.")
            context += "\n\n" + _session_rules()
            return context

    # Not in a recognized project — list available projects and instruct Claude to ask
    registered = _find_registered_projects()
    if not registered:
        return None

    project_names = []
    project_lines = []
    for proj in registered:
        devprojects = list(proj.glob("*.devproject"))
        name = devprojects[0].stem if devprojects else proj.name
        project_names.append(name)
        project_lines.append(f"- {name} ({proj})")

    names_str = ", ".join(project_names)
    return (
        f"You have RecCli project memory available with {len(registered)} registered projects:\n"
        + "\n".join(project_lines) + "\n\n"
        "Ask the user which project they'd like to work on today. "
        "IMPORTANT: When they choose a project, you MUST immediately call the reccli load_project_context MCP tool "
        "with the project path to activate session recording and load the feature map. Do not skip this step. "
        "If they want to work on a new project, use project_init to scan and initialize it.\n\n"
        + _session_rules()
    )


def _build_project_context(project_root: Path, header: str) -> str:
    """Shared context builder used by both SessionStart and PostCompact."""
    manager = DevProjectManager(project_root)
    document = manager.load_or_create()

    sections = [header, ""]

    project_meta = document.get("project", {})
    name = project_meta.get("name", project_root.name)
    desc = project_meta.get("description", "")
    sections.append(f"# {name}")
    if desc:
        sections.append(desc)
    sections.append("")

    features = document.get("features", [])
    if features:
        sections.append("## Features")
        for f in features:
            status = f.get("status", "unknown")
            title = f.get("title", f.get("feature_id", "?"))
            sections.append(f"- **{title}** [{status}]: {f.get('description', '')[:100]}")
        sections.append("")

    try:
        tree = generate_compact_tree(project_root)
        sections.append("## Codebase Structure")
        sections.append(f"```\n{tree}\n```")
        sections.append("")
    except Exception:
        pass

    try:
        sessions_dir = project_root / "devsession"
        if sessions_dir.exists():
            session_files = sorted(
                list(sessions_dir.glob("*.devsession")),
                key=lambda p: p.stat().st_mtime,
                reverse=True,
            )
            if session_files:
                import json
                with open(session_files[0]) as f:
                    data = json.load(f)
                summary = data.get("summary")
                if summary and summary.get("overview"):
                    sections.append("## Last Session Summary")
                    sections.append(summary["overview"])
                    for cat in ["decisions", "code_changes", "problems_solved", "open_issues"]:
                        items = summary.get(cat, [])
                        if items:
                            sections.append(f"### {cat.replace('_', ' ').title()}")
                            for item in items[:5]:
                                text = (
                                    item.get("decision")
                                    or item.get("description")
                                    or item.get("problem")
                                    or item.get("issue", "")
                                )
                                sections.append(f"- {text[:100]}")
                    sections.append("")
    except Exception:
        pass

    return "\n".join(sections)
