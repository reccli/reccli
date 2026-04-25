"""
RecCli MCP Server

Exposes RecCli's temporal memory engine to any MCP-compatible agent
(Claude Code, Cursor, Windsurf, etc.) as callable tools.

Transport: stdio (stdout is the MCP channel — never print() to stdout)
"""

import json
import math
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from mcp.server.fastmcp import FastMCP

mcp = FastMCP("reccli")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _resolve_root(working_directory: str) -> Optional[Path]:
    from .project.devproject import discover_project_root
    return discover_project_root(Path(working_directory).expanduser().resolve())


def _get_embedding_provider():
    """Get an embedding provider with the API key from RecCli config."""
    from .runtime.config import Config
    from .retrieval.embeddings import get_embedding_provider
    config = Config()
    api_key = config.get_api_key("openai")
    return get_embedding_provider({"provider": "openai", "api_key": api_key})



def _sessions_dir(project_root: Path) -> Path:
    d = project_root / "devsession"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _format_features(features: list) -> str:
    if not features:
        return "No features detected."
    lines = []
    for f in features:
        title = f.get("title", "Untitled")
        status = f.get("status", "unknown")
        files = f.get("files_touched", [])
        desc = f.get("description", "")
        lines.append(f"### {title} [{status}]")
        if desc:
            lines.append(desc)
        if files:
            lines.append(f"Files: {', '.join(files[:15])}")
            if len(files) > 15:
                lines.append(f"  ... and {len(files) - 15} more")
        lines.append("")
    return "\n".join(lines)


def _format_search_results(results: list) -> str:
    if not results:
        return "No results found."
    lines = []
    for i, r in enumerate(results, 1):
        content = (r.get("content_preview") or r.get("content") or "")[:200]
        score = r.get("final_score") or r.get("rrf_score") or r.get("cosine_score", 0)
        result_id = r.get("result_id") or r.get("id", "")
        session = r.get("session") or r.get("session_id", "")
        badges = r.get("badges", [])
        badge_str = f" [{', '.join(badges)}]" if badges else ""
        lines.append(f"{i}. [{session}] (score: {score:.3f}){badge_str}")
        lines.append(f"   {content}")
        if result_id:
            lines.append(f"   result_id: {result_id}")
        lines.append("")
    return "\n".join(lines)


def _build_resume_from(sessions_dir: Path) -> Optional[str]:
    """Build a concise 'Resume From' block from the latest session's open issues and next steps."""
    session_files = sorted(sessions_dir.glob("*.devsession"), key=lambda p: p.stat().st_mtime, reverse=True)
    if not session_files:
        return None
    try:
        from .session.devsession import DevSession
        session = DevSession.load(session_files[0], verify_checksums=False)
        if not session.summary:
            return None

        summary = session.summary
        lines = []

        for issue in summary.get("open_issues", [])[:5]:
            text = issue.get("issue") or str(issue)
            lines.append(f"- **Open:** {text}")

        for step in summary.get("next_steps", [])[:5]:
            text = step.get("action") or str(step)
            lines.append(f"- **Next:** {text}")

        return "\n".join(lines) if lines else None
    except Exception:
        return None


def _collect_pinned_items(sessions_dir: Path, limit: int = 10, max_sessions: int = 20) -> List[Dict[str, Any]]:
    """Scan recent sessions for items with pinned=True.

    Returns up to `limit` pinned items, newest-first across the last `max_sessions`
    session files. Each entry carries session, category, id, text, and locked flag.
    """
    from .session.devsession import DevSession
    pinned = []
    session_files = sorted(
        sessions_dir.glob("*.devsession"),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    for sf in session_files[:max_sessions]:
        if sf.name.startswith(".live_"):
            continue
        try:
            s = DevSession.load(sf, verify_checksums=False)
        except Exception:
            continue
        if not s.summary:
            continue
        for cat in ("decisions", "code_changes", "problems_solved", "open_issues", "next_steps"):
            for item in s.summary.get(cat, []):
                if item.get("pinned"):
                    text = (item.get("decision") or item.get("action") or
                            item.get("problem") or item.get("issue") or
                            item.get("description") or "")
                    pinned.append({
                        "session": sf.stem,
                        "category": cat,
                        "id": item.get("id"),
                        "text": text,
                        "locked": bool(item.get("locked")),
                    })
                    if len(pinned) >= limit:
                        return pinned
    return pinned


def _format_file_search_results(results: list, file_path: str) -> str:
    lines = [f"Found {len(results)} messages referencing '{file_path}':\n"]
    for i, r in enumerate(results, 1):
        content = (r.get("content_preview") or "")[:200]
        role = r.get("role", "?")
        session = r.get("session", "")
        ts = r.get("timestamp", "")[:19]
        tool = r.get("tool_name", "")
        tool_str = f" ({tool})" if tool else ""
        result_id = r.get("result_id", "")
        lines.append(f"{i}. [{session}] {ts} [{role}{tool_str}]")
        lines.append(f"   {content}")
        if result_id:
            lines.append(f"   result_id: {result_id}")
        lines.append("")
    return "\n".join(lines)


def _ensure_index(sessions_dir: Path) -> None:
    """Auto-build or incrementally update the unified vector index.

    Called before search so the MCP never returns 'index not found'.
    Skips sessions that are already indexed.
    """
    from .retrieval.vector_index import build_unified_index, update_index_with_new_session

    index_path = sessions_dir / "index.json"
    session_files = sorted(
        list(sessions_dir.glob("*.devsession")) + list(sessions_dir.glob(".live_*.devsession"))
    )

    if not session_files:
        return  # nothing to index

    if not index_path.exists():
        # Full build from scratch
        try:
            build_unified_index(sessions_dir, verbose=False)
        except Exception:
            pass
        return

    # Incremental: check for un-indexed sessions
    try:
        with open(index_path, "r") as f:
            index = json.load(f)
        indexed_sessions = {
            entry["session_id"]
            for entry in index.get("session_manifest", [])
        }
        for sf in session_files:
            session_id = sf.stem
            if session_id not in indexed_sessions:
                try:
                    update_index_with_new_session(sessions_dir, sf, verbose=False)
                except Exception:
                    pass
    except Exception:
        pass


def _latest_session_summary(sessions_dir: Path) -> Optional[str]:
    """Load summary from the most recent .devsession file."""
    session_files = sorted(sessions_dir.glob("*.devsession"), key=lambda p: p.stat().st_mtime, reverse=True)
    if not session_files:
        return None
    try:
        from .session.devsession import DevSession
        session = DevSession.load(session_files[0], verify_checksums=False)
        if not session.summary:
            return None
        summary = session.summary
        parts = []
        if summary.get("overview"):
            parts.append(f"**Last session overview**: {summary['overview']}")
        for category, label in [
            ("decisions", "Decisions"),
            ("problems_solved", "Problems solved"),
            ("open_issues", "Open issues"),
            ("next_steps", "Next steps"),
        ]:
            items = summary.get(category, [])
            if items:
                parts.append(f"\n**{label}**:")
                for item in items[:5]:
                    text = (
                        item.get("decision")
                        or item.get("problem")
                        or item.get("issue")
                        or item.get("action")
                        or str(item)
                    )
                    parts.append(f"- {text}")
        return "\n".join(parts) if parts else None
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Tools
# ---------------------------------------------------------------------------

@mcp.tool()
def load_project_context(working_directory: str) -> str:
    """Load project context for session start. Call this at the beginning of every conversation.

    Returns the project feature map, folder tree, and last session summary
    so the agent has full project understanding without re-explanation.

    Args:
        working_directory: Path to the project or any subdirectory within it.
    """
    from .project.devproject import (
        DevProjectManager,
        generate_compact_tree,
        resolve_devproject_path,
    )

    project_root = _resolve_root(working_directory)
    if project_root is None:
        return (
            "No project root found (no .git or .devproject). "
            "Run `project_init` first to initialize project memory."
        )

    # Reap any dead background tasks from prior sessions
    try:
        from .hooks.session_recorder import cleanup_bg_tasks
        cleanup_bg_tasks(project_root)
    except Exception:
        pass

    # Note: per-session breadcrumb is set by the PostToolUse hook
    # when it detects this tool was called (uses the real session_id)

    manager = DevProjectManager(project_root)

    # Load or create .devproject
    devproject_path = resolve_devproject_path(project_root)
    if not devproject_path.exists():
        return (
            f"No .devproject found at {project_root}. "
            "Run `project_init` to scan the codebase and create one."
        )

    document = manager.load_or_create()

    # Silent file path validation
    try:
        manager.validate_and_fix_file_paths()
        document = manager.load_or_create()
    except Exception:
        pass

    # Build context
    sections = []

    project_meta = document.get("project", {})
    sections.append(f"# {project_meta.get('name', project_root.name)}")
    sections.append(f"{project_meta.get('description', '')}")
    sections.append("")

    # Features
    features = document.get("features", [])
    if features:
        sections.append("## Features")
        sections.append(_format_features(features))

    # Folder tree
    try:
        tree = generate_compact_tree(project_root)
        sections.append("## Codebase Structure")
        sections.append(f"```\n{tree}\n```")
        sections.append("")
    except Exception:
        pass

    # Last session summary — check if it needs retroactive summarization
    sessions_dir = _sessions_dir(project_root)
    last_summary = _latest_session_summary(sessions_dir)
    if last_summary:
        # Resume-from section: surface open issues and next steps prominently
        resume_lines = _build_resume_from(sessions_dir)
        if resume_lines:
            sections.append("## Resume From (last session)")
            sections.append(resume_lines)
            sections.append("")

        # Pinned memory: items the user marked as always-inject (via pin_memory)
        pinned_items = _collect_pinned_items(sessions_dir)
        if pinned_items:
            sections.append("## Pinned Memory")
            for p in pinned_items:
                lock_marker = " [locked]" if p["locked"] else ""
                sections.append(f"- `{p['id']}`{lock_marker} ({p['category']}): {p['text']}")
            sections.append("")

        sections.append("## Last Session")
        sections.append(last_summary)
        sections.append("")

    # Detect unsummarized previous session
    try:
        from .session.devsession import DevSession
        for sf in sorted(sessions_dir.glob("*.devsession"), key=lambda p: p.stat().st_mtime, reverse=True):
            s = DevSession.load(sf)
            overview = (s.summary or {}).get("overview", "")
            is_stub = not overview or overview in ("Session summarized without LLM", "Placeholder summary")
            if is_stub and len(s.conversation) >= 4:
                sections.append("## ACTION REQUIRED: Previous Session Unsummarized")
                sections.append(
                    f"The previous session ({sf.name}, {len(s.conversation)} messages) has no structured summary. "
                    "Please read the session conversation using expand_search_result or by reading the file directly, "
                    "analyze the key decisions, code changes, and problems solved, then call "
                    "summarize_previous_session with your analysis. This links the summary to the full conversation."
                )
                sections.append("")
            break  # Only check the most recent
    except Exception:
        pass

    # Pending proposals
    proposals = [p for p in document.get("proposals", []) if p.get("status") == "pending"]
    if proposals:
        sections.append(f"## Pending Proposals ({len(proposals)})")
        for p in proposals[:3]:
            ops = [op.get("op", "?") for op in p.get("diff", [])]
            sections.append(f"- {p['proposal_id']}: {', '.join(ops)}")
        sections.append("")

    return "\n".join(sections)


@mcp.tool()
def project_init(
    working_directory: str,
    description: str = "",
    force: bool = False,
) -> str:
    """Initialize project memory from codebase scan.

    Scans the codebase with Tree-sitter, clusters files into features,
    and creates a .devproject file.

    If an Anthropic API key is available, clustering runs automatically via LLM.
    If not, returns the scan results and clustering prompt for you (Claude) to
    process in-conversation. Call project_apply_clustering with your JSON result.

    Args:
        working_directory: Path to the project root.
        description: Optional 1-2 sentence project description to guide feature clustering.
        force: Overwrite existing .devproject if True.
    """
    from .project.devproject import DevProjectManager, discover_project_root

    root = Path(working_directory).expanduser().resolve()
    project_root = discover_project_root(root) or root

    manager = DevProjectManager(project_root)
    project_context = description.strip() or manager.suggest_init_project_context()

    # Try the LLM path first
    try:
        document = manager.initialize_from_codebase(
            force=force,
            project_context=project_context,
        )
    except ValueError as e:
        return f"Cannot initialize: {e}. Use force=True to overwrite."
    except RuntimeError as llm_error:
        # LLM not available — fall back to scan + prompt for Claude
        return _project_scan_for_claude(manager, project_context, force, str(llm_error))

    return _format_init_result(manager, document)


@mcp.tool()
def project_apply_clustering(
    working_directory: str,
    clustering_json: str,
    force: bool = False,
) -> str:
    """Apply feature clustering results from your in-conversation analysis.

    Call this after project_init returns a scan+prompt (when no LLM API key
    is configured). Pass your clustering JSON as the clustering_json argument.

    Args:
        working_directory: Path to the project root.
        clustering_json: JSON string with your clustering result (project, features, hub_files, etc.).
        force: Overwrite existing .devproject if True.
    """
    from .project.devproject import DevProjectManager, discover_project_root

    root = Path(working_directory).expanduser().resolve()
    project_root = discover_project_root(root) or root

    manager = DevProjectManager(project_root)

    if manager.path.exists() and not force:
        return f"Cannot apply: .devproject already exists. Use force=True to overwrite."

    try:
        clustering = json.loads(clustering_json)
    except json.JSONDecodeError as e:
        return f"Invalid JSON: {e}"

    # Build the document using the non-LLM path for structure, then overlay clustering
    inventory = manager._build_codebase_inventory()
    normalized = manager._normalize_llm_cluster_output(clustering, inventory)
    normalized["features"] = manager._refine_features_with_artifact_candidates(
        normalized.get("features", []),
        inventory,
    )

    from .project.devproject import create_devproject
    document = create_devproject(project_root)
    document["project"] = manager._scan_project_metadata(document["project"])
    document["project"]["source"] = "auto"
    if clustering.get("project", {}).get("description"):
        document["project"]["description"] = clustering["project"]["description"]
    if clustering.get("project", {}).get("name"):
        document["project"]["name"] = clustering["project"]["name"]
    document["features"] = normalized["features"]
    document["hub_files"] = normalized.get("hub_files", [])
    document["shared_infrastructure"] = normalized.get("shared_infrastructure", [])
    document["unassigned"] = normalized.get("unassigned", [])

    manager._link_documents_to_document(document, inventory, use_embeddings=False)
    manager.save(document)

    return _format_init_result(manager, document)


def _project_scan_for_claude(manager, project_context, force, error_msg) -> str:
    """Run the scan, return prompt + inventory for Claude to cluster."""
    inventory = manager._build_codebase_inventory()
    truncated = manager._truncate_inventory_for_llm(inventory)
    readme_content = manager._read_readme_for_clustering()

    llm_input = json.dumps({
        "readme_content": readme_content or "",
        "project_context": project_context or "",
        "inventory": truncated,
    }, indent=2, ensure_ascii=False)

    # Truncate if huge
    if len(llm_input) > 80000:
        llm_input = json.dumps({
            "readme_content": readme_content or "",
            "project_context": project_context or "",
            "inventory": manager._truncate_inventory_for_llm(inventory, aggressive=True),
        }, ensure_ascii=False)

    return (
        f"No LLM API available ({error_msg}). Codebase scanned successfully.\n\n"
        f"Please cluster these files into features and call `project_apply_clustering` "
        f"with your JSON result.\n\n"
        f"## Clustering Instructions\n\n"
        f"Identify canonical project features. A feature is a stable, durable work area.\n"
        f"Optimize for: stable feature identities, file ownership boundaries, "
        f"session-to-feature matching by file overlap.\n\n"
        f"Return JSON with: project (name, description), features (title, description, "
        f"files, suggested_file_boundaries), hub_files, shared_infrastructure, unassigned.\n\n"
        f"## Scanned Inventory\n\n```json\n{llm_input}\n```"
    )


def _format_init_result(manager, document) -> str:
    # Register project in global registry for SessionStart discovery
    try:
        from .hooks.context_injector import register_project
        name = document.get("project", {}).get("name", manager.project_root.name)
        register_project(manager.project_root, name)
    except Exception:
        pass

    features = document.get("features", [])
    lines = [
        f"Initialized .devproject at {manager.path}",
        f"Project: {document['project'].get('name', manager.project_root.name)}",
        f"Features detected: {len(features)}",
        "",
    ]
    for f in features[:15]:
        files_count = len(f.get("files_touched", []))
        lines.append(f"- {f.get('feature_id')}: {f.get('title')} ({files_count} files)")
    if len(features) > 15:
        lines.append(f"... and {len(features) - 15} more")
    return "\n".join(lines)


@mcp.tool()
def search_history(
    query: str,
    working_directory: str,
    top_k: int = 5,
    file_path: str = "",
) -> str:
    """Search past session history for decisions, code changes, and problems solved.

    Uses hybrid retrieval (dense embeddings + BM25 + reciprocal rank fusion)
    across all .devsession files in the project.

    Args:
        query: Natural language search query (e.g. "what did we decide about auth?").
        working_directory: Path to the project.
        top_k: Number of results to return (default 5).
        file_path: Optional file path filter — only return results referencing this file.
    """
    from .retrieval.search import search
    from .runtime.config import Config

    project_root = _resolve_root(working_directory)
    if project_root is None:
        return "No project root found."

    sessions_dir = _sessions_dir(project_root)

    # Flush any active hook WALs so current-session messages are searchable
    try:
        from .hooks.session_recorder import flush_active_wals
        flush_active_wals(project_root)
    except Exception:
        pass

    # Auto-build or update index if missing or stale
    _ensure_index(sessions_dir)

    try:
        provider = _get_embedding_provider()

        # Use expanded search (synonym-expanded multi-query) if enabled
        config = Config()
        if config.data.get("expanded_search", False):
            from .retrieval.search import search_expanded
            results = search_expanded(
                sessions_dir=sessions_dir,
                query=query,
                top_k=top_k,
                provider=provider,
                file_path=file_path or None,
            )
        else:
            results = search(
                sessions_dir=sessions_dir,
                query=query,
                top_k=top_k,
                provider=provider,
                file_path=file_path or None,
            )
    except Exception as e:
        return f"Search failed: {e}"

    if not results:
        return "No results found. The project may not have any session history yet."

    return _format_search_results(results)


@mcp.tool()
def configure(
    setting: str = "",
    value: bool | None = None,
) -> str:
    """View or change RecCli configuration.

    Call with no args to see all current settings.
    Call with setting + value to change a specific setting.

    Available settings:
      - auto_reason: Inject reasoning scaffold for debug/planning prompts (default: off)
      - mmc: Parallel multi-agent reasoning — supersedes auto_reason (default: off)
      - session_signal: Track resolved/open items via hidden tags (default: on)
      - expanded_search: Synonym query expansion for broader search recall (default: off)

    Args:
        setting: Setting name to change (empty = show all).
        value: New value (True/False). Required when setting is provided.
    """
    from .runtime.config import Config
    config = Config()

    known_settings = ["auto_reason", "mmc", "session_signal", "expanded_search"]
    defaults = {"auto_reason": False, "mmc": False, "session_signal": True, "expanded_search": False}

    if not setting:
        # Show all settings
        lines = ["**RecCli Configuration:**\n"]
        for s in known_settings:
            current = config.data.get(s, defaults.get(s, False))
            default = defaults.get(s, False)
            marker = "" if current == default else " (changed)"
            lines.append(f"  {s}: {'on' if current else 'off'}{marker}")
        return "\n".join(lines)

    if setting not in known_settings:
        return f"Unknown setting '{setting}'. Available: {', '.join(known_settings)}"

    if value is None:
        current = config.data.get(setting, defaults.get(setting, False))
        return f"{setting}: {'on' if current else 'off'}"

    config.data[setting] = value
    config.save()
    return f"{setting}: {'on' if value else 'off'}"


@mcp.tool()
def toggle_auto_reason(enabled: bool) -> str:
    """Enable or disable auto-reason scaffold injection.

    When enabled, RecCli detects debug/planning intent from your prompts
    and injects a reasoning scaffold to guide systematic thinking through a
    diverge-converge-validate pattern. This is a standalone mode — for
    parallel agent comparison, use toggle_mmc instead.

    Args:
        enabled: True to enable, False to disable.
    """
    from .runtime.config import Config
    config = Config()
    config.data["auto_reason"] = enabled
    config.save()
    return f"Auto-reason {'enabled' if enabled else 'disabled'}."


@mcp.tool()
def toggle_mmc(enabled: bool) -> str:
    """Enable or disable MMC (Multiple Model Comparison) parallel reasoning.

    When enabled, debug and planning prompts trigger parallel agent execution:
    3 agents each independently run the full diverge-converge reasoning scaffold
    with a different analytical lens (e.g., recent changes vs data flow vs assumptions).
    Their conclusions are then compared to extract high-confidence consensus.

    MMC supersedes auto-reason when enabled — it includes the reasoning scaffold
    within each parallel agent. Disable MMC to fall back to single-agent auto-reason.

    Args:
        enabled: True to enable, False to disable.
    """
    from .runtime.config import Config
    config = Config()
    config.data["mmc"] = enabled
    config.save()
    return f"MMC parallel reasoning {'enabled' if enabled else 'disabled'}."


@mcp.tool()
def toggle_session_signal(enabled: bool) -> str:
    """Enable or disable session-signal forward pointers.

    When enabled, a SESSION RULE asks the agent to append a hidden tag to each
    response tracking what was resolved and what remains open. The Stop hook
    extracts and strips the tag, storing the parsed signal in the WAL.

    Args:
        enabled: True to enable, False to disable.
    """
    from .runtime.config import Config
    config = Config()
    config.data["session_signal"] = enabled
    config.save()
    return f"Session-signal {'enabled' if enabled else 'disabled'}."


@mcp.tool()
def toggle_expanded_search(enabled: bool) -> str:
    """Enable or disable expanded search with synonym query expansion.

    When enabled, search queries are expanded with synonyms for broader recall.
    For example, searching "auth middleware" also searches "authentication layer"
    and "login handler".

    Args:
        enabled: True to enable, False to disable.
    """
    from .runtime.config import Config
    config = Config()
    config.data["expanded_search"] = enabled
    config.save()
    return f"Expanded search {'enabled' if enabled else 'disabled'}."


@mcp.tool()
def search_by_file(
    file_path: str,
    working_directory: str,
    top_k: int = 20,
) -> str:
    """Find all conversation history that references a specific file.

    Use this to answer "what did we do to X file?" — returns all messages
    across sessions that mention the file path or filename.

    Args:
        file_path: File path to search for (full path or just filename).
        working_directory: Path to the project.
        top_k: Number of results to return (default 20).
    """
    from .retrieval.search import search_by_file as _search_by_file

    project_root = _resolve_root(working_directory)
    if project_root is None:
        return "No project root found."

    sessions_dir = _sessions_dir(project_root)

    # Flush active WALs so current-session messages are searchable
    try:
        from .hooks.session_recorder import flush_active_wals
        flush_active_wals(project_root)
    except Exception:
        pass

    results = _search_by_file(sessions_dir, file_path, top_k=top_k)
    if not results:
        return f"No messages found referencing '{file_path}'."

    return _format_file_search_results(results, file_path)


@mcp.tool()
def search_by_time(
    start_time: str,
    working_directory: str,
    end_time: str = "",
    query: str = "",
    top_k: int = 20,
) -> str:
    """Search session history within a time range.

    Use this for questions like "what happened on March 29?" or
    "what did we work on last Tuesday?"

    Args:
        start_time: Start of range — ISO date or datetime (e.g. "2026-03-29" or "2026-03-29T10:00:00").
        working_directory: Path to the project.
        end_time: End of range — ISO date or datetime. Defaults to end of start_time's day.
        query: Optional text filter to narrow results within the time range.
        top_k: Number of results to return (default 20).
    """
    from .retrieval.search import search_by_time_range

    project_root = _resolve_root(working_directory)
    if project_root is None:
        return "No project root found."

    sessions_dir = _sessions_dir(project_root)

    # Flush active WALs
    try:
        from .hooks.session_recorder import flush_active_wals
        flush_active_wals(project_root)
    except Exception:
        pass

    # Default end_time to end of start day
    if not end_time:
        end_time = start_time[:10] if len(start_time) >= 10 else start_time

    results = search_by_time_range(
        sessions_dir,
        start_time=start_time,
        end_time=end_time,
        query=query or None,
        top_k=top_k,
    )

    if not results:
        return f"No messages found in range {start_time} to {end_time}."

    return _format_search_results(results)


def _reconstruct_file_from_raw_response(raw_response) -> Optional[str]:
    """Reconstruct a file's content from an Edit/Write raw_response payload.

    Honors `replaceAll` / `replace_all` — with replace_all, all occurrences of
    oldString are substituted; without it, only the first match is. This
    matches Claude Code's Edit tool contract: replace_all is required when
    oldString appears more than once in the file.
    """
    try:
        resp = json.loads(raw_response) if isinstance(raw_response, str) else raw_response
    except (json.JSONDecodeError, TypeError):
        return None
    if not isinstance(resp, dict):
        return None

    # Write tool: content field is authoritative
    if resp.get("content") and "oldString" not in resp and "originalFile" not in resp:
        return resp["content"]

    original = resp.get("originalFile")
    old_str = resp.get("oldString")
    new_str = resp.get("newString")
    replace_all = bool(resp.get("replaceAll") or resp.get("replace_all"))

    if original and old_str is not None and new_str is not None:
        if replace_all:
            return original.replace(old_str, new_str)
        return original.replace(old_str, new_str, 1)
    if original:
        return original
    return None


@mcp.tool()
def recover_file(
    file_path: str,
    working_directory: str,
    version: int = 0,
    list_only: bool = False,
) -> str:
    """Recover a file's contents from session history.

    Searches artifact sidecars across all sessions for snapshots of the
    given file. By default returns the most recent version; pass version=1
    for the previous, version=2 for two back, etc. Use this when a file
    was lost, overwritten, or needs to be restored to a previous state.

    Args:
        file_path: File path to recover (full path or just filename).
        working_directory: Path to the project.
        version: Which version to return. 0 = most recent (default), 1 = previous, etc.
        list_only: If True, list all available versions without returning content.
    """
    project_root = _resolve_root(working_directory)
    if project_root is None:
        return "No project root found."

    sessions_dir = _sessions_dir(project_root)
    basename = file_path.rsplit("/", 1)[-1] if "/" in file_path else file_path

    # Scan artifact sidecars newest-first
    artifact_files = sorted(
        sessions_dir.glob(".artifacts_*.json"),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )

    if not artifact_files:
        return f"No file artifacts found. Artifact extraction is only available for sessions recorded after this feature was added."

    matches = []
    for af in artifact_files:
        try:
            data = json.loads(af.read_text(encoding="utf-8"))
            for art in data.get("artifacts", []):
                art_path = art.get("file_path", "")
                if file_path in art_path or basename in art_path:
                    content = art.get("file_content")
                    # Reconstruct from raw_response if file_content is missing
                    if not content and art.get("raw_response"):
                        content = _reconstruct_file_from_raw_response(art["raw_response"])
                    matches.append({
                        "file_path": art_path,
                        "timestamp": art.get("timestamp", ""),
                        "tool": art.get("tool", ""),
                        "session": af.stem.replace(".artifacts_", ""),
                        "has_content": bool(content),
                        "content": content,
                        "raw_response": art.get("raw_response"),
                    })
        except Exception:
            continue

    if not matches:
        return f"No snapshots found for '{file_path}'. The file may not have been edited in any recorded session."

    # Build list of versions (content-bearing snapshots, newest-first)
    versioned = [m for m in matches if m["has_content"]]

    lines = [f"Found {len(matches)} snapshot(s) for '{file_path}' ({len(versioned)} with content):\n"]
    for i, m in enumerate(matches, 0):
        marker = f"v{versioned.index(m)}" if m in versioned else "no-content"
        lines.append(f"- [{marker}] [{m['session']}] {m['timestamp']} via {m['tool']}")
        if m["has_content"]:
            lines.append(f"    {len(m['content']):,} chars")

    if list_only:
        return "\n".join(lines)

    if not versioned:
        if matches[0].get("raw_response"):
            lines.append(f"\n--- Raw response (may need parsing) ---")
            lines.append(matches[0]["raw_response"][:10000])
        return "\n".join(lines)

    if version < 0 or version >= len(versioned):
        return (
            f"Version {version} out of range (0..{len(versioned) - 1} available).\n"
            + "\n".join(lines)
        )

    chosen = versioned[version]
    lines.append(f"\n--- Version {version} content ({chosen['timestamp']}) ---")
    lines.append(chosen["content"])

    return "\n".join(lines)


@mcp.tool()
def list_sessions(
    working_directory: str,
    limit: int = 20,
    query: str = "",
    since: str = "",
    has_summary: Optional[bool] = None,
) -> str:
    """Browse all recorded sessions for this project.

    Shows sessions sorted by date (newest first) with message counts,
    summary status, and overview snippets. Use this to see what sessions
    exist before searching or drilling into one.

    Args:
        working_directory: Path to the project or any subdirectory within it.
        limit: Maximum number of sessions to show (default 20).
        query: Optional substring filter — matches against session stem and overview.
        since: Optional ISO date (e.g. "2026-03-29") — only sessions started on or after this date.
        has_summary: Optional filter — True for summarized sessions only, False for unsummarized.
    """
    from .session.devsession import DevSession

    project_root = _resolve_root(working_directory)
    if project_root is None:
        return "No project root found."

    sessions_dir = _sessions_dir(project_root)
    session_files = sorted(
        sessions_dir.glob("*.devsession"),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )

    # Exclude live snapshots
    session_files = [sf for sf in session_files if not sf.name.startswith(".live_")]

    if not session_files:
        return "No recorded sessions found."

    query_lower = query.lower() if query else None
    since_prefix = since[:10] if since else None

    matched = []
    for sf in session_files:
        try:
            s = DevSession.load(sf, verify_checksums=False)
        except Exception:
            matched.append((sf, None))
            continue
        msg_count = len(s.conversation)
        overview_text = (s.summary or {}).get("overview", "") if s.summary else ""
        is_summarized = bool(overview_text.strip())
        first_ts = s.conversation[0].get("timestamp", "") if s.conversation else ""

        if has_summary is not None and has_summary != is_summarized:
            continue
        if since_prefix and (not first_ts or first_ts[:10] < since_prefix):
            continue
        if query_lower:
            haystack = f"{sf.stem.lower()} {overview_text.lower()}"
            if query_lower not in haystack:
                continue

        matched.append((sf, {"msg_count": msg_count, "is_summarized": is_summarized,
                             "overview": overview_text, "first_ts": first_ts}))
        if len(matched) >= limit:
            break

    if not matched:
        return f"No sessions match the filters (total={len(session_files)})."

    filter_desc = []
    if query: filter_desc.append(f"query='{query}'")
    if since: filter_desc.append(f"since={since}")
    if has_summary is not None: filter_desc.append(f"has_summary={has_summary}")
    filter_str = f" [{', '.join(filter_desc)}]" if filter_desc else ""

    lines = [f"**{len(matched)} session(s)** (of {len(session_files)} total{filter_str}):\n"]

    for sf, info in matched:
        if info is None:
            lines.append(f"- **{sf.stem}** — (failed to load)")
            continue
        status = "summarized" if info["is_summarized"] else "unsummarized"
        lines.append(f"- **{sf.stem}** — {info['msg_count']} msgs, {status}")
        if info["first_ts"]:
            lines.append(f"  Started: {info['first_ts'][:16]}")
        if info["overview"]:
            ov = info["overview"][:120] + ("..." if len(info["overview"]) > 120 else "")
            lines.append(f"  {ov}")

    return "\n".join(lines)


@mcp.tool()
def expand_search_result(
    result_id: str,
    working_directory: str,
    context_window: int = 5,
) -> str:
    """Expand a search result to show full conversation context around it.

    Use this after search_history to drill into a specific result
    and see the surrounding messages.

    Args:
        result_id: The result_id from a search_history result.
        working_directory: Path to the project.
        context_window: Number of messages before/after to include (default 5).
    """
    from .retrieval.search import expand_result

    project_root = _resolve_root(working_directory)
    if project_root is None:
        return "No project root found."

    sessions_dir = _sessions_dir(project_root)
    result = expand_result(sessions_dir, result_id, context_window)

    if result is None:
        # Distinguish between session not found and message not found
        parts = result_id.rsplit("_msg_", 1)
        session_stem = parts[0] if len(parts) == 2 else result_id
        session_exists = any(
            sf.stem == session_stem or sf.stem.startswith(f".live_{session_stem}")
            for sf in sessions_dir.glob("*.devsession")
        )
        if not session_exists:
            return f"Session '{session_stem}' not found. It may have been compacted or the ID is invalid."
        return f"Message index out of range in session '{session_stem}'. The session exists but the message was not found."

    lines = []
    hit_type = result.get("hit_type", "message")
    references = set(result.get("references", []))

    # --- Summary item hit: show the item, linked spans, then full conversation slice ---
    if hit_type == "summary_item":
        item = result.get("summary_item", {})
        lines.append(f"**Summary item** `{item.get('id', '?')}`:")
        # Show the item fields (excluding internal linking fields for readability)
        display_item = {k: v for k, v in item.items()
                        if k not in ("span_ids", "references", "message_range", "t_first", "t_last")}
        lines.append(json.dumps(display_item, indent=2, ensure_ascii=False))
        lines.append("")

        linked_spans = result.get("linked_spans", [])
        if linked_spans:
            lines.append(f"**Linked spans** ({len(linked_spans)}):")
            for span in linked_spans:
                lines.append(f"  - `{span.get('id')}` [{span.get('kind')}]: {span.get('topic', '')}")
            lines.append("")

        lines.append(f"**Source conversation** (messages {result['context_start']}–{result['context_end']}):")

    # --- Span hit: show the span, then its conversation region ---
    elif hit_type == "span":
        span = result.get("span", {})
        lines.append(f"**Span** `{span.get('id', '?')}` [{span.get('kind', '?')}]: {span.get('topic', '')}")
        lines.append(f"  Messages {result['context_start']}–{result['context_end']}")
        lines.append("")

    # --- Message hit: just show conversation context ---
    else:
        lines.append(f"**Conversation context** (around message {result.get('message_index', '?')}):")

    context_messages = result.get("context_messages", [])
    if context_messages:
        for msg in context_messages:
            role = msg.get("role", "?")
            msg_id = msg.get("id", "")
            content = (msg.get("content") or "")[:500]
            tool_resp = msg.get("tool_response")
            # Mark key evidence messages from summary references
            ref_marker = " ⬅ [key evidence]" if msg_id in references else ""
            lines.append(f"[{role}]{ref_marker}: {content}")
            if tool_resp:
                lines.append(f"  [full tool response]: {tool_resp[:2000]}")
        lines.append("")

    return "\n".join(lines) if lines else "No context available."


@mcp.tool()
def save_session_notes(
    working_directory: str,
    overview: str = "",
    decisions: list[str] | None = None,
    problems_solved: list[str] | None = None,
    open_issues: list[str] | None = None,
    next_steps: list[str] | None = None,
    files_changed: list[str] | None = None,
) -> str:
    """Save key outcomes from this session to project memory.

    Call this before ending a session where significant work was done.
    The notes are persisted as a .devsession file and a .devproject
    update is proposed from the evidence.

    Args:
        working_directory: Path to the project.
        overview: 1-2 sentence summary of what was accomplished.
        decisions: List of key technical decisions made.
        problems_solved: List of problems that were solved.
        open_issues: List of issues that remain open.
        next_steps: List of planned next actions.
        files_changed: List of file paths that were modified.
    """
    from .session.devsession import DevSession
    from .project.devproject import (
        DevProjectManager,
        default_devsession_path,
        resolve_session_project_root,
    )

    project_root = _resolve_root(working_directory)
    if project_root is None:
        return "No project root found."

    # Build a minimal conversation from the structured notes
    conversation = []
    timestamp = datetime.now().isoformat()

    if overview:
        conversation.append({
            "role": "system",
            "content": f"Session overview: {overview}",
            "timestamp": timestamp,
        })

    for decision in (decisions or []):
        conversation.append({
            "role": "assistant",
            "content": f"Decision: {decision}",
            "timestamp": timestamp,
        })

    for problem in (problems_solved or []):
        conversation.append({
            "role": "assistant",
            "content": f"Problem solved: {problem}",
            "timestamp": timestamp,
        })

    for issue in (open_issues or []):
        conversation.append({
            "role": "assistant",
            "content": f"Open issue: {issue}",
            "timestamp": timestamp,
        })

    for step in (next_steps or []):
        conversation.append({
            "role": "assistant",
            "content": f"Next step: {step}",
            "timestamp": timestamp,
        })

    for file_path in (files_changed or []):
        conversation.append({
            "role": "tool",
            "content": f"Updated file: {file_path}",
            "timestamp": timestamp,
        })

    if not conversation:
        return "Nothing to save. Provide at least one of: overview, decisions, problems_solved, open_issues, next_steps, or files_changed."

    # Try to get the real conversation from the active WAL
    real_conversation = None
    try:
        from .hooks.session_recorder import _find_project_root, _devsession_dir
        sessions_dir = _devsession_dir(project_root)
        for wal in sorted(sessions_dir.glob(".hooks_wal_*.jsonl"), key=lambda p: p.stat().st_mtime, reverse=True):
            lines = wal.read_text(encoding="utf-8").strip().split("\n")
            if len(lines) < 2:
                continue
            records = []
            for line in lines[1:]:
                if line.strip():
                    try:
                        records.append(json.loads(line))
                    except json.JSONDecodeError:
                        continue
            if records:
                real_conversation = []
                for rec in records:
                    msg = {
                        "role": rec.get("role", "system"),
                        "content": rec.get("content", ""),
                        "timestamp": rec.get("timestamp", ""),
                    }
                    if rec.get("tool_name"):
                        msg["tool_name"] = rec["tool_name"]
                    real_conversation.append(msg)
                break  # Use the most recent WAL
    except Exception:
        pass

    # Create and save .devsession
    session = DevSession()
    # Use real conversation from WAL if available, otherwise synthetic
    session.conversation = real_conversation or conversation
    session.metadata["project_root"] = str(project_root)
    session.metadata["working_directory"] = str(project_root)
    session.metadata["source"] = "mcp_hooks" if real_conversation else "mcp_agent_reported"

    # Build summary from structured input, with BM25-matched message ranges
    conv_len = len(session.conversation)

    def _bm25_message_range(query_text: str, conversation: list, k1: float = 1.5, b: float = 0.75, threshold_ratio: float = 0.3) -> dict:
        """Compute a tight message range by BM25-scoring query_text against conversation messages.

        Finds messages most relevant to the summary item text, then returns
        the contiguous range covering the top-scoring cluster.
        Falls back to full-session range if no messages score well.
        """
        if not conversation or not query_text or not query_text.strip():
            return {"start": "msg_001", "end": f"msg_{len(conversation):03d}",
                    "start_index": 0, "end_index": len(conversation)}

        query_terms = query_text.lower().split()
        if not query_terms:
            return {"start": "msg_001", "end": f"msg_{len(conversation):03d}",
                    "start_index": 0, "end_index": len(conversation)}

        # Tokenize each message
        doc_tokens = []
        for msg in conversation:
            content = (msg.get("content") or "").lower()
            tool_resp = (msg.get("tool_response") or "")[:2000].lower()
            text = f"{content} {tool_resp}" if tool_resp else content
            doc_tokens.append(text.split())

        n_docs = len(doc_tokens)
        doc_lengths = [len(t) for t in doc_tokens]
        avg_dl = sum(doc_lengths) / n_docs if n_docs else 1

        # Document frequencies
        df = {}
        for tokens in doc_tokens:
            for term in set(tokens):
                df[term] = df.get(term, 0) + 1

        # Score each message
        scores = []
        for idx, tokens in enumerate(doc_tokens):
            score = 0.0
            dl = doc_lengths[idx]
            for term in query_terms:
                if term not in df:
                    continue
                tf = tokens.count(term)
                idf = math.log((n_docs - df[term] + 0.5) / (df[term] + 0.5) + 1.0)
                numerator = tf * (k1 + 1)
                denominator = tf + k1 * (1 - b + b * (dl / avg_dl))
                score += idf * (numerator / denominator)
            scores.append(score)

        max_score = max(scores) if scores else 0
        if max_score <= 0:
            return {"start": "msg_001", "end": f"msg_{n_docs:03d}",
                    "start_index": 0, "end_index": n_docs}

        # Collect messages scoring above threshold
        threshold = max_score * threshold_ratio
        matching_indices = [i for i, s in enumerate(scores) if s >= threshold]

        if not matching_indices:
            return {"start": "msg_001", "end": f"msg_{n_docs:03d}",
                    "start_index": 0, "end_index": n_docs}

        # Split matches into clusters separated by gaps > max_gap messages
        max_gap = 8
        clusters = []
        current_cluster = [matching_indices[0]]
        for i in range(1, len(matching_indices)):
            if matching_indices[i] - matching_indices[i - 1] > max_gap:
                clusters.append(current_cluster)
                current_cluster = [matching_indices[i]]
            else:
                current_cluster.append(matching_indices[i])
        clusters.append(current_cluster)

        # Pick the cluster with the highest aggregate BM25 score
        best_cluster = max(clusters, key=lambda c: sum(scores[i] for i in c))

        start_idx = best_cluster[0]
        end_idx = best_cluster[-1] + 1  # exclusive

        # Collect reference message IDs (top scorers within the best cluster)
        # Message IDs are 1-based (msg_001 = first message at index 0)
        top_k = sorted(best_cluster, key=lambda i: scores[i], reverse=True)[:5]
        references = [f"msg_{i+1:03d}" for i in top_k]

        return {
            "start": f"msg_{start_idx+1:03d}",
            "end": f"msg_{end_idx:03d}",
            "start_index": start_idx,
            "end_index": end_idx,
            "_references": references,
        }

    conv = session.conversation

    # Pre-compute ranges for each summary item
    decision_ranges = [_bm25_message_range(d, conv) for d in (decisions or [])]
    change_ranges = [_bm25_message_range(f, conv) for f in (files_changed or [])]
    problem_ranges = [_bm25_message_range(p, conv) for p in (problems_solved or [])]
    issue_ranges = [_bm25_message_range(issue, conv) for issue in (open_issues or [])]
    step_ranges = [_bm25_message_range(step, conv) for step in (next_steps or [])]

    session.summary = {
        "schema_version": "1.1",
        "model": "agent_reported",
        "created_at": timestamp,
        "overview": overview or "Agent-reported session notes.",
        "decisions": [
            {"id": f"dec_{i:03d}", "decision": d, "reasoning": "", "impact": "medium",
             "span_ids": [], "references": decision_ranges[i].pop("_references", []),
             "message_range": decision_ranges[i],
             "confidence": "medium", "pinned": False, "locked": False}
            for i, d in enumerate(decisions or [])
        ],
        "code_changes": [
            {"id": f"chg_{i:03d}", "files": [f], "description": f"Modified {f}", "type": "feature",
             "lines_added": None, "lines_removed": None, "source_of_truth": "agent_reported",
             "span_ids": [], "references": change_ranges[i].pop("_references", []),
             "message_range": change_ranges[i],
             "confidence": "medium", "pinned": False, "locked": False}
            for i, f in enumerate(files_changed or [])
        ],
        "problems_solved": [
            {"id": f"prb_{i:03d}", "problem": p, "solution": "",
             "span_ids": [], "references": problem_ranges[i].pop("_references", []),
             "message_range": problem_ranges[i],
             "confidence": "medium", "pinned": False, "locked": False}
            for i, p in enumerate(problems_solved or [])
        ],
        "open_issues": [
            {"id": f"iss_{i:03d}", "issue": issue, "severity": "medium",
             "span_ids": [], "references": issue_ranges[i].pop("_references", []),
             "message_range": issue_ranges[i],
             "confidence": "medium", "pinned": False, "locked": False}
            for i, issue in enumerate(open_issues or [])
        ],
        "next_steps": [
            {"id": f"nxt_{i:03d}", "action": step, "priority": i + 1,
             "span_ids": [], "references": step_ranges[i].pop("_references", []),
             "message_range": step_ranges[i],
             "confidence": "medium", "pinned": False, "locked": False}
            for i, step in enumerate(next_steps or [])
        ],
        "causal_edges": [],
        "audit_trail": [],
    }

    # Generate spans linking summary items to conversation
    try:
        from .summarization.summary_schema import ensure_summary_span_links
        session.spans = ensure_summary_span_links(session.summary, session.spans)
    except Exception:
        pass

    sessions_dir = _sessions_dir(project_root)
    output_path = default_devsession_path(project_root)
    session.save(output_path, skip_validation=True)

    # Background: generate embeddings (messages + spans + summary items)
    try:
        from .hooks.session_recorder import register_bg_task
        # Don't re-summarize (we already have the summary), just embed + index
        import subprocess, sys
        script = (
            "import sys\n"
            "from pathlib import Path\n"
            "path = Path(sys.argv[1])\n"
            "from reccli.session.devsession import DevSession\n"
            "s = DevSession.load(path)\n"
            "s.generate_embeddings(storage_mode='external')\n"
            "for span in s.spans:\n"
            "    span.pop('embedding', None)\n"
            "if s.summary:\n"
            "    for cat in ['decisions','code_changes','problems_solved','open_issues','next_steps']:\n"
            "        for item in s.summary.get(cat, []):\n"
            "            item.pop('embedding', None)\n"
            "s.save(path)\n"
            "from reccli.retrieval.vector_index import update_index_with_new_session\n"
            "update_index_with_new_session(path.parent, path, verbose=False)\n"
        )
        proc = subprocess.Popen(
            [sys.executable, "-c", script, str(output_path)],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
        register_bg_task(project_root, proc.pid, "save_session_notes.embed")
    except Exception:
        pass

    # Also update index immediately for BM25 (embeddings come later from background)
    try:
        from .retrieval.vector_index import update_index_with_new_session
        update_index_with_new_session(sessions_dir, output_path, verbose=False)
    except Exception:
        pass  # index update is best-effort; search will auto-build later

    item_counts = []
    if decisions:
        item_counts.append(f"{len(decisions)} decisions")
    if problems_solved:
        item_counts.append(f"{len(problems_solved)} problems")
    if files_changed:
        item_counts.append(f"{len(files_changed)} file changes")
    if open_issues:
        item_counts.append(f"{len(open_issues)} open issues")
    if next_steps:
        item_counts.append(f"{len(next_steps)} next steps")

    base = (
        f"Session saved: {output_path.name}\n"
        f"Contents: {', '.join(item_counts) if item_counts else 'overview only'}"
    )

    # Propose .devproject update. If the session has rich semantic content,
    # delegate grouping to the agent in-conversation (no extra API call) and
    # ask it to call propose_feature_grouping with domain-grounded JSON.
    # Otherwise fall through to the heuristic proposal path.
    try:
        manager = DevProjectManager(project_root)
        if manager.session_has_semantic_content(session.summary):
            prompt = manager.build_grouping_prompt(session, output_path)
            return f"{base}\n\n{prompt}"
        _, proposal = manager.generate_proposal_for_session(session, output_path)
        if proposal:
            return f"{base}\nProposed .devproject update: {proposal['proposal_id']}"
        return f"{base}\n.devproject already in sync."
    except Exception as e:
        return f"{base}\n.devproject proposal skipped: {e}"


@mcp.tool()
def propose_feature_grouping(
    working_directory: str,
    session_path: str,
    grouping_json: str,
) -> str:
    """Apply your in-conversation feature grouping to produce a .devproject proposal.

    Call this after `save_session_notes` returns a grouping prompt. Pass the
    `session_path` from that prompt and your grouping JSON (with `candidates`
    and `unassigned` arrays) as `grouping_json`.

    Args:
        working_directory: Path to the project root.
        session_path: Path to the .devsession file (provided by save_session_notes).
        grouping_json: JSON string of {"candidates": [...], "unassigned": [...]}.
    """
    from .session.devsession import DevSession
    from .project.devproject import DevProjectManager

    project_root = _resolve_root(working_directory)
    if project_root is None:
        return "No project root found."

    sess_path = Path(session_path).expanduser().resolve()
    if not sess_path.exists():
        return f"Session file not found: {sess_path}"

    try:
        grouping = json.loads(grouping_json)
    except json.JSONDecodeError as e:
        return f"Invalid grouping_json: {e}"

    if not isinstance(grouping, dict) or "candidates" not in grouping:
        return "grouping_json must be an object with a `candidates` array."

    try:
        session = DevSession.load(sess_path)
    except Exception as e:
        return f"Failed to load session: {e}"

    manager = DevProjectManager(project_root)
    try:
        _, proposal = manager.apply_grouping_proposal(session, sess_path, grouping)
    except Exception as e:
        return f"Failed to apply grouping: {e}"

    if proposal is None:
        return ".devproject already in sync — no proposal generated."

    op_lines = []
    for op in proposal["diff"]:
        if op["op"] == "add_feature":
            feat = op["feature"]
            op_lines.append(f"  + add {feat['feature_id']} — {feat.get('title', '')}")
        elif op["op"] == "update_feature":
            op_lines.append(f"  ~ update {op['feature_id']}")
        elif op["op"] == "link_session":
            op_lines.append("  + link session")

    unassigned = grouping.get("unassigned") or []
    unassigned_note = f"\nUnassigned (excluded from features): {len(unassigned)}" if unassigned else ""

    return (
        f"Proposed .devproject update: {proposal['proposal_id']}\n"
        + "\n".join(op_lines)
        + unassigned_note
    )


@mcp.tool()
def summarize_previous_session(
    working_directory: str,
    overview: str = "",
    decisions: list[str] | None = None,
    problems_solved: list[str] | None = None,
    open_issues: list[str] | None = None,
    next_steps: list[str] | None = None,
    files_changed: list[str] | None = None,
) -> str:
    """Update the most recent unsummarized session with a structured summary.

    Call this when load_project_context indicates the previous session needs
    summarization. You should read the previous session's conversation first,
    analyze it, then call this with your structured analysis.

    Args:
        working_directory: Path to the project.
        overview: 1-2 sentence summary of the previous session.
        decisions: Key technical decisions from the previous session.
        problems_solved: Problems that were solved.
        open_issues: Issues that remained open.
        next_steps: Planned next actions.
        files_changed: Files that were modified.
    """
    from .session.devsession import DevSession
    from .summarization.summary_schema import ensure_summary_span_links

    project_root = _resolve_root(working_directory)
    if project_root is None:
        return "No project root found."

    sessions_dir = _sessions_dir(project_root)

    # Find the most recent .devsession with a stub summary (exclude live snapshots)
    target = None
    target_path = None
    for sf in sorted(sessions_dir.glob("*.devsession"), key=lambda p: p.stat().st_mtime, reverse=True):
        if sf.name.startswith(".live_"):
            continue
        try:
            s = DevSession.load(sf)
            summary_overview = (s.summary or {}).get("overview", "")
            is_stub = not summary_overview or summary_overview in (
                "Session summarized without LLM", "Placeholder summary"
            )
            if is_stub and len(s.conversation) >= 2:
                target = s
                target_path = sf
                break
        except Exception:
            continue

    if target is None:
        return "No unsummarized session found."

    timestamp = datetime.now().isoformat()
    conv_len = len(target.conversation)
    end_msg = f"msg_{conv_len:03d}"

    def _make_range():
        return {"start": "msg_001", "end": end_msg, "start_index": 0, "end_index": conv_len}

    target.summary = {
        "schema_version": "1.1",
        "model": "claude_in_conversation",
        "created_at": timestamp,
        "overview": overview or "Session summarized retroactively.",
        "decisions": [
            {"id": f"dec_{i:03d}", "decision": d, "reasoning": "", "impact": "medium",
             "span_ids": [], "references": [], "message_range": _make_range(),
             "confidence": "medium", "pinned": False, "locked": False}
            for i, d in enumerate(decisions or [])
        ],
        "code_changes": [
            {"id": f"chg_{i:03d}", "files": [f], "description": f"Modified {f}", "type": "feature",
             "lines_added": None, "lines_removed": None, "source_of_truth": "agent_reported",
             "span_ids": [], "references": [], "message_range": _make_range(),
             "confidence": "medium", "pinned": False, "locked": False}
            for i, f in enumerate(files_changed or [])
        ],
        "problems_solved": [
            {"id": f"prb_{i:03d}", "problem": p, "solution": "",
             "span_ids": [], "references": [], "message_range": _make_range(),
             "confidence": "medium", "pinned": False, "locked": False}
            for i, p in enumerate(problems_solved or [])
        ],
        "open_issues": [
            {"id": f"iss_{i:03d}", "issue": issue, "severity": "medium",
             "span_ids": [], "references": [], "message_range": _make_range(),
             "confidence": "medium", "pinned": False, "locked": False}
            for i, issue in enumerate(open_issues or [])
        ],
        "next_steps": [
            {"id": f"nxt_{i:03d}", "action": step, "priority": i + 1,
             "span_ids": [], "references": [], "message_range": _make_range(),
             "confidence": "medium", "pinned": False, "locked": False}
            for i, step in enumerate(next_steps or [])
        ],
        "causal_edges": [],
        "audit_trail": [],
    }

    try:
        target.spans = ensure_summary_span_links(target.summary, target.spans)
    except Exception:
        pass

    target.save(target_path, skip_validation=True)

    # Background embed the new summary items + spans
    try:
        from .hooks.session_recorder import register_bg_task
        import subprocess, sys
        script = (
            "import sys\n"
            "from pathlib import Path\n"
            "path = Path(sys.argv[1])\n"
            "from reccli.session.devsession import DevSession\n"
            "s = DevSession.load(path)\n"
            "s.generate_embeddings(force=False, storage_mode='external')\n"
            "for span in s.spans:\n"
            "    span.pop('embedding', None)\n"
            "if s.summary:\n"
            "    for cat in ['decisions','code_changes','problems_solved','open_issues','next_steps']:\n"
            "        for item in s.summary.get(cat, []):\n"
            "            item.pop('embedding', None)\n"
            "s.save(path)\n"
            "from reccli.retrieval.vector_index import build_unified_index\n"
            "build_unified_index(path.parent, verbose=False)\n"
        )
        proc = subprocess.Popen(
            [sys.executable, "-c", script, str(target_path)],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
        register_bg_task(project_root, proc.pid, "summarize_previous_session.embed")
    except Exception:
        pass

    item_counts = []
    if decisions: item_counts.append(f"{len(decisions)} decisions")
    if problems_solved: item_counts.append(f"{len(problems_solved)} problems")
    if files_changed: item_counts.append(f"{len(files_changed)} file changes")
    if open_issues: item_counts.append(f"{len(open_issues)} open issues")
    if next_steps: item_counts.append(f"{len(next_steps)} next steps")

    return (
        f"Updated {target_path.name} with retroactive summary.\n"
        f"Session had {conv_len} messages.\n"
        f"Summary: {', '.join(item_counts) if item_counts else 'overview only'}"
    )


@mcp.tool()
def evaluate_continuation(
    goal: str,
    open_items: list[str],
    resolved_items: list[str] | None = None,
) -> str:
    """Decide whether to continue working or wait for user input.

    Call this after completing a step when you have open items remaining.
    Pass your current goal and open items directly — do not rely on the
    WAL, since the Stop hook hasn't fired yet for your current response.

    If it returns action=continue, work on the next item it provides.
    If it returns action=wait or action=done, stop and let the user direct.

    Args:
        goal: The user's current session goal.
        open_items: List of open items from your current session signal.
        resolved_items: Optional list of items you just resolved (for logging).
    """
    if not open_items:
        return json.dumps({"action": "done", "reason": "No open items."})

    if not goal:
        # No goal set — treat all open items as actionable
        return json.dumps({
            "action": "continue",
            "goal": "",
            "next": open_items[0],
            "remaining": open_items[1:],
            "filtered": [],
        })

    # Build an expanded goal vocabulary using the same synonym clusters search uses
    _STOP = {
        "the","a","an","and","or","but","in","on","at","to","for","of","with",
        "by","from","as","is","are","was","were","be","been","being","this","that",
    }
    goal_lower = goal.lower()
    goal_words = {w for w in goal_lower.split() if w not in _STOP and len(w) > 2}

    try:
        from .retrieval.query_expansion import _SYNONYM_MAP
        expanded = set(goal_words)
        for w in list(goal_words):
            if w in _SYNONYM_MAP:
                expanded |= _SYNONYM_MAP[w]
        goal_words = expanded
    except Exception:
        pass

    actionable = []
    filtered = []
    for item in open_items:
        item_lower = item.lower()
        item_words = {w for w in item_lower.split() if w not in _STOP and len(w) > 2}
        overlap = goal_words & item_words
        substring_match = any(w in item_lower for w in goal_words if len(w) > 3)
        if overlap or substring_match:
            actionable.append(item)
        else:
            filtered.append(item)

    if not actionable:
        return json.dumps({
            "action": "wait",
            "reason": "Open items are not related to the current goal.",
            "goal": goal,
            "filtered_items": filtered,
        })

    return json.dumps({
        "action": "continue",
        "goal": goal,
        "next": actionable[0],
        "remaining": actionable[1:],
        "filtered": filtered,
    })


@mcp.tool()
def list_issues(
    working_directory: str,
    clear: bool = False,
) -> str:
    """List or clear accumulated issue flags from RecCli hooks and tools.

    Issues are logged when hooks or tools encounter errors that would
    otherwise be silently swallowed. Use this to diagnose problems with
    session recording, search, or context injection.

    Args:
        working_directory: Path to the project or any subdirectory within it.
        clear: If True, clear the issue log after reading.
    """
    project_root = _resolve_root(working_directory)
    if project_root is None:
        return "No project root found."

    from .hooks.session_recorder import get_issues, clear_issues

    issues = get_issues(project_root)
    if not issues:
        return "No issues logged."

    lines = [f"**{len(issues)} issue(s) logged:**\n"]
    for i, issue in enumerate(issues, 1):
        ts = issue.get("timestamp", "?")[:19]
        sev = issue.get("severity", "?")
        comp = issue.get("component", "?")
        msg = issue.get("message", "?")
        lines.append(f"{i}. [{sev}] {ts} — {comp}: {msg}")
        tb = issue.get("traceback")
        if tb and tb.strip() != "NoneType: None":
            # Show last line of traceback (the actual error)
            last_line = tb.strip().splitlines()[-1]
            lines.append(f"   → {last_line}")

    if clear:
        count = clear_issues(project_root)
        lines.append(f"\nCleared {count} issue(s).")

    return "\n".join(lines)


@mcp.tool()
def inspect_result_id(
    result_id: str,
    working_directory: str,
) -> str:
    """Inspect what a result_id actually points to without expanding its context.

    Returns the hit type (summary item / span / message), the session it belongs to,
    what it links to (message_range, span_ids, references), and the summary-side
    fields — useful for debugging why a search hit looks wrong or for understanding
    the topology of a result before deciding whether to expand it.

    Args:
        result_id: The result_id from a search_history / search_by_file result.
        working_directory: Path to the project.
    """
    project_root = _resolve_root(working_directory)
    if project_root is None:
        return "No project root found."

    sessions_dir = _sessions_dir(project_root)
    index_path = sessions_dir / "index.json"
    if not index_path.exists():
        _ensure_index(sessions_dir)

    target = None
    if index_path.exists():
        try:
            with open(index_path, "r") as f:
                index = json.load(f)
            for v in index.get("unified_vectors", []):
                if v.get("id") == result_id:
                    target = v
                    break
        except Exception as e:
            return f"Failed to read index: {e}"

    # Fall back to parsing result_id format directly
    if target is None:
        parts = result_id.rsplit("_msg_", 1)
        session_stem = parts[0] if len(parts) == 2 else result_id
        msg_idx = None
        if len(parts) == 2:
            try:
                msg_idx = int(parts[1])
            except ValueError:
                pass
        return json.dumps({
            "result_id": result_id,
            "source": "parsed-only",
            "hit_type": "message" if msg_idx is not None else "unknown",
            "session": session_stem,
            "message_index": msg_idx,
            "note": "Not found in index — may be a search_by_file/search_by_time result without embedding.",
        }, indent=2)

    msg_id = target.get("message_id", "")
    hit_type = "message"
    if any(msg_id.startswith(p) for p in _SUMMARY_ITEM_PREFIXES):
        hit_type = "summary_item"
    elif msg_id.startswith("spn_"):
        hit_type = "span"

    payload = {
        "result_id": result_id,
        "source": "index",
        "hit_type": hit_type,
        "session": target.get("session"),
        "message_id": msg_id,
        "message_index": target.get("message_index"),
        "kind": target.get("kind"),
        "timestamp": target.get("timestamp"),
        "content_preview": (target.get("content_preview") or "")[:200],
    }

    # For summary items and spans, enrich with the linked structure from the session file
    if hit_type in ("summary_item", "span"):
        session_stem = target.get("session")
        session_file = sessions_dir / f"{session_stem}.devsession"
        if session_file.exists():
            try:
                from .session.devsession import DevSession
                s = DevSession.load(session_file, verify_checksums=False)
                if hit_type == "summary_item":
                    from .retrieval.search import _find_summary_item
                    item = _find_summary_item(s.summary, msg_id)
                    if item:
                        payload["summary_item"] = {
                            "id": item.get("id"),
                            "text": (item.get("decision") or item.get("action") or
                                     item.get("problem") or item.get("issue") or
                                     item.get("description") or ""),
                            "span_ids": item.get("span_ids", []),
                            "references": item.get("references", []),
                            "message_range": item.get("message_range"),
                            "confidence": item.get("confidence"),
                            "pinned": item.get("pinned", False),
                            "locked": item.get("locked", False),
                        }
                elif hit_type == "span":
                    from .retrieval.search import _find_span
                    span = _find_span(s.spans or [], msg_id)
                    if span:
                        payload["span"] = {
                            "id": span.get("id"),
                            "kind": span.get("kind"),
                            "topic": span.get("topic"),
                            "start_index": span.get("start_index"),
                            "end_index": span.get("end_index"),
                            "message_ids": span.get("message_ids", []),
                        }
            except Exception as e:
                payload["load_error"] = str(e)

    return json.dumps(payload, indent=2, ensure_ascii=False)


@mcp.tool()
def preview_context(working_directory: str) -> str:
    """Preview exactly what load_project_context would inject right now.

    Returns the same content load_project_context would, but framed as a preview
    so you can validate your project map and resume-brief before actually starting
    work. Useful for debugging context-injection issues or for inspecting what the
    agent sees at session start.

    Args:
        working_directory: Path to the project or any subdirectory within it.
    """
    return "# PREVIEW — This is what load_project_context would inject:\n\n" + load_project_context(working_directory)


@mcp.tool()
def rebuild_index(working_directory: str) -> str:
    """Force a full rebuild of the unified vector index.

    Call this after an embedding provider change, if list_issues surfaces
    dimension-mismatch errors, or if search results seem stale. The canonical
    session data (conversation, summary, spans) is untouched — only the index
    is regenerated.

    Args:
        working_directory: Path to the project.
    """
    project_root = _resolve_root(working_directory)
    if project_root is None:
        return "No project root found."

    sessions_dir = _sessions_dir(project_root)

    try:
        from .hooks.session_recorder import flush_active_wals
        flush_active_wals(project_root)
    except Exception:
        pass

    try:
        from .retrieval.vector_index import build_unified_index
        index = build_unified_index(sessions_dir, verbose=False)
    except Exception as e:
        return f"Rebuild failed: {e}"

    total = index.get("total_vectors", 0)
    sessions = index.get("total_sessions", 0)
    emb = index.get("embedding", {})
    model = emb.get("model", "?")
    dims = emb.get("dimensions", "?")
    return (
        f"Unified index rebuilt.\n"
        f"  Sessions: {sessions}\n"
        f"  Vectors: {total}\n"
        f"  Embedding: {model} ({dims}D)"
    )


@mcp.tool()
def delete_session(
    session_id: str,
    working_directory: str,
    hard: bool = False,
) -> str:
    """Archive or delete a recorded session.

    By default the session file is moved to devsession/.archived/ — reversible,
    and archived sessions are excluded from search after the next index rebuild.
    Pass hard=True to permanently delete the file and its artifact sidecars.

    Args:
        session_id: The session stem (e.g., "session-20261018-153045"), as shown by list_sessions.
        working_directory: Path to the project.
        hard: If True, permanently delete. Default False archives instead.
    """
    project_root = _resolve_root(working_directory)
    if project_root is None:
        return "No project root found."

    sessions_dir = _sessions_dir(project_root)
    session_file = sessions_dir / f"{session_id}.devsession"
    if not session_file.exists():
        return f"Session '{session_id}' not found."

    sidecars = [
        sessions_dir / f".artifacts_{session_id}.json",
        sessions_dir / f"{session_id}.embeddings.npy",
    ]
    existing_sidecars = [p for p in sidecars if p.exists()]

    if hard:
        try:
            session_file.unlink()
            for p in existing_sidecars:
                p.unlink()
        except Exception as e:
            return f"Hard delete failed: {e}"
        action = "deleted"
    else:
        archive_dir = sessions_dir / ".archived"
        archive_dir.mkdir(parents=True, exist_ok=True)
        try:
            session_file.rename(archive_dir / session_file.name)
            for p in existing_sidecars:
                p.rename(archive_dir / p.name)
        except Exception as e:
            return f"Archive failed: {e}"
        action = "archived"

    # Rebuild index so the removed session stops appearing in search
    try:
        from .retrieval.vector_index import build_unified_index
        build_unified_index(sessions_dir, verbose=False)
        index_note = " Index rebuilt."
    except Exception as e:
        index_note = f" Index rebuild failed: {e} — run rebuild_index manually."

    sidecar_note = f" Moved/removed {len(existing_sidecars)} sidecar(s)." if existing_sidecars else ""
    return f"Session '{session_id}' {action}.{sidecar_note}{index_note}"


def _find_session_with_item(sessions_dir: Path, item_id: str):
    """Locate the .devsession file containing a given summary item ID."""
    from .session.devsession import DevSession
    for sf in sessions_dir.glob("*.devsession"):
        if sf.name.startswith(".live_"):
            continue
        try:
            s = DevSession.load(sf, verify_checksums=False)
        except Exception:
            continue
        if not s.summary:
            continue
        for cat in ("decisions", "code_changes", "problems_solved", "open_issues", "next_steps"):
            for item in s.summary.get(cat, []):
                if item.get("id") == item_id:
                    return sf, s, cat, item
    return None, None, None, None


_ITEM_TEXT_FIELDS = {
    "dec_": "decision",
    "chg_": "description",
    "prb_": "problem",
    "iss_": "issue",
    "nxt_": "action",
}


@mcp.tool()
def edit_summary_item(
    item_id: str,
    working_directory: str,
    new_text: str = "",
    new_confidence: str = "",
    new_reasoning: str = "",
    new_solution: str = "",
) -> str:
    """Edit the text or metadata of a specific summary item.

    Use this to correct a wrong decision, clarify a problem statement, or
    update confidence on an item that was recorded under uncertainty.
    Respects the `locked` flag — locked items cannot be edited until unlocked
    via the devsession file.

    Args:
        item_id: The summary item ID (e.g. "dec_000", "prb_001").
        working_directory: Path to the project.
        new_text: New primary text. Updates `decision`, `description`, `problem`, `issue`, or `action` depending on prefix.
        new_confidence: Optional new confidence level: "low", "medium", "high".
        new_reasoning: Optional new reasoning (for decisions only).
        new_solution: Optional new solution (for problems_solved only).
    """
    project_root = _resolve_root(working_directory)
    if project_root is None:
        return "No project root found."

    sessions_dir = _sessions_dir(project_root)
    sf, session, cat, item = _find_session_with_item(sessions_dir, item_id)
    if item is None:
        return f"Summary item '{item_id}' not found across any session."

    if item.get("locked"):
        return f"Item '{item_id}' is locked. Edit rejected — unlock by setting `locked: false` in {sf.name} if this is intentional."

    changes = []
    if new_text:
        prefix = item_id[:4]
        field = _ITEM_TEXT_FIELDS.get(prefix)
        if field:
            item[field] = new_text
            changes.append(f"{field}={new_text[:60]!r}")
    if new_confidence:
        if new_confidence not in ("low", "medium", "high"):
            return f"Invalid confidence '{new_confidence}'. Must be low, medium, or high."
        item["confidence"] = new_confidence
        changes.append(f"confidence={new_confidence}")
    if new_reasoning and item_id.startswith("dec_"):
        item["reasoning"] = new_reasoning
        changes.append("reasoning updated")
    if new_solution and item_id.startswith("prb_"):
        item["solution"] = new_solution
        changes.append("solution updated")

    if not changes:
        return "No changes specified."

    try:
        session.save(sf, skip_validation=True)
    except Exception as e:
        return f"Save failed: {e}"

    return f"Updated {item_id} in {sf.name}: {', '.join(changes)}"


@mcp.tool()
def pin_memory(
    item_id: str,
    working_directory: str,
    unpin: bool = False,
) -> str:
    """Pin or unpin a summary item so context injection always includes it.

    Pinned items are surfaced at session start regardless of retrieval relevance —
    useful for "we always need to remember this" decisions or architectural rules
    that should guide every session. Respects the `locked` flag like edit_summary_item.

    Args:
        item_id: The summary item ID (e.g. "dec_000").
        working_directory: Path to the project.
        unpin: If True, remove the pin instead of adding one.
    """
    project_root = _resolve_root(working_directory)
    if project_root is None:
        return "No project root found."

    sessions_dir = _sessions_dir(project_root)
    sf, session, cat, item = _find_session_with_item(sessions_dir, item_id)
    if item is None:
        return f"Summary item '{item_id}' not found across any session."

    if item.get("locked") and unpin:
        return f"Item '{item_id}' is locked. Unpinning rejected."

    item["pinned"] = not unpin
    try:
        session.save(sf, skip_validation=True)
    except Exception as e:
        return f"Save failed: {e}"

    action = "unpinned" if unpin else "pinned"
    return f"Item {item_id} {action} in {sf.name}."


@mcp.tool()
def retry_summarization(
    working_directory: str,
    session_id: str = "",
) -> str:
    """Re-run background summarization + embedding on a session.

    By default targets the most recent session with a stub or missing summary.
    Pass session_id to target a specific session. Spawns the same background
    pipeline used at session end — embed, summarize, update index.

    Args:
        working_directory: Path to the project.
        session_id: Optional specific session stem. If empty, uses the most recent stub.
    """
    project_root = _resolve_root(working_directory)
    if project_root is None:
        return "No project root found."

    sessions_dir = _sessions_dir(project_root)

    target_path = None
    if session_id:
        candidate = sessions_dir / f"{session_id}.devsession"
        if not candidate.exists():
            return f"Session '{session_id}' not found."
        target_path = candidate
    else:
        from .session.devsession import DevSession
        for sf in sorted(sessions_dir.glob("*.devsession"), key=lambda p: p.stat().st_mtime, reverse=True):
            if sf.name.startswith(".live_"):
                continue
            try:
                s = DevSession.load(sf, verify_checksums=False)
                overview = (s.summary or {}).get("overview", "")
                if not overview or overview in ("Session summarized without LLM", "Placeholder summary"):
                    target_path = sf
                    break
            except Exception:
                continue
        if target_path is None:
            return "No unsummarized session found. Pass session_id to force re-run on a specific session."

    try:
        from .hooks.session_recorder import register_bg_task
        import subprocess, sys
        script = (
            "import sys\n"
            "from pathlib import Path\n"
            "path = Path(sys.argv[1])\n"
            "from reccli.session.devsession import DevSession\n"
            "s = DevSession.load(path)\n"
            "if not s.summary or not s.summary.get('overview','').strip() "
            "or s.summary.get('overview','') in ('Session summarized without LLM','Placeholder summary'):\n"
            "    s.generate_summary()\n"
            "s.generate_embeddings(force=False, storage_mode='external')\n"
            "for span in s.spans:\n"
            "    span.pop('embedding', None)\n"
            "if s.summary:\n"
            "    for cat in ['decisions','code_changes','problems_solved','open_issues','next_steps']:\n"
            "        for item in s.summary.get(cat, []):\n"
            "            item.pop('embedding', None)\n"
            "s.save(path)\n"
            "from reccli.retrieval.vector_index import build_unified_index\n"
            "build_unified_index(path.parent, verbose=False)\n"
        )
        proc = subprocess.Popen(
            [sys.executable, "-c", script, str(target_path)],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
        register_bg_task(project_root, proc.pid, f"retry_summarization:{target_path.stem}")
    except Exception as e:
        return f"Failed to spawn retry: {e}"

    return (
        f"Re-running summarization on {target_path.name} in the background.\n"
        f"Check list_issues if this doesn't complete within a few minutes."
    )


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main():
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
