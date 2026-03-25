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


def get_post_compact_context(cwd: str) -> Optional[str]:
    """Build context to inject after Claude Code compaction."""
    project_root = discover_project_root(Path(cwd).resolve())
    if project_root is None:
        return None
    devproject_path = resolve_devproject_path(project_root)
    if not devproject_path.exists():
        return None
    return _build_project_context(project_root, "[RecCli] Project context re-injected after compaction.")


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
            return _build_project_context(project_root, "[RecCli] Project context loaded on session start.")

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
        "Once they choose, call the load_project_context MCP tool with the project path. "
        "If they want to work on a new project, use project_init to scan and initialize it."
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
