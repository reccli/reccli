"""
RecCli MCP Server

Exposes RecCli's temporal memory engine to any MCP-compatible agent
(Claude Code, Cursor, Windsurf, etc.) as callable tools.

Transport: stdio (stdout is the MCP channel — never print() to stdout)
"""

import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Optional

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

    # Mark this project as active so hooks record to it even from ~/
    try:
        from .hooks.session_recorder import set_active_project
        set_active_project("mcp_active", project_root)
    except Exception:
        pass

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
) -> str:
    """Search past session history for decisions, code changes, and problems solved.

    Uses hybrid retrieval (dense embeddings + BM25 + reciprocal rank fusion)
    across all .devsession files in the project.

    Args:
        query: Natural language search query (e.g. "what did we decide about auth?").
        working_directory: Path to the project.
        top_k: Number of results to return (default 5).
    """
    from .retrieval.search import search

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
        results = search(
            sessions_dir=sessions_dir,
            query=query,
            top_k=top_k,
            provider=provider,
        )
    except Exception as e:
        return f"Search failed: {e}"

    if not results:
        return "No results found. The project may not have any session history yet."

    return _format_search_results(results)


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
        return f"Could not expand result: {result_id}. Index may need rebuilding."

    lines = []
    if result.get("summary"):
        lines.append("**Summary context:**")
        lines.append(json.dumps(result["summary"], indent=2, ensure_ascii=False))
        lines.append("")

    context_messages = result.get("context_messages", [])
    if context_messages:
        lines.append("**Conversation context:**")
        for msg in context_messages:
            role = msg.get("role", "?")
            content = (msg.get("content") or "")[:500]
            lines.append(f"[{role}]: {content}")
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

    # Build summary from structured input, with ranges spanning the real conversation
    conv_len = len(session.conversation)
    end_msg = f"msg_{conv_len:03d}"

    def _make_range():
        return {"start": "msg_001", "end": end_msg, "start_index": 0, "end_index": conv_len}

    session.summary = {
        "schema_version": "1.1",
        "model": "agent_reported",
        "created_at": timestamp,
        "overview": overview or "Agent-reported session notes.",
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
        from .hooks.session_recorder import _spawn_background_summarize
        # Don't re-summarize (we already have the summary), just embed + index
        import subprocess, sys
        script = (
            "import sys\n"
            "from pathlib import Path\n"
            "path = Path(sys.argv[1])\n"
            "from reccli.session.devsession import DevSession\n"
            "s = DevSession.load(path)\n"
            "s.generate_embeddings()\n"
            "s.save(path)\n"
            "from reccli.retrieval.vector_index import update_index_with_new_session\n"
            "update_index_with_new_session(path.parent, path, verbose=False)\n"
        )
        subprocess.Popen(
            [sys.executable, "-c", script, str(output_path)],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
    except Exception:
        pass

    # Also update index immediately for BM25 (embeddings come later from background)
    try:
        from .retrieval.vector_index import update_index_with_new_session
        update_index_with_new_session(sessions_dir, output_path, verbose=False)
    except Exception:
        pass  # index update is best-effort; search will auto-build later

    # Propose .devproject update from session evidence
    proposal_msg = ""
    try:
        manager = DevProjectManager(project_root)
        _, proposal = manager.generate_proposal_for_session(session, output_path)
        if proposal:
            proposal_msg = f"\nProposed .devproject update: {proposal['proposal_id']}"
        else:
            proposal_msg = "\n.devproject already in sync."
    except Exception:
        proposal_msg = "\n.devproject proposal skipped."

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

    return (
        f"Session saved: {output_path.name}\n"
        f"Contents: {', '.join(item_counts) if item_counts else 'overview only'}"
        f"{proposal_msg}"
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

    # Find the most recent .devsession with a stub summary
    target = None
    target_path = None
    for sf in sorted(sessions_dir.glob("*.devsession"), key=lambda p: p.stat().st_mtime, reverse=True):
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
        import subprocess, sys
        script = (
            "import sys\n"
            "from pathlib import Path\n"
            "path = Path(sys.argv[1])\n"
            "from reccli.session.devsession import DevSession\n"
            "s = DevSession.load(path)\n"
            "s.generate_embeddings(force=False)\n"
            "s.save(path)\n"
            "from reccli.retrieval.vector_index import build_unified_index\n"
            "build_unified_index(path.parent, verbose=False)\n"
        )
        subprocess.Popen(
            [sys.executable, "-c", script, str(target_path)],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
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


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main():
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
