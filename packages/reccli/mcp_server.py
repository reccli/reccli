"""
RecCli MCP Server

Exposes RecCli's temporal memory engine to any MCP-compatible agent
(Claude Code, Cursor, Windsurf, etc.) as callable tools.

Transport: stdio (stdout is the MCP channel — never print() to stdout)
"""

import json
import math
import os
import re
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from mcp.server.fastmcp import FastMCP

mcp = FastMCP("reccli")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


from .session.devsession import is_stub_overview  # shared stub predicate


def _real_session_files(sessions_dir: Path, newest_first: bool = True):
    """Recorded sessions, excluding live in-progress snapshots.

    pathlib's glob matches dotfiles, so "*.devsession" picks up ".live_*" too. A
    reader looking for "the latest session" would then select the snapshot of the
    conversation currently running, rather than the last finished one.
    """
    files = [p for p in sessions_dir.glob("*.devsession") if not p.name.startswith(".live_")]
    return sorted(files, key=lambda p: p.stat().st_mtime, reverse=newest_first)


def _resolve_root(working_directory: str) -> Optional[Path]:
    from .project.devproject import discover_project_root
    return discover_project_root(Path(working_directory).expanduser().resolve())


def _organization_process_group_is_live(pid: int, run_dir: Path) -> Optional[bool]:
    """Reconcile durable organization status with the actual supervisor group.

    ``None`` means liveness could not be determined. A matching non-zombie
    organization worker or native-agent child means the group is still live.
    """
    if pid <= 1:
        return False
    try:
        import subprocess

        proc = subprocess.run(
            ["ps", "-o", "pid=,stat=,command=", "-g", str(pid)],
            capture_output=True, text=True, timeout=3, check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if proc.returncode != 0:
        return False
    request_path = str(run_dir / "request.json")
    saw_active_native_child = False
    for raw_line in (proc.stdout or "").splitlines():
        pieces = raw_line.strip().split(None, 2)
        if len(pieces) < 3:
            continue
        stat, command = pieces[1], pieces[2]
        if stat.startswith("Z"):
            continue
        if "reccli.organization_worker" in command and request_path in command:
            return True
        if (
            command.startswith("claude ")
            or " codex exec " in f" {command} "
            or "reccli.mcp_server" in command
        ):
            saw_active_native_child = True
    return saw_active_native_child


def _detect_default_provider() -> str:
    """Pick the audit child provider that matches the host CLI.

    The audit child should run on the same auth/quota/billing surface the
    caller is already paying for. Detection order:

    1. ``RECCLI_HOST`` env var — explicit override. Recommended for Codex MCP
       setup: ``env = { RECCLI_HOST = "codex" }`` in the codex config.toml
       mcp_servers block, since Codex CLI does not reliably pass its own
       session env vars through to MCP subprocesses.
    2. ``CLAUDECODE`` / ``CLAUDE_CODE_SESSION_ID`` → "claude". Claude Code
       passes these to MCP subprocesses automatically.
    3. ``CODEX_SESSION_ID`` / ``CODEX_HOME`` → "codex". Some Codex versions
       pass these through; many do not — prefer ``RECCLI_HOST``.
    4. Best-effort parent process inspection via ``ps``.
    5. Fallback to "claude".
    """
    host_override = (os.environ.get("RECCLI_HOST") or "").strip().lower()
    if host_override in {"claude", "codex"}:
        return host_override

    if os.environ.get("CLAUDECODE") or os.environ.get("CLAUDE_CODE_SESSION_ID"):
        return "claude"
    if os.environ.get("CODEX_SESSION_ID") or os.environ.get("CODEX_HOME"):
        return "codex"

    detected = _detect_provider_from_process_tree()
    if detected:
        return detected

    return "claude"


def _detect_provider_from_process_tree() -> Optional[str]:
    """Walk up the parent process chain and look for codex/claude markers.

    Uses ``ps`` since psutil is not a dependency. Best-effort only — returns
    None on any failure rather than raising.
    """
    import subprocess
    try:
        pid = os.getpid()
        for _ in range(10):
            ppid_proc = subprocess.run(
                ["ps", "-o", "ppid=", "-p", str(pid)],
                capture_output=True, text=True, timeout=2, check=False,
            )
            if ppid_proc.returncode != 0:
                return None
            ppid_str = (ppid_proc.stdout or "").strip()
            if not ppid_str:
                return None
            try:
                ppid = int(ppid_str)
            except ValueError:
                return None
            if ppid <= 1 or ppid == pid:
                return None
            args_proc = subprocess.run(
                ["ps", "-o", "args=", "-p", str(ppid)],
                capture_output=True, text=True, timeout=2, check=False,
            )
            if args_proc.returncode != 0:
                return None
            args = (args_proc.stdout or "").lower()
            if "codex" in args:
                return "codex"
            # Claude Code's CLI script path contains "/claude/" or invokes "claude"
            if "/claude/" in args or args.rstrip().endswith(" claude") or args.startswith("claude "):
                return "claude"
            pid = ppid
    except (OSError, subprocess.TimeoutExpired):
        return None
    return None


_CODEX_MODEL_LINE_RE = __import__("re").compile(r'^model\s*=\s*"([^"]+)"')

# Re-exported from session.devsession so the hooks recorder, this module's
# announcer, and its writer all share one bound. They drifted once (4 vs 2) and
# sessions in the gap were invisible to the announcer while being first in line
# for the writer.
from .session.devsession import MIN_SUMMARIZABLE_MESSAGES as _MIN_SUMMARIZABLE_MESSAGES

def _is_unsummarized(session) -> bool:
    """True if the session carries no real summary (absent or a known stub)."""
    from .session.devsession import is_stub_overview
    return is_stub_overview((session.summary or {}).get("overview", ""))


def _is_superseded_snapshot(session, session_path, sessions_dir) -> bool:
    """True if this session is a stale prefix of a longer, already-summarized sibling.

    A single Claude session can flush more than once, leaving an earlier partial
    file alongside the complete one. Both share ``claude_session_id``. The partial
    never gets summarized, so it sits at the head of the "needs summarizing" queue
    forever and sends agents off to re-summarize content the complete file covers.

    Sharing ``claude_session_id`` is NOT sufficient on its own: one Claude session
    legitimately spans several distinct devsessions. The superseding sibling must
    also be at least as long AND already summarized.

    Prefiltered on file size so this does not parse every session in the directory.
    """
    csid = (session.metadata or {}).get("claude_session_id")
    if not csid:
        return False
    try:
        own_len = len(session.conversation)
    except Exception:
        return False

    needle = f'"claude_session_id": "{csid}"'
    compact = f'"claude_session_id":"{csid}"'

    from .session.devsession import DevSession
    for sf in sessions_dir.glob("*.devsession"):
        if sf == session_path or sf.name.startswith(".live_"):
            continue
        try:
            # Cheap prefilter: metadata sits near the top of the document, so scan a
            # header slice for the id instead of parsing multi-MB files. Do NOT
            # prefilter on file size - a session with MORE messages can be SMALLER on
            # disk once its tool_response payloads have been extracted to sidecars.
            with open(sf, "r", encoding="utf-8", errors="ignore") as fh:
                head = fh.read(65536)
            if needle not in head and compact not in head:
                continue
            other = DevSession.load(sf)
            if (other.metadata or {}).get("claude_session_id") != csid:
                continue
            if _is_unsummarized(other):
                continue
            # Delegate to the single definition of "superseded" so this filter and
            # `reccli doctor` cannot drift apart. They did drift once: the diagnostic
            # copy omitted the prefix test and flagged real sessions for archival.
            from .doctor import is_prefix_superseded
            if is_prefix_superseded(session_path, own_len, sf, len(other.conversation)):
                return True
        except Exception:
            continue
    return False


def _detect_default_model(provider: str) -> Optional[str]:
    """Return the model name configured for the host CLI, or None.

    For codex, parses ``~/.codex/config.toml`` for the top-level ``model``
    key. For claude, returns None — Claude Code's session model is set via
    ``/model`` and is not persisted to a settings file or env var, so the
    spawned subprocess uses the CLI's compiled default unless the caller
    passes ``model`` explicitly.
    """
    provider = (provider or "").strip().lower()
    if provider == "codex":
        config_path = Path.home() / ".codex" / "config.toml"
        if not config_path.exists():
            return None
        try:
            text = config_path.read_text(encoding="utf-8")
        except OSError:
            return None
        in_top_level = True
        for raw_line in text.splitlines():
            line = raw_line.strip()
            if line.startswith("[") and line.endswith("]"):
                in_top_level = False
                continue
            if not in_top_level or not line or line.startswith("#"):
                continue
            match = _CODEX_MODEL_LINE_RE.match(line)
            if match:
                return match.group(1)
        return None
    return None


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


def _sanitize_agent_bridge_stem(value: str, default: str) -> str:
    """Return a filesystem-safe stem for agent bridge files."""
    stem = re.sub(r"[^A-Za-z0-9_.-]+", "_", (value or "").strip())
    stem = stem.strip("._-")
    if not stem:
        stem = default
    return stem[:80]


def _tail_text(path: Path, lines: int) -> str:
    """Read the last N lines from a small text protocol file."""
    limit = max(1, min(int(lines or 1), 200))
    try:
        content = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return ""
    except UnicodeDecodeError:
        content = path.read_text(encoding="utf-8", errors="replace")
    return "\n".join(content.splitlines()[-limit:])


def _build_resume_from(sessions_dir: Path) -> Optional[str]:
    """Build a concise 'Resume From' block from the latest session's open issues and next steps."""
    session_files = _real_session_files(sessions_dir)
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
            text = (issue.get("issue") if isinstance(issue, dict) else None) or str(issue)
            lines.append(f"- **Open:** {text}")

        for step in summary.get("next_steps", [])[:5]:
            text = (step.get("action") if isinstance(step, dict) else None) or str(step)
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
        _real_session_files(sessions_dir),
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
                if not isinstance(item, dict):
                    continue
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
        # pathlib's glob matches dotfiles, so '*.devsession' already includes
        # '.live_*.devsession'. Concatenating both scanned every live snapshot twice.
        sessions_dir.glob("*.devsession")
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
    session_files = _real_session_files(sessions_dir)
    if not session_files:
        return None
    try:
        from .session.devsession import DevSession
        session = DevSession.load(session_files[0], verify_checksums=False)
        if not session.summary:
            return None
        summary = session.summary
        parts = []
        # A crashed summarization writes "Summarization failed: <api error>" into
        # overview. Rendering it verbatim injected a raw provider error as the
        # session's remembered history into the first tool every session calls.
        if summary.get("overview") and not is_stub_overview(summary.get("overview")):
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
                    if isinstance(item, dict):
                        text = (
                            item.get("decision")
                            or item.get("problem")
                            or item.get("issue")
                            or item.get("action")
                            or str(item)
                        )
                    else:
                        text = str(item)
                    parts.append(f"- {text}")
        return "\n".join(parts) if parts else None
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Tools
# ---------------------------------------------------------------------------

@mcp.tool()
def establish_agent_bridge(
    agent_id: str = "",
    peer_id: str = "",
    channel_name: str = "agent_chat",
    message: str = "",
    base_directory: str = "/private/tmp",
    reset: bool = False,
    tail_lines: int = 40,
) -> str:
    """Establish a file-backed bridge for two local coding agents.

    Creates or opens an append-only shared log, optionally appends a message
    from this agent, and returns the protocol plus recent log tail. Repeated
    calls are safe: by default the log is never truncated.

    Args:
        agent_id: Short name for the caller, such as "codex" or "claude".
        peer_id: Optional peer name. When provided, mirror file paths are returned.
        channel_name: Safe stem for the shared log file. Defaults to agent_chat.
        message: Optional message to append to the shared log and outbound mirror.
        base_directory: Directory for bridge files. Defaults to /private/tmp.
        reset: If True, truncate the shared log before writing the bridge header.
        tail_lines: Number of recent log lines to return, capped at 200.
    """
    base_dir = Path(base_directory or "/private/tmp").expanduser().resolve()
    try:
        base_dir.mkdir(parents=True, exist_ok=True)
    except Exception as e:
        return f"Failed to create bridge directory {base_dir}: {e}"

    channel = _sanitize_agent_bridge_stem(channel_name, "agent_chat")
    agent = _sanitize_agent_bridge_stem(agent_id, "agent")
    peer = _sanitize_agent_bridge_stem(peer_id, "peer") if peer_id else ""

    log_path = base_dir / f"{channel}.log"
    outbound_mirror = base_dir / f"{agent}_to_{peer}.txt" if peer else None
    inbound_mirror = base_dir / f"{peer}_to_{agent}.txt" if peer else None

    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    header = (
        "# RecCli agent bridge\n"
        f"# channel={channel}\n"
        "# protocol=append-only shared log; do not truncate except with reset=True\n"
        "# message_format=[YYYY-MM-DD HH:MM:SS agent_id]: message\n"
    )

    try:
        if reset or not log_path.exists() or log_path.stat().st_size == 0:
            log_path.write_text(header, encoding="utf-8")

        appended_line = ""
        if message:
            clean_message = message.replace("\r\n", "\n").replace("\r", "\n").strip()
            appended_line = f"[{timestamp} {agent}]: {clean_message}"
            with log_path.open("a", encoding="utf-8") as f:
                f.write("\n" + appended_line + "\n")
            if outbound_mirror is not None:
                outbound_mirror.write_text(appended_line + "\n", encoding="utf-8")
    except Exception as e:
        return f"Failed to establish bridge at {log_path}: {e}"

    tail = _tail_text(log_path, tail_lines)
    parts = [
        "Agent bridge established.",
        f"shared_log: {log_path}",
        "protocol: append messages to shared_log, poll with stat/tail, never truncate unless reset=True.",
    ]
    if outbound_mirror is not None:
        parts.append(f"outbound_mirror: {outbound_mirror}")
    if inbound_mirror is not None:
        parts.append(f"inbound_mirror: {inbound_mirror}")
    if appended_line:
        parts.append(f"appended: {appended_line}")
    parts.append(f"\nLast {max(1, min(int(tail_lines or 1), 200))} line(s):")
    parts.append(tail or "(empty)")
    return "\n".join(parts)


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
                # Include the session stem: item ids are per-session sequential, so
                # pin_memory/edit_summary_item now refuse an ambiguous id. Printing
                # the id alone made unpinning unreachable from the only surface that
                # shows pinned items.
                sections.append(
                    f"- `{p['id']}`{lock_marker} ({p['category']}, session `{p.get('session', '?')}`): {p['text']}"
                )
            sections.append("")

        sections.append("## Last Session")
        sections.append(last_summary)
        sections.append("")

    # Detect unsummarized previous session
    try:
        from .session.devsession import DevSession
        for sf in sorted(sessions_dir.glob("*.devsession"), key=lambda p: p.stat().st_mtime, reverse=True):
            if sf.name.startswith(".live_"):
                continue  # live snapshots are not summarizable targets
            s = DevSession.load(sf)
            if (_is_unsummarized(s) and len(s.conversation) >= _MIN_SUMMARIZABLE_MESSAGES
                    and not _is_superseded_snapshot(s, sf, sessions_dir)):
                sections.append("## ACTION REQUIRED: Previous Session Unsummarized")
                sections.append(
                    f"The previous session ({sf.name}, {len(s.conversation)} messages) has no structured summary. "
                    "Please read the session conversation using expand_search_result or by reading the file directly, "
                    "analyze the key decisions, code changes, and problems solved, then call "
                    f"summarize_previous_session with your analysis AND session_id=\"{sf.stem}\". "
                    "Always pass session_id so the summary lands on this exact session. "
                    "This links the summary to the full conversation."
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
def start_organization(
    working_directory: str,
    mission: Optional[str] = None,
    launch_mode: str = "auto",
    provider: str = "auto",
    topology: str = "google-rotating",
    max_rounds: int = 8,
    max_concurrency: int = 5,
    turn_timeout_seconds: int = 1200,
    model: str = "auto",
    evidence_paths: Optional[List[str]] = None,
    protected_paths: Optional[List[str]] = None,
    context_manifest: Optional[str] = None,
    experiment_policy: Optional[str] = None,
    max_experiments: int = 3,
    open_console: Optional[bool] = None,
    console_port: int = 8777,
) -> str:
    """Start the project's organization, or an explicitly custom organization.

    This is the single public launch surface. With the default
    ``launch_mode="auto"`` and no ``mission``, RecCli uses the repository's
    tracked project launch contract: it runs declared preflights, validates the
    dynamic mission and current HEAD, applies an eligible terminal-conclusion
    continuation, prevents duplicate/pending-approval launches, and opens the
    authenticated console.

    For a repository without a project launch contract, supplying ``mission``
    starts a custom organization using the remaining arguments. If a tracked
    project contract exists, a caller-supplied mission/configuration requires
    ``launch_mode="custom"`` so project safety policy cannot be bypassed by
    accident. ``launch_mode="project"`` explicitly requires the project path
    and rejects a caller-supplied mission.

    Returns immediately after launching a detached supervisor. Each organization
    member runs as a separate persistent Claude Code or Codex CLI session using
    the caller's existing subscription authentication; this tool never requires
    or forwards an Anthropic/OpenAI API key.

    With provider="auto", RecCli uses a mixed organization when both native CLIs
    are installed and authenticated. Worker/primary-manager lanes share a
    provider for continuity, alternate-manager review prefers the other
    provider, the release manager stays on the host provider, and the fresh
    verifier uses the opposite provider. If only one usable CLI is available,
    auto falls back to a homogeneous run. Explicit "claude" or "codex" always
    remains homogeneous; explicit "mixed" requires both subscriptions.

    The default topology is a Google-style selectively escalated hierarchy with
    four managers, four workers, rotating alternate-manager review, a dedicated
    release manager, exact-candidate approvals, and a fresh final verifier.
    Agents receive the mission, RecCli's `.devproject` feature map, code, tests,
    and task-relevant repository documentation. Raw manager deliberation is not
    broadcast to workers.

    The target repository must have no tracked uncommitted changes. The worker
    creates isolated Git worktrees and writes its durable trace under
    `devsession/agent-organizations/<run-id>/`. Run-scoped artifacts are
    reviewed through a temporary tracked staging prefix, exported to the run's
    `deliverables/` directory with a hash manifest, and removed from the clean
    promotion branch. Native agents edit and test but do not mutate Git
    administrative state: the RecCli supervisor validates scopes, materializes
    commits, and applies reviewed candidates. Remote push and hosting
    credentials are outside this tool. Poll with
    `organization_status`; stop a live run with `cancel_organization`. Every
    terminal run writes a lead-owned `run-conclusion.json` and
    `run-conclusion.md` describing accomplishments, evidence, blockers,
    infrastructure failures, promotion readiness, and the smallest next
    action. `organization_status` returns that conclusion prominently once it
    exists. MCP is pull-based, so callers must continue polling; RecCli cannot
    inject an unsolicited message into a launch turn that has already ended.

    `evidence_paths` selects ignored or external files/directories that every
    agent must see (for example sealed experiment receipts or a related
    reference project). RecCli clones/copies them once into a run-owned,
    read-only snapshot, records per-file SHA-256 hashes, exposes only the
    snapshot to native sessions, checks its inventory after each round, and
    re-hashes it before release. Symlinks are rejected. Relative paths resolve
    from the project root.

    `topology="scientific"` cuts authority at reversibility rather than at a
    research/execution phase boundary. Workers may choose and run experiments
    in disposable branches. Manager B may wake two fresh, opposite-provider
    research specialists for a load-bearing technical question; their
    structured fragments must be synthesized into a validated decision packet
    before dependent implementation is delegated. Dormant specialists consume
    no agent turns. With a tracked `experiment_policy`, primary managers can
    bind one worker, one mutable tracked file, and one immutable evaluator into
    an autonomous baseline/challenger loop. RecCli runs the baseline first,
    enforces fixed trial/time budgets, records a compact ledger, keeps strict
    improvements, host-reverts regressions, and wakes managers only for
    judgment events. `max_experiments` bounds challenger trials and sealed
    generated-output bundles.
    `protected_paths` is a deny-write list for tracked immutable
    evidence, authority records, ledgers, or standards. The adversarial auditor is fully
    evidence-sighted and veto-only. Completion emits a promotion request; it
    does not import outputs into the canonical archive, push, or merge the
    caller's branch.

    `context_manifest` selects one tracked project-local
    `reccli.organization-context-packs.v1` mapping. RecCli materializes a
    hash-bound, read-only common-plus-lane context box for each agent. Workers
    receive their common core and assigned lane; required `paths` are read
    before work while optional `library_paths` are consulted on demand. Agents
    explicitly named as full-context receive the union. Canonical repository
    documentation remains readable and authoritative, so this is educational
    routing rather than a deny-read sandbox.

    Args:
        working_directory: Project root or any path inside the project.
        mission: Custom product/engineering brief. Omit for the normal tracked
            project launch.
        launch_mode: ``auto`` (recommended), ``project``, or ``custom``.
        provider: "auto", "mixed", "claude", or "codex". Uses installed native CLIs.
        topology: "google-rotating" (recommended), "google" (baseline), or
            "scientific" (autonomous reversible scientific exploration).
        max_rounds: Maximum synchronized organization work rounds (default 8).
            One round may run several agent turns in parallel; this is not a
            total-agent-turn count. RecCli may add up to four review-only
            closeout boundaries for candidates already produced at the cap;
            workers and new experiments cannot run during closeout.
        max_concurrency: Maximum native agent subprocesses running at once.
        turn_timeout_seconds: Timeout for each individual agent turn.
        model: Provider model override, or "auto" for the native CLI default.
        evidence_paths: Explicit ignored/external evidence files or directories
            to seal into the shared run snapshot.
        protected_paths: Tracked project-relative files/directories that no
            organization worktree may change.
        context_manifest: Tracked project-relative common-plus-agent context
            mapping with required paths and optional indexed library paths,
            used to build run-scoped documentation boxes.
        experiment_policy: Tracked project-relative
            `reccli.organization-experiment-policy.v1` evaluator policy.
        max_experiments: Hard cap on autonomous challenger trials and sealed
            generated-output bundles (default 3).
        open_console: In project mode, defaults to true. In custom mode,
            defaults to false for backward compatibility.
        console_port: Authenticated localhost console port (default 8777).
    """
    try:
        normalized_mode = str(launch_mode or "auto").strip().lower()
        if normalized_mode not in {"auto", "project", "custom"}:
            return json.dumps({
                "status": "launch_blocked",
                "code": "invalid_launch_mode",
                "error": (
                    "launch_mode must be auto, project, or custom"
                ),
            }, indent=2)

        project_root = _resolve_root(working_directory)
        has_project_contract = False
        if project_root is not None:
            has_project_contract = (
                (project_root / "reccli.organization-launch.json").exists()
                or (
                    project_root
                    / "scripts"
                    / "validate_organization_readiness.py"
                ).is_file()
            )

        custom_mission = mission.strip() if isinstance(mission, str) else ""
        use_project_launch = (
            normalized_mode == "project"
            or (normalized_mode == "auto" and not custom_mission)
        )
        if use_project_launch:
            if custom_mission:
                return json.dumps({
                    "status": "launch_blocked",
                    "code": "project_mode_rejects_custom_mission",
                    "error": (
                        "project launch mode selects the tracked mission; "
                        "omit mission or explicitly use launch_mode='custom'"
                    ),
                }, indent=2)
            from .organization_project_launch import (
                start_project_organization_result,
            )

            return json.dumps(start_project_organization_result(
                working_directory,
                open_console=(
                    True if open_console is None else bool(open_console)
                ),
                console_port=int(console_port),
            ), indent=2, ensure_ascii=False)

        if not custom_mission:
            return json.dumps({
                "status": "launch_blocked",
                "code": "custom_mission_required",
                "error": (
                    "custom launch mode requires a non-empty mission"
                ),
            }, indent=2)
        if normalized_mode == "auto" and has_project_contract:
            return json.dumps({
                "status": "launch_blocked",
                "code": "explicit_custom_mode_required",
                "error": (
                    "this repository owns a project organization launch "
                    "contract; omit mission to use it, or explicitly set "
                    "launch_mode='custom' to bypass project launch policy"
                ),
            }, indent=2)

        from .organization_launch import (
            launch_organization_console,
            start_organization_from_arguments,
        )

        started = start_organization_from_arguments({
            "working_directory": working_directory,
            "mission": custom_mission,
            "provider": provider,
            "topology": topology,
            "max_rounds": max_rounds,
            "max_concurrency": max_concurrency,
            "turn_timeout_seconds": turn_timeout_seconds,
            "model": model,
            "evidence_paths": evidence_paths,
            "protected_paths": protected_paths,
            "context_manifest": context_manifest,
            "experiment_policy": experiment_policy,
            "max_experiments": max_experiments,
        })
        if open_console is True:
            root = _resolve_root(working_directory)
            if root is not None:
                started["console"] = launch_organization_console(
                    root,
                    port=int(console_port),
                    open_browser=True,
                )
        return json.dumps(started, indent=2, ensure_ascii=False)
    except Exception as exc:
        return json.dumps({"status": "failed_to_start", "error": str(exc)}, indent=2)


def start_project_organization(
    working_directory: str,
    open_console: bool = True,
    console_port: int = 8777,
) -> str:
    """Deprecated compatibility alias; use ``start_organization``."""
    return start_organization(
        working_directory,
        launch_mode="project",
        open_console=open_console,
        console_port=console_port,
    )


@mcp.tool()
def organization_status(
    working_directory: str,
    run_id: str,
    include_recent_events: int = 10,
) -> str:
    """Read durable status for a multi-agent organization run.

    This is safe to call after an MCP restart because status is read from the
    project's run directory rather than process memory. Terminal runs include
    a top-level `conclusion` authored by the organization lead, or a
    conservative host fallback when cancellation or infrastructure failure
    prevented that final read-only synthesis.

    Args:
        working_directory: Project root or any path inside the project.
        run_id: Run ID returned by `start_organization`, or an absolute run path.
        include_recent_events: Number of recent event/message records to include.
    """
    try:
        from .organization_control import organization_snapshot

        payload = organization_snapshot(
            working_directory,
            run_id,
            include_recent=include_recent_events,
        )
        payload["recent_events"] = payload.get("activities", [])
        return json.dumps(payload, indent=2, ensure_ascii=False)
    except Exception as exc:
        return json.dumps({"status": "status_error", "run_id": run_id, "error": str(exc)}, indent=2)


@mcp.tool()
def approve_organization(
    working_directory: str,
    run_id: str,
    request_sha256: str,
    idempotency_key: str,
) -> str:
    """Approve one exact organization packet and execute its declared action.

    The approval request must already be staged by a terminal organization.
    RecCli revalidates the request hash, exact Git checkpoint, and clean
    repository before acting. Checkpoint approvals start a fresh successor run;
    verified code promotions fast-forward only the clean local branch. This
    tool never revives a terminal supervisor and never pushes a remote.

    Args:
        working_directory: Project root or any path inside the project.
        run_id: Terminal run containing the staged approval request.
        request_sha256: Exact hash shown by organization_status or the console.
        idempotency_key: Caller-stable key preventing duplicate execution.
    """
    try:
        from .organization_control import approve_organization_request

        result = approve_organization_request(
            working_directory,
            run_id,
            request_sha256=request_sha256,
            idempotency_key=idempotency_key,
            requested_by="mcp-human-operator",
        )
        return json.dumps(result, indent=2, ensure_ascii=False)
    except Exception as exc:
        return json.dumps({
            "status": "approval_error",
            "run_id": run_id,
            "error": str(exc),
        }, indent=2)


@mcp.tool()
def reject_organization(
    working_directory: str,
    run_id: str,
    candidate: str,
    reason: str,
    idempotency_key: str,
) -> str:
    """Permanently reject one exact candidate from a terminal organization.

    Rejection applies no repository changes. It disables later approval and
    binds successor missions not to revive the failed candidate as progress.

    Args:
        working_directory: Project root or any path inside the project.
        run_id: Terminal organization run containing the candidate.
        candidate: Exact 40-character candidate commit to reject.
        reason: Concise human reason the candidate does not advance the goal.
        idempotency_key: Caller-stable key preventing duplicate decisions.
    """
    try:
        from .organization_control import reject_organization_candidate

        result = reject_organization_candidate(
            working_directory,
            run_id,
            candidate=candidate,
            reason=reason,
            idempotency_key=idempotency_key,
            requested_by="mcp-human-operator",
        )
        return json.dumps(result, indent=2, ensure_ascii=False)
    except Exception as exc:
        return json.dumps({
            "status": "rejection_error",
            "run_id": run_id,
            "candidate": candidate,
            "error": str(exc),
        }, indent=2)


@mcp.tool()
def list_organizations(
    working_directory: str,
    limit: int = 100,
) -> str:
    """List durable organization runs for a project, newest first."""
    try:
        from .organization_control import list_organization_runs

        return json.dumps(
            list_organization_runs(working_directory, limit=limit),
            indent=2,
            ensure_ascii=False,
        )
    except Exception as exc:
        return json.dumps({
            "status": "list_error",
            "error": str(exc),
        }, indent=2)


@mcp.tool()
def steer_organization(
    working_directory: str,
    run_id: str,
    target: str,
    message: str,
    tag: str = "plan",
    idempotency_key: Optional[str] = None,
) -> str:
    """Queue a human steering message for an agent or role group.

    The organization applies the message at its next safe round boundary and
    records a durable acknowledgement. Targets may be an exact agent ID or one
    of: ``all``, ``lead``, ``finalizer``, ``managers``, ``workers``, or
    ``integrators``.
    """
    try:
        from .organization_control import queue_control_request

        result = queue_control_request(
            working_directory,
            run_id,
            "message",
            target=target,
            content=message,
            tag=tag,
            idempotency_key=idempotency_key,
            requested_by="mcp-human-operator",
        )
        return json.dumps(result, indent=2, ensure_ascii=False)
    except Exception as exc:
        return json.dumps({
            "status": "steer_error",
            "run_id": run_id,
            "error": str(exc),
        }, indent=2)


@mcp.tool()
def pause_organization(
    working_directory: str,
    run_id: str,
    idempotency_key: Optional[str] = None,
) -> str:
    """Pause after the active synchronized round finishes."""
    try:
        from .organization_control import queue_control_request

        return json.dumps(queue_control_request(
            working_directory,
            run_id,
            "pause",
            idempotency_key=idempotency_key,
            requested_by="mcp-human-operator",
        ), indent=2, ensure_ascii=False)
    except Exception as exc:
        return json.dumps({
            "status": "pause_error",
            "run_id": run_id,
            "error": str(exc),
        }, indent=2)


@mcp.tool()
def resume_organization(
    working_directory: str,
    run_id: str,
    idempotency_key: Optional[str] = None,
) -> str:
    """Resume a run paused at a synchronized round boundary."""
    try:
        from .organization_control import queue_control_request

        return json.dumps(queue_control_request(
            working_directory,
            run_id,
            "resume",
            idempotency_key=idempotency_key,
            requested_by="mcp-human-operator",
        ), indent=2, ensure_ascii=False)
    except Exception as exc:
        return json.dumps({
            "status": "resume_error",
            "run_id": run_id,
            "error": str(exc),
        }, indent=2)


@mcp.tool()
def open_organization_console(
    working_directory: str,
    port: int = 8777,
    open_browser: bool = True,
) -> str:
    """Launch the localhost Next.js organization viewer and steering console.

    Dependency installation and production build are automatic on first use.
    The detached console binds only to ``127.0.0.1`` and requires a generated
    per-launch token for its data and control routes.
    """
    try:
        root = _resolve_root(working_directory)
        if root is None:
            return json.dumps({
                "status": "not_found",
                "working_directory": working_directory,
            }, indent=2)
        from .organization_launch import launch_organization_console

        return json.dumps(launch_organization_console(
            root,
            port=int(port),
            open_browser=open_browser,
        ), indent=2)
    except Exception as exc:
        return json.dumps({
            "status": "console_error",
            "error": str(exc),
        }, indent=2)


@mcp.tool()
def cancel_organization(working_directory: str, run_id: str) -> str:
    """Cancel a running multi-agent organization and its native CLI children.

    The cancellation marker is durable. The tool also reconciles that persisted
    state with process-group liveness: even if status.json already says
    cancelled, a matching live supervisor group is still terminated so active
    Claude/Codex turns stop promptly.

    Args:
        working_directory: Project root or any path inside the project.
        run_id: Run ID returned by `start_organization`, or an absolute run path.
    """
    try:
        from .organization_control import cancel_organization_run

        result = cancel_organization_run(
            working_directory,
            run_id,
            requested_by="mcp-human-operator",
            process_group_liveness=_organization_process_group_is_live,
        )
        return json.dumps(result, indent=2, ensure_ascii=False)
    except Exception as exc:
        return json.dumps({"status": "cancel_error", "run_id": run_id, "error": str(exc)}, indent=2)


@mcp.tool()
def audit_feature(
    working_directory: str,
    feature_id: str,
    agents: int = 6,
    provider: str = "auto",
    mode: str = "report",
    focus: str = "",
    max_files: int = 8,
    max_file_chars: int = 12000,
    timeout_seconds: int = 1800,
    max_concurrency: int = 1,
    model: str = "auto",
    files: Optional[List[str]] = None,
    globs: Optional[List[str]] = None,
) -> str:
    """Dispatch read-only audit agents scoped to one feature.

    Each agent runs through the host CLI adapter (Claude Code or Codex) that
    the caller is already authenticated with — no API keys required. The tool
    resolves a `.devproject` feature, creates an audit context pack under
    `devsession/agent-audits/<date>/<feature>/`, then dispatches one or more
    independent audit agents through the selected provider adapter.

    Audit *scope* defaults to the feature's ``files_touched``. Pass explicit
    ``files`` and/or ``globs`` to override scope when the feature map is stale,
    or to audit a product capability that crosses feature boundaries. The
    feature is still resolved for description, docs, and session linkage.

    Args:
        working_directory: Path to the project or any subdirectory within it.
        feature_id: Feature ID or exact feature title from `.devproject`.
        agents: Number of independent audit agents to run. Default 6.
        provider: "auto" (default; host-detected to match the calling CLI — Claude Code -> "claude", Codex CLI -> "codex"), "codex" (read-only enforced by Codex sandbox), "claude" (read-only enforced by --tools ""), or "none" (prepare artifacts without dispatch).
        mode: Must be "report" in v1.
        focus: Optional narrower instruction for this audit.
        max_files: Max files to include with full text in the context pack. Applies to feature-derived scope and to override scope alike.
        max_file_chars: Max characters to include per file.
        timeout_seconds: Per-agent subprocess timeout.
        max_concurrency: Max provider subprocesses to run at once. Default 1 (sequential): a quota error on one agent aborts the rest of the batch instead of burning quota on every remaining agent. Pass >1 to run in parallel.
        model: "auto" (default), explicit model name (e.g. "opus", "sonnet", "gpt-5.5"), or "" / "none" to use the CLI's compiled default. With "auto" the codex provider parses ~/.codex/config.toml; the claude provider has no env-based detection and falls through to the CLI default unless an explicit model is passed.
        files: Explicit list of relative paths to use as audit scope. When provided (or globs is provided), replaces feature.files_touched. Paths outside project_root are ignored.
        globs: Glob patterns expanded against project_root (e.g. ["src/app/api/**/*.ts", "scripts/*digest*.ts"]). Recursive `**` patterns are supported. Combined with `files` (deduped, files first then globs in result order). When provided, replaces feature.files_touched.
    """
    project_root = _resolve_root(working_directory)
    if project_root is None:
        return "No project root found."

    if (mode or "report").strip().lower() != "report":
        return "Feature audit failed: v1 only supports mode='report'."

    provider_requested = provider
    provider_normalized = (provider or "auto").strip().lower()
    if provider_normalized == "auto":
        provider_normalized = _detect_default_provider()

    model_requested = model
    model_normalized = (model or "").strip()
    if model_normalized.lower() in {"", "none", "default"}:
        model_normalized = None
    elif model_normalized.lower() == "auto":
        model_normalized = _detect_default_model(provider_normalized)

    try:
        from .agent_harness import create_agent_harness_run
        from .agent_providers import run_audit_agents

        run = create_agent_harness_run(
            project_root=project_root,
            feature_id=feature_id,
            mode="audit",
            agent_count=agents,
            focus=focus,
            max_files=max_files,
            max_file_chars=max_file_chars,
            files=files,
            globs=globs,
        )
        agent_results = run_audit_agents(
            provider=provider_normalized,
            project_root=project_root,
            run_dir=Path(run["run_dir"]),
            context_pack_path=Path(run["context_pack_path"]),
            agents=run["agents"],
            timeout_seconds=timeout_seconds,
            max_concurrency=max_concurrency,
            model=model_normalized,
        )
    except Exception as e:
        return f"Feature audit failed: {e}"
    failed_results = [
        r for r in agent_results
        if r.get("status") != "completed"
        or r.get("parse_status", "valid_json") in {"parse_failed", "empty"}
    ]
    quota_skipped = sum(
        1 for r in agent_results
        if r.get("status") == "skipped" and "quota" in (r.get("skip_reason") or "").lower()
    )
    quota_hit = quota_skipped > 0 or any(r.get("quota_error") for r in agent_results)

    if provider_normalized == "none":
        status = "prepared"
        status_reason = "Dry run; no provider dispatched."
    elif not failed_results:
        status = "completed"
        status_reason = f"All {len(agent_results)} agents completed with parseable output."
    elif quota_hit:
        status = "partial"
        completed_count = len(agent_results) - len(failed_results)
        status_reason = (
            f"Provider quota hit. {completed_count} of {len(agent_results)} agents completed; "
            f"{quota_skipped} skipped to preserve quota. Retry later or switch provider."
        )
    else:
        status = "partial"
        status_reason = f"{len(failed_results)} of {len(agent_results)} agents failed, timed out, or returned empty/unparseable output."

    # Aggregate per-agent findings into the run-level report.md. Skip on dry runs
    # so the prepared-artifact stub stays intact.
    if provider_normalized != "none":
        try:
            from .agent_harness import write_merged_report
            write_merged_report(
                Path(run["run_dir"]),
                agent_results,
                bundle_status=status,
                bundle_status_reason=status_reason,
            )
        except Exception:
            pass  # report aggregation is best-effort; per-agent files remain authoritative

    bundle = {
        "status": status,
        "status_reason": status_reason,
        "provider": provider_normalized,
        "provider_requested": provider_requested,
        "model": model_normalized,
        "model_requested": model_requested,
        "mode": "report",
        "max_concurrency": max_concurrency,
        "quota_hit": quota_hit,
        "run_id": run["run_id"],
        "run_dir": run["run_dir"],
        "context_pack": run["context_pack_path"],
        "report": run["report_path"],
        "feature": run["feature"],
        "scope": run.get("scope"),
        "gitignore": run.get("gitignore"),
        "agent_results": agent_results,
    }

    # Persist the bundle to disk so audit_status() can return it later.
    # This is the recovery path when the caller's MCP client times out at its
    # tool boundary (codex hangs up at 120s) — the audit subprocess keeps
    # running here and writes results normally; the caller can then call
    # audit_status(run_id) and get the exact same JSON back.
    try:
        bundle_path = Path(run["run_dir"]) / "bundle.json"
        bundle_path.write_text(
            json.dumps(bundle, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
    except Exception:
        pass  # bundle persistence is best-effort; the synchronous return is authoritative

    return json.dumps(bundle, indent=2, ensure_ascii=False)


def _find_agent_audit_run(project_root: Path, run_id_or_path: str) -> Path:
    explicit = Path(run_id_or_path).expanduser()
    if explicit.exists() and explicit.is_dir():
        return explicit.resolve()

    audit_root = project_root / "devsession" / "agent-audits"
    matches = [path for path in audit_root.glob(f"*/*/{run_id_or_path}") if path.is_dir()]
    if len(matches) == 1:
        return matches[0]
    if not matches:
        raise FileNotFoundError(f"Audit run '{run_id_or_path}' not found under {audit_root}")
    raise ValueError(f"Multiple audit runs matched '{run_id_or_path}'")


@mcp.tool()
def audit_status(working_directory: str, run_id: str) -> str:
    """Retrieve the bundle JSON for an audit run.

    Recovery path for callers whose MCP client timed out at its tool boundary
    (codex hangs up at 120s) while the audit subprocess kept running and finished
    on disk. ``audit_feature`` writes ``<run_dir>/bundle.json`` as its final
    step; this tool returns that file verbatim, so the caller gets the same
    JSON they would have received from the original synchronous call.

    Args:
        working_directory: Path to the project or any subdirectory within it.
        run_id: Audit run ID returned by audit_feature, or the explicit run_dir path.

    Returns:
        The persisted bundle JSON when ``bundle.json`` exists. Otherwise a
        small status object indicating ``in_progress`` (with per-agent findings
        progress so far) or ``not_found`` if the run_id can't be resolved.
    """
    project_root = _resolve_root(working_directory)
    if project_root is None:
        return "No project root found."

    try:
        run_dir = _find_agent_audit_run(project_root, run_id)
    except FileNotFoundError as e:
        return json.dumps(
            {"status": "not_found", "run_id": run_id, "error": str(e)},
            indent=2,
            ensure_ascii=False,
        )
    except ValueError as e:
        return json.dumps(
            {"status": "ambiguous", "run_id": run_id, "error": str(e)},
            indent=2,
            ensure_ascii=False,
        )

    bundle_path = run_dir / "bundle.json"
    if bundle_path.exists():
        return bundle_path.read_text(encoding="utf-8")

    # No bundle yet — the run is in progress, never started, or crashed
    # before audit_feature got to its final write.
    progress: List[Dict[str, Any]] = []
    for findings_file in sorted(run_dir.glob("agent_*_findings.json")):
        agent_id = findings_file.stem.removesuffix("_findings")
        try:
            data = json.loads(findings_file.read_text(encoding="utf-8"))
            findings = data.get("findings", [])
            progress.append({
                "agent_id": agent_id,
                "status": data.get("status", "unknown"),
                "findings": len(findings) if isinstance(findings, list) else 0,
            })
        except Exception:
            progress.append({"agent_id": agent_id, "status": "unreadable", "findings": 0})

    return json.dumps(
        {
            "status": "in_progress",
            "run_id": run_id,
            "run_dir": str(run_dir),
            "agent_progress": progress,
            "note": (
                "bundle.json not yet written. The audit subprocess may still be "
                "running, or it may have crashed before audit_feature finalized. "
                "Per-agent files under run_dir are authoritative; this view is a "
                "best-effort progress snapshot."
            ),
        },
        indent=2,
        ensure_ascii=False,
    )


@mcp.tool()
def consolidate_audit(
    working_directory: str,
    run_id: str,
    judge_provider: str = "none",
    judge_model: str = "auto",
    max_judge_clusters: int = 50,
) -> str:
    """Cluster N agents' findings into a deduplicated, ranked set.

    Reads completed ``agent_*_findings.json`` files in the run directory,
    clusters them via the shared similarity heuristic from ``audit_analysis``
    (title token Jaccard + file-path overlap), picks a representative per
    cluster, and ranks by (agent_count, severity, confidence) — agreement is
    the dominant signal. Writes ``consolidated.json`` next to ``bundle.json``
    so ``audit_status`` and other callers can find it.

    Args:
        working_directory: Path to the project or any subdirectory.
        run_id: Audit run ID returned by ``audit_feature``, or run_dir path.
        judge_provider: ``"none"`` (default; deterministic clustering only,
            free and millisecond-fast), ``"auto"`` (host-detected), ``"claude"``,
            or ``"codex"`` to add an LLM judge pass that may merge clusters
            the heuristic missed. Failures fall back to deterministic ordering;
            this tool never raises on judge errors.
        judge_model: ``"auto"`` (default), explicit model name, or ``"none"``.
            Only meaningful when judge_provider is set.
        max_judge_clusters: Caps how many clusters the judge sees. Pathological
            runs with hundreds of clusters won't trigger runaway LLM cost.

    Returns the consolidated bundle as a JSON string. The same payload is
    persisted to ``<run_dir>/consolidated.json`` for caller-side recovery.
    """
    project_root = _resolve_root(working_directory)
    if project_root is None:
        return "No project root found."

    try:
        run_dir = _find_agent_audit_run(project_root, run_id)
    except FileNotFoundError as e:
        return json.dumps(
            {"status": "not_found", "run_id": run_id, "error": str(e)},
            indent=2,
            ensure_ascii=False,
        )
    except ValueError as e:
        return json.dumps(
            {"status": "ambiguous", "run_id": run_id, "error": str(e)},
            indent=2,
            ensure_ascii=False,
        )

    judge_provider_normalized = (judge_provider or "").strip().lower()
    if judge_provider_normalized in {"", "none"}:
        judge_provider_resolved: Optional[str] = None
    elif judge_provider_normalized == "auto":
        judge_provider_resolved = _detect_default_provider()
    else:
        judge_provider_resolved = judge_provider_normalized

    judge_model_normalized: Optional[str] = (judge_model or "").strip()
    if judge_model_normalized.lower() in {"", "none", "default"}:
        judge_model_normalized = None
    elif judge_model_normalized.lower() == "auto":
        judge_model_normalized = _detect_default_model(
            judge_provider_resolved or "claude"
        )

    from .audit_consolidation import consolidate_audit_run

    try:
        result = consolidate_audit_run(
            run_dir,
            project_root=project_root,
            judge_provider=judge_provider_resolved,
            judge_model=judge_model_normalized,
            max_judge_clusters=max_judge_clusters,
        )
    except Exception as e:
        # consolidate_audit_run is documented to never raise, but defend the
        # MCP boundary anyway.
        return json.dumps(
            {"status": "error", "run_id": run_id, "error": str(e)},
            indent=2,
            ensure_ascii=False,
        )

    return json.dumps(result, indent=2, ensure_ascii=False)


@mcp.tool()
def replay_audit_agent(
    working_directory: str,
    run_id: str,
    agent_id: str,
    provider: str = "auto",
    timeout_seconds: int = 1800,
    model: str = "auto",
) -> str:
    """Re-run one agent from an existing feature audit.

    Use this when one audit agent times out, returns unparseable output, or
    needs to be retried without re-running the whole audit.

    Args:
        working_directory: Path to the project or any subdirectory within it.
        run_id: Audit run ID returned by audit_feature, or the explicit run_dir path.
        agent_id: Agent ID to replay, e.g. "agent_03".
        provider: "auto" (default; host-detected), "claude", "codex", or "none".
        timeout_seconds: Per-agent subprocess timeout.
        model: "auto" (default), explicit model name (e.g. "opus", "gpt-5.5"), or "none" to use the CLI default.
    """
    project_root = _resolve_root(working_directory)
    if project_root is None:
        return "No project root found."

    provider_normalized = (provider or "auto").strip().lower()
    if provider_normalized == "auto":
        provider_normalized = _detect_default_provider()

    model_normalized = (model or "").strip()
    if model_normalized.lower() in {"", "none", "default"}:
        model_normalized = None
    elif model_normalized.lower() == "auto":
        model_normalized = _detect_default_model(provider_normalized)

    try:
        from .agent_providers import run_agent_provider

        run_dir = _find_agent_audit_run(project_root, run_id)
        context_pack_path = run_dir / "context_pack.json"
        if not context_pack_path.exists():
            return f"Replay failed: missing context pack at {context_pack_path}"
        context_pack = json.loads(context_pack_path.read_text(encoding="utf-8"))
        agent = next(
            (item for item in context_pack.get("agents", []) if item.get("agent_id") == agent_id),
            None,
        )
        if agent is None:
            available = ", ".join(item.get("agent_id", "?") for item in context_pack.get("agents", []))
            return f"Replay failed: agent '{agent_id}' not found. Available agents: {available or 'none'}"

        result = run_agent_provider(
            provider=provider_normalized,
            project_root=project_root,
            run_dir=run_dir,
            context_pack_path=context_pack_path,
            agent=agent,
            timeout_seconds=timeout_seconds,
            model=model_normalized,
        )
    except Exception as e:
        return f"Replay failed: {e}"

    # Re-aggregate the merged report so the replayed agent's findings are reflected.
    try:
        from .agent_harness import write_merged_report
        all_results = []
        for findings_file in sorted(run_dir.glob("agent_*_findings.json")):
            stem = findings_file.stem.removesuffix("_findings")
            all_results.append({
                "agent_id": stem,
                "findings_path": str(findings_file),
                "status": "completed",
            })
        write_merged_report(run_dir, all_results)
    except Exception:
        pass

    # Update the persisted bundle.json so audit_status reflects the replayed
    # agent's new findings instead of the original (failed/stale) result.
    try:
        bundle_path = run_dir / "bundle.json"
        if bundle_path.exists():
            persisted = json.loads(bundle_path.read_text(encoding="utf-8"))
            agent_results_list = persisted.get("agent_results", [])
            replaced = False
            for i, ar in enumerate(agent_results_list):
                if ar.get("agent_id") == agent_id:
                    agent_results_list[i] = result
                    replaced = True
                    break
            if not replaced:
                agent_results_list.append(result)
            persisted["agent_results"] = agent_results_list
            bundle_path.write_text(
                json.dumps(persisted, indent=2, ensure_ascii=False) + "\n",
                encoding="utf-8",
            )
    except Exception:
        pass

    return json.dumps({
        "status": result.get("status"),
        "run_id": run_id,
        "run_dir": str(run_dir),
        "model": model_normalized,
        "agent_result": result,
    }, indent=2, ensure_ascii=False)


@mcp.tool()
def propose_patch(
    working_directory: str,
    run_id: str,
    agent_id: str,
    finding_index: int,
    provider: str = "auto",
    file_budget: int = 50_000,
    timeout_seconds: int = 600,
    model: str = "auto",
) -> str:
    """Dispatch one agent to propose a unified-diff patch for one audit finding.

    Reads the named finding from a completed audit run, loads referenced source
    files fresh from disk with a generous per-file budget (default 50K chars),
    dispatches a single agent to produce a unified diff, and runs
    `git apply --check` to test applicability.

    Does NOT apply the diff. Returns the diff path and applicability status.
    The caller runs `git apply <patch_dir>/patch.diff` if they want to apply it.

    Patch artifacts are written under the audit run directory:
        <run_dir>/patches/<agent_id>_finding_<index>_<timestamp>/
            prompt.md, raw_response.txt, patch.diff, result.json,
            stdout.txt, stderr.txt

    Args:
        working_directory: Path to the project or any subdirectory within it.
        run_id: Audit run ID returned by audit_feature, or the explicit run_dir path.
        agent_id: Agent ID whose finding should be patched (e.g. "agent_01").
        finding_index: Zero-based index into that agent's findings array.
        provider: "auto" (default; host-detected to match the calling CLI),
            "claude", or "codex". "none" is not supported — propose_patch
            requires a real provider.
        file_budget: Max characters per file in the diff prompt. Files larger
            than this are tail-truncated to a line boundary with the starting
            line number annotated so diff @@ headers stay accurate. Default 50000.
        timeout_seconds: Subprocess timeout for the diff-generation agent.
        model: "auto" (default), explicit model name (e.g. "opus", "sonnet", "gpt-5.5"), or "none" for the CLI default. With "auto" the codex provider parses ~/.codex/config.toml; the claude provider has no env-based detection and falls through to the CLI default unless explicit.
    """
    project_root = _resolve_root(working_directory)
    if project_root is None:
        return "No project root found."

    provider_normalized = (provider or "auto").strip().lower()
    if provider_normalized == "auto":
        provider_normalized = _detect_default_provider()
    if provider_normalized == "none":
        return "propose_patch requires a real provider; got 'none'. Use 'auto', 'claude', or 'codex'."

    model_normalized = (model or "").strip()
    if model_normalized.lower() in {"", "none", "default"}:
        model_normalized = None
    elif model_normalized.lower() == "auto":
        model_normalized = _detect_default_model(provider_normalized)

    try:
        from .propose_patch import propose_patch_for_finding

        run_dir = _find_agent_audit_run(project_root, run_id)
        result = propose_patch_for_finding(
            project_root=project_root,
            run_dir=run_dir,
            agent_id=agent_id,
            finding_index=finding_index,
            provider=provider_normalized,
            file_budget=file_budget,
            timeout_seconds=timeout_seconds,
            model=model_normalized,
        )
    except Exception as exc:
        return f"propose_patch failed: {exc}"

    return json.dumps(result, indent=2, ensure_ascii=False)


@mcp.tool()
def run_mmc(
    prompt: str,
    mode: str = "auto",
    provider: str = "auto",
    model: str = "auto",
    working_directory: str = "",
    timeout_seconds: int = 600,
) -> str:
    """Dispatch a multi-mode-consensus 3-lens parallel reasoning pass on demand.

    The complementary surface to the auto-surface MMC hook: where the hook
    fires automatically when a prompt's phrasing matches a topic or
    difficulty pattern, this tool runs MMC explicitly when the agent (or
    user, via tool call) decides the problem warrants parallel reasoning.

    Each sub-agent reasons about the same prompt with a different
    analytical lens (debug: recent changes / data flow / assumptions;
    planning: simplicity / robustness / performance) plus the
    diverge→converge→validate scaffold for that mode. Sub-agents run
    independently — that independence is what makes the cross-lens
    comparison signal meaningful.

    Returns the 3 raw responses for the calling agent to synthesize:
    identify where 2+ lenses converged on the same root cause / approach
    (high-confidence findings), and note unique conclusions from a single
    lens as lower-confidence.

    Args:
        prompt: The user's original problem statement, verbatim.
        mode: ``"auto"`` (default; uses the prompt-text intent detection
            and falls back to ``"planning"``), ``"debug"``, or ``"planning"``.
        provider: ``"auto"`` (default; host-detected to match the calling
            CLI), ``"claude"``, or ``"codex"``.
        model: ``"auto"`` (default), explicit model name, or ``"none"``.
        working_directory: Project root for codex's ``--cd``. Defaults to
            the resolved project root, or the current working directory
            when no project is detected.
        timeout_seconds: Per-sub-agent subprocess timeout.

    Returns the resolved mode, framings, and raw responses as a JSON string.
    """
    provider_normalized = (provider or "auto").strip().lower()
    if provider_normalized in {"", "auto"}:
        provider_normalized = _detect_default_provider()
    if provider_normalized == "none":
        return "run_mmc requires a real provider; got 'none'. Use 'auto', 'claude', or 'codex'."

    model_normalized: Optional[str] = (model or "").strip()
    if model_normalized.lower() in {"", "none", "default"}:
        model_normalized = None
    elif model_normalized.lower() == "auto":
        model_normalized = _detect_default_model(provider_normalized)

    if working_directory:
        project_root = _resolve_root(working_directory) or Path(working_directory)
    else:
        project_root = Path.cwd()

    from .mmc import run_mmc_consensus

    try:
        result = run_mmc_consensus(
            prompt,
            mode=mode,
            provider=provider_normalized,
            model=model_normalized,
            working_directory=project_root,
            timeout_seconds=timeout_seconds,
        )
    except ValueError as exc:
        return json.dumps(
            {"status": "invalid_request", "error": str(exc)},
            indent=2,
            ensure_ascii=False,
        )
    except Exception as exc:
        return json.dumps(
            {"status": "error", "error": str(exc)},
            indent=2,
            ensure_ascii=False,
        )

    return json.dumps(result, indent=2, ensure_ascii=False)


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
        _real_session_files(sessions_dir),
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
        is_summarized = not is_stub_overview(overview_text)
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
    wal_session_id = None
    try:
        from .hooks.session_recorder import _find_project_root, _devsession_dir
        sessions_dir = _devsession_dir(project_root)
        for wal in sorted(sessions_dir.glob(".hooks_wal_*.jsonl"), key=lambda p: p.stat().st_mtime, reverse=True):
            lines = wal.read_text(encoding="utf-8").strip().split("\n")
            if len(lines) < 2:
                continue
            # Header line carries the Claude session id. Capturing it lets
            # end_session identify this file by exact id instead of falling back to
            # a created_at window that any concurrent session also satisfies.
            try:
                wal_session_id = (json.loads(lines[0]) or {}).get("session_id") or None
            except Exception:
                wal_session_id = None
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
                    # Spec field on assistant messages (DEVSESSION_FORMAT.md); dropped at every
                    # flush site, so 0 of 87,379 stored messages carried it. Persisting it is what
                    # lets drift history survive a compaction.
                    if rec.get("session_signal"):
                        msg["session_signal"] = rec["session_signal"]
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
    if wal_session_id:
        session.metadata["claude_session_id"] = wal_session_id

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
    # Drop null/empty entries before building parallel range lists, so indices stay
    # aligned and no code_changes item is emitted with files: [None].
    files_changed = [f for f in (files_changed or []) if f]
    change_ranges = [_bm25_message_range(f, conv) for f in files_changed]
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
            "            if isinstance(item, dict):\n"
            "                item.pop('embedding', None)\n"
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
    session_id: str = "",
) -> str:
    """Update a specific unsummarized session with a structured summary.

    Call this when load_project_context indicates the previous session needs
    summarization. You should read the previous session's conversation first,
    analyze it, then call this with your structured analysis.

    ALWAYS pass session_id. load_project_context names the exact session in its
    ACTION REQUIRED block; pass that value back. Without it this falls back to
    "most recent unsummarized on disk", which can resolve to a different session
    than the one you read, and the summary lands on the wrong file.

    Args:
        working_directory: Path to the project.
        overview: 1-2 sentence summary of the previous session.
        decisions: Key technical decisions from the previous session.
        problems_solved: Problems that were solved.
        open_issues: Issues that remained open.
        next_steps: Planned next actions.
        files_changed: Files that were modified.
        session_id: Session stem (e.g. "07122026_1907") naming the exact target.
            Strongly recommended. Omit only when no target is known.
    """
    from .session.devsession import DevSession
    from .summarization.summary_schema import ensure_summary_span_links

    project_root = _resolve_root(working_directory)
    if project_root is None:
        return "No project root found."

    sessions_dir = _sessions_dir(project_root)

    target = None
    target_path = None

    if session_id:
        # Positive targeting: resolve the named session and refuse to write anywhere else.
        stem = session_id[:-len(".devsession")] if session_id.endswith(".devsession") else session_id
        candidate = sessions_dir / f"{stem}.devsession"
        if not candidate.exists():
            return f"Session not found: {stem}.devsession. Not writing (no fallback target was chosen)."
        try:
            s = DevSession.load(candidate)
        except Exception as e:
            return f"Could not load {candidate.name}: {e}. Not writing."
        if not _is_unsummarized(s):
            return (
                f"{candidate.name} already has a summary; refusing to overwrite. "
                "If you intended to replace it, clear summary in the file first."
            )
        target, target_path = s, candidate
    else:
        # Fallback: most recent unsummarized session on disk (excluding live snapshots).
        # Shares _MIN_SUMMARIZABLE_MESSAGES with load_project_context so this can never
        # select a session the announcer considered too short to mention.
        for sf in sorted(sessions_dir.glob("*.devsession"), key=lambda p: p.stat().st_mtime, reverse=True):
            if sf.name.startswith(".live_"):
                continue
            try:
                s = DevSession.load(sf)
                if (_is_unsummarized(s) and len(s.conversation) >= _MIN_SUMMARIZABLE_MESSAGES
                        and not _is_superseded_snapshot(s, sf, sessions_dir)):
                    target = s
                    target_path = sf
                    break
            except Exception:
                continue

    if target is None:
        return "No unsummarized session found."

    # Compare-and-swap: a background summarizer may have finished between the
    # ACTION REQUIRED announcement and this call. Re-read from disk and bail if
    # the target is no longer unsummarized, rather than clobbering fresh work.
    try:
        if not _is_unsummarized(DevSession.load(target_path)):
            return (
                f"{target_path.name} was summarized by another writer just now; "
                "refusing to overwrite. Re-read it before deciding whether to revise."
            )
    except Exception:
        pass

    timestamp = datetime.now().isoformat()
    conv_len = len(target.conversation)
    end_msg = f"msg_{conv_len:03d}"
    # Never emit code_changes with files: [None] (crashes downstream text composers).
    files_changed = [f for f in (files_changed or []) if f]

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
            "            if isinstance(item, dict):\n"
            "                item.pop('embedding', None)\n"
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
def doctor(working_directory: str, verbose: bool = False) -> str:
    """Check this project's memory integrity and report anything failing silently.

    Read-only. Surfaces the failure modes that otherwise produce no signal:
    sessions on disk that are missing from the search index, summary links that no
    longer resolve into their conversation, stale partial snapshots, checksums
    stranded on emptied structures, unflushed write-ahead logs, and feature-map
    boundary collisions.

    Run this when search results seem incomplete, when a session's history seems to
    have gone missing, or before relying on project memory for anything important.

    Args:
        working_directory: Path to the project or any subdirectory within it.
        verbose: Include every finding and the checks that passed.
    """
    from .doctor import run_diagnostics, format_report

    project_root = _resolve_root(working_directory)
    if project_root is None:
        return "No project root found."

    try:
        result = run_diagnostics(project_root)
    except Exception as e:
        return f"Diagnostics failed: {e}"

    return format_report(result, verbose=verbose)


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
    lines = [
        "Unified index rebuilt.",
        f"  Sessions: {sessions}",
        f"  Vectors: {total}",
        f"  Embedding: {model} ({dims}D)",
    ]
    # Sessions that failed to load are EXCLUDED from the index. Silently dropping
    # them made a checksum mismatch look like a clean rebuild, so surface them.
    skipped = index.get("skipped_sessions") or []
    if skipped:
        lines.append(f"  ⚠️  Skipped {len(skipped)} session(s) - NOT searchable:")
        for s in skipped[:10]:
            lines.append(f"       {s.get('file')}: {s.get('reason')}")
        if len(skipped) > 10:
            lines.append(f"       ... and {len(skipped) - 10} more")
    return "\n".join(lines)


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


def _find_sessions_with_item(sessions_dir: Path, item_id: str, session_id: str = ""):
    """Every session containing a given summary item ID.

    Summary item ids are sequential PER SESSION (dec_001, iss_000, ...), not
    globally unique, so the same id routinely exists in dozens of sessions.
    Returning all matches lets callers refuse an ambiguous write instead of
    silently editing whichever file the filesystem happened to yield first.
    """
    from .session.devsession import DevSession

    if session_id:
        stem = session_id[:-len(".devsession")] if session_id.endswith(".devsession") else session_id
        # Reject anything that is not a bare stem. Joining a caller-supplied string
        # onto sessions_dir let "../../other-project/devsession/x" resolve outside the
        # project and edit a different project's session.
        if not stem or "/" in stem or "\\" in stem or stem.startswith("."):
            return []
        candidate = (sessions_dir / f"{stem}.devsession").resolve()
        try:
            candidate.relative_to(sessions_dir.resolve())
        except ValueError:
            return []
        candidates = [candidate]
    else:
        candidates = [p for p in sorted(sessions_dir.glob("*.devsession"))
                      if not p.name.startswith(".live_")]

    matches = []
    for sf in candidates:
        if not sf.exists():
            continue
        try:
            s = DevSession.load(sf, verify_checksums=False)
        except Exception:
            continue
        if not s.summary:
            continue
        for cat in ("decisions", "code_changes", "problems_solved", "open_issues", "next_steps"):
            for item in s.summary.get(cat, []):
                if isinstance(item, dict) and item.get("id") == item_id:
                    matches.append((sf, s, cat, item))
    return matches


def _find_session_with_item(sessions_dir: Path, item_id: str, session_id: str = ""):
    """Locate the one .devsession containing an item ID, or nothing if ambiguous.

    Refusing on ambiguity is deliberate: picking the first match wrote a
    correction onto an unrelated session eight days older than the intended one.
    """
    matches = _find_sessions_with_item(sessions_dir, item_id, session_id)
    if len(matches) == 1:
        return matches[0]
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
    session_id: str = "",
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
        session_id: Session stem (e.g. "07122026_1907") identifying which session's
            item to edit. Summary item ids are sequential PER SESSION, not globally
            unique, so pass this whenever you know it. Without it, an id present in
            more than one session is refused rather than guessed at.
    """
    project_root = _resolve_root(working_directory)
    if project_root is None:
        return "No project root found."

    sessions_dir = _sessions_dir(project_root)
    matches = _find_sessions_with_item(sessions_dir, item_id, session_id)
    if not matches:
        where = f" in {session_id}" if session_id else " across any session"
        return f"Summary item '{item_id}' not found{where}."
    distinct_sessions = {m[0].stem for m in matches}
    if len(distinct_sessions) > 1:
        names = ", ".join(sorted(distinct_sessions)[:8])
        return (
            f"'{item_id}' exists in {len(distinct_sessions)} sessions ({names}). "
            "Refusing to guess which one you meant - pass session_id to choose."
        )
    sf, session, cat, item = matches[0]

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
    session_id: str = "",
) -> str:
    """Pin or unpin a summary item so context injection always includes it.

    Pinned items are surfaced at session start regardless of retrieval relevance —
    useful for "we always need to remember this" decisions or architectural rules
    that should guide every session. Respects the `locked` flag like edit_summary_item.

    Args:
        item_id: The summary item ID (e.g. "dec_000").
        working_directory: Path to the project.
        unpin: If True, remove the pin instead of adding one.
        session_id: Session stem identifying which session's item to pin. Item ids
            are sequential per session, so pass this when known; an ambiguous id is
            refused rather than guessed at.
    """
    project_root = _resolve_root(working_directory)
    if project_root is None:
        return "No project root found."

    sessions_dir = _sessions_dir(project_root)
    matches = _find_sessions_with_item(sessions_dir, item_id, session_id)
    if not matches:
        where = f" in {session_id}" if session_id else " across any session"
        return f"Summary item '{item_id}' not found{where}."
    distinct_sessions = {m[0].stem for m in matches}
    if len(distinct_sessions) > 1:
        names = ", ".join(sorted(distinct_sessions)[:8])
        return (
            f"'{item_id}' exists in {len(distinct_sessions)} sessions ({names}). "
            "Refusing to guess which one you meant - pass session_id to choose."
        )
    sf, session, cat, item = matches[0]

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
        # Same guard triple the other two "most recent unsummarized" readers use, so
        # this one cannot select a fragment or a superseded partial they refuse.
        for sf in _real_session_files(sessions_dir):
            try:
                s = DevSession.load(sf, verify_checksums=False)
                if (_is_unsummarized(s)
                        and len(s.conversation) >= _MIN_SUMMARIZABLE_MESSAGES
                        and not _is_superseded_snapshot(s, sf, sessions_dir)):
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
            "or __import__('reccli.session.devsession', fromlist=['x']).is_stub_summary(s.summary):\n"
            "    s.generate_summary()\n"
            "s.generate_embeddings(force=False, storage_mode='external')\n"
            "for span in s.spans:\n"
            "    span.pop('embedding', None)\n"
            "if s.summary:\n"
            "    for cat in ['decisions','code_changes','problems_solved','open_issues','next_steps']:\n"
            "        for item in s.summary.get(cat, []):\n"
            "            if isinstance(item, dict):\n"
            "                item.pop('embedding', None)\n"
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
