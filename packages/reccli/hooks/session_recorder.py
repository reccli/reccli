"""
Hooks-based session recorder for Claude Code.

Records conversation messages from Claude Code hook events into a WAL file,
then finalizes into a .devsession file on session end.

WAL format (one JSON object per line):
  Line 0: header  {"format": "reccli-hooks-wal", "version": 1, ...}
  Lines 1+: message records  {"type": "user_prompt"|"assistant_response"|"tool_use", ...}
"""

import json
import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Optional, Dict, Any

from ..project.devproject import discover_project_root, default_devsession_path


def _devsession_dir(project_root: Path) -> Path:
    d = project_root / "devsession"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _wal_path(project_root: Path, session_id: str) -> Path:
    return _devsession_dir(project_root) / f".hooks_wal_{session_id}.jsonl"


ACTIVE_PROJECT_DIR = Path.home() / ".reccli" / "active_sessions"


def set_active_project(session_id: str, project_root: Path) -> None:
    """Mark a project as active for this Claude Code session.

    Called by load_project_context so hooks know which project to record to
    even when cwd is not inside the project.
    """
    ACTIVE_PROJECT_DIR.mkdir(parents=True, exist_ok=True)
    breadcrumb = ACTIVE_PROJECT_DIR / f"{session_id}.json"
    with open(breadcrumb, "w") as f:
        json.dump({"project_root": str(project_root.resolve())}, f)


def _find_project_root(cwd: str, session_id: str = "") -> Optional[Path]:
    """Find project root from cwd, or from the active session breadcrumb."""
    resolved = Path(cwd).resolve()
    root = discover_project_root(resolved)
    if root:
        return root

    # Check if a project was loaded via MCP for this session
    if session_id:
        breadcrumb = ACTIVE_PROJECT_DIR / f"{session_id}.json"
        if breadcrumb.exists():
            try:
                data = json.loads(breadcrumb.read_text())
                p = Path(data["project_root"])
                if p.exists():
                    return p
            except Exception:
                pass

    # Check any active session breadcrumb (fallback for hooks without session_id)
    if ACTIVE_PROJECT_DIR.exists():
        for bc in sorted(ACTIVE_PROJECT_DIR.glob("*.json"), key=lambda f: f.stat().st_mtime, reverse=True):
            try:
                data = json.loads(bc.read_text())
                p = Path(data["project_root"])
                if p.exists():
                    return p
            except Exception:
                continue

    return None


def _append_to_wal(wal_file: Path, record: Dict[str, Any]) -> None:
    """Append a single JSON record to the WAL. Fsync for crash safety."""
    with open(wal_file, "a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")
        f.flush()
        os.fsync(f.fileno())


# Approximate tokens per byte in conversational text (conservative estimate)
_BYTES_PER_TOKEN = 4
_PRECOMPACT_TOKEN_THRESHOLD = 400_000
_PRECOMPACT_BYTE_THRESHOLD = _PRECOMPACT_TOKEN_THRESHOLD * _BYTES_PER_TOKEN  # ~1.6MB WAL
_REMINDER_SENT_SUFFIX = ".precompact_reminded"


def check_precompaction_threshold(session_id: str, cwd: str) -> Optional[str]:
    """Check if the WAL is approaching the compaction threshold.

    Returns a reminder string to inject into Claude's context if the session
    is large enough to warrant a pre-compaction save. Only fires once per session.
    """
    project_root = _find_project_root(cwd, session_id)
    if project_root is None:
        return None

    wal = _wal_path(project_root, session_id)
    if not wal.exists():
        return None

    # Don't remind twice
    reminder_flag = wal.with_suffix(_REMINDER_SENT_SUFFIX)
    if reminder_flag.exists():
        return None

    try:
        wal_size = wal.stat().st_size
    except Exception:
        return None

    if wal_size < _PRECOMPACT_BYTE_THRESHOLD:
        return None

    # Mark as reminded
    try:
        reminder_flag.touch()
    except Exception:
        pass

    approx_tokens = wal_size // _BYTES_PER_TOKEN
    return (
        f"[RecCli] This session has ~{approx_tokens:,} tokens recorded. "
        "Context compaction may happen soon. To preserve your work with full context, "
        "please call save_session_notes now to capture decisions, code changes, and "
        "problems solved this session. This also updates the .devproject feature map. "
        "After saving, you can continue working normally."
    )


def start_session(session_id: str, cwd: str) -> None:
    """Create a new WAL file for this Claude Code session."""
    project_root = _find_project_root(cwd, session_id)
    if project_root is None:
        return

    wal = _wal_path(project_root, session_id)
    if wal.exists():
        return  # Already started (session resume)

    header = {
        "format": "reccli-hooks-wal",
        "version": 1,
        "session_id": session_id,
        "started_at": datetime.now().isoformat(),
        "working_directory": cwd,
        "project_root": str(project_root),
    }
    _append_to_wal(wal, header)


def record_user_prompt(session_id: str, prompt: str, cwd: str) -> None:
    """Append a user prompt to the active session WAL."""
    project_root = _find_project_root(cwd, session_id)
    if project_root is None:
        return

    wal = _wal_path(project_root, session_id)
    if not wal.exists():
        # Auto-start if hook fired before SessionStart (e.g. resume)
        start_session(session_id, cwd)

    _append_to_wal(wal, {
        "type": "user_prompt",
        "timestamp": datetime.now().isoformat(),
        "role": "user",
        "content": prompt,
    })


def record_assistant_response(session_id: str, message: str, cwd: str) -> None:
    """Append an assistant response to the active session WAL."""
    project_root = _find_project_root(cwd, session_id)
    if project_root is None:
        return

    wal = _wal_path(project_root, session_id)
    if not wal.exists():
        return

    _append_to_wal(wal, {
        "type": "assistant_response",
        "timestamp": datetime.now().isoformat(),
        "role": "assistant",
        "content": message,
    })


def record_tool_use(
    session_id: str,
    tool_name: str,
    tool_input: Any,
    tool_response: Any,
    cwd: str,
) -> None:
    """Append a tool use event to the active session WAL."""
    project_root = _find_project_root(cwd, session_id)
    if project_root is None:
        return

    wal = _wal_path(project_root, session_id)
    if not wal.exists():
        return

    # Truncate large tool responses to keep WAL manageable
    input_str = json.dumps(tool_input, ensure_ascii=False) if tool_input else ""
    response_str = json.dumps(tool_response, ensure_ascii=False) if tool_response else ""
    if len(input_str) > 1000:
        input_str = input_str[:1000] + "...[truncated]"
    if len(response_str) > 2000:
        response_str = response_str[:2000] + "...[truncated]"

    _append_to_wal(wal, {
        "type": "tool_use",
        "timestamp": datetime.now().isoformat(),
        "role": "tool",
        "tool_name": tool_name,
        "content": f"{tool_name}: {input_str}\n→ {response_str}",
    })


def flush_active_wals(project_root: Path) -> list:
    """Snapshot all active WAL files into .devsession files for mid-session search.

    Unlike end_session, this does NOT delete the WAL — the session is still
    recording. It writes/overwrites a live snapshot .devsession so search can
    find current-session messages.
    """
    from ..session.devsession import DevSession

    sessions_dir = _devsession_dir(project_root)
    flushed = []

    for wal in sessions_dir.glob(".hooks_wal_*.jsonl"):
        try:
            lines = wal.read_text(encoding="utf-8").strip().split("\n")
        except Exception:
            continue

        if len(lines) < 2:
            continue

        try:
            header = json.loads(lines[0])
        except json.JSONDecodeError:
            continue

        records = []
        for line in lines[1:]:
            if line.strip():
                try:
                    records.append(json.loads(line))
                except json.JSONDecodeError:
                    continue

        if not records:
            continue

        sid = header.get("session_id", wal.stem)
        conversation = []
        for rec in records:
            msg = {
                "role": rec.get("role", "system"),
                "content": rec.get("content", ""),
                "timestamp": rec.get("timestamp", ""),
            }
            if rec.get("tool_name"):
                msg["tool_name"] = rec["tool_name"]
            conversation.append(msg)

        session = DevSession(session_id=sid)
        session.metadata["working_directory"] = header.get("working_directory", "")
        session.metadata["project_root"] = str(project_root)
        session.metadata["source"] = "claude_code_hooks_live"
        session.metadata["claude_session_id"] = sid
        session.conversation = conversation

        # Write to a stable snapshot path keyed by session_id (overwrites on each flush)
        snapshot_path = sessions_dir / f".live_{sid}.devsession"
        session.save(snapshot_path, skip_validation=True)
        flushed.append(snapshot_path)

    return flushed


def compact_session(session_id: str, cwd: str) -> Optional[Path]:
    """Flush WAL to .devsession at compaction time. WAL keeps recording.

    Unlike end_session, this:
    - Saves a .devsession from the current WAL contents
    - Spawns background summarization
    - Does NOT delete the WAL (session continues)
    """
    project_root = _find_project_root(cwd, session_id)
    if project_root is None:
        return None

    # Flush WAL to live snapshot first
    flushed = flush_active_wals(project_root)
    if not flushed:
        return None

    # Convert live snapshot to a real .devsession
    sessions_dir = _devsession_dir(project_root)
    for snapshot in flushed:
        from ..session.devsession import DevSession
        try:
            session = DevSession.load(snapshot)
        except Exception:
            continue

        output_path = default_devsession_path(project_root)
        session.save(output_path, skip_validation=True)

        # Background summarize the compacted session
        if len(session.conversation) >= 4:
            _spawn_background_summarize(output_path)

        return output_path

    return None


def end_session(session_id: str, cwd: str) -> Optional[Path]:
    """Finalize the WAL into a .devsession file.

    Must complete within ~1.5s (SessionEnd hook timeout).
    Summarization and indexing are deferred to next search or explicit command.
    """
    project_root = _find_project_root(cwd, session_id)
    if project_root is None:
        return None

    wal = _wal_path(project_root, session_id)
    if not wal.exists():
        return None

    # Read WAL
    try:
        lines = wal.read_text(encoding="utf-8").strip().split("\n")
    except Exception:
        return None

    if len(lines) < 2:
        # Header only, no messages recorded
        wal.unlink(missing_ok=True)
        return None

    header = json.loads(lines[0])
    records = []
    for line in lines[1:]:
        if line.strip():
            try:
                records.append(json.loads(line))
            except json.JSONDecodeError:
                continue

    if not records:
        wal.unlink(missing_ok=True)
        return None

    # Build conversation array from WAL records
    conversation = []
    for rec in records:
        msg = {
            "role": rec.get("role", "system"),
            "content": rec.get("content", ""),
            "timestamp": rec.get("timestamp", ""),
        }
        if rec.get("tool_name"):
            msg["tool_name"] = rec["tool_name"]
        conversation.append(msg)

    # Check if save_session_notes already created a .devsession with a summary.
    # If so, merge the full WAL conversation into it instead of creating a new file.
    from ..session.devsession import DevSession

    sessions_dir = _devsession_dir(project_root)
    existing_session = None
    existing_path = None
    for sf in sorted(sessions_dir.glob("*.devsession"), key=lambda p: p.stat().st_mtime, reverse=True):
        try:
            candidate = DevSession.load(sf)
            if candidate.summary and candidate.summary.get("overview", "").strip():
                # Found a recent file with a real summary — merge into it
                existing_session = candidate
                existing_path = sf
                break
        except Exception:
            continue

    if existing_session and len(conversation) > len(existing_session.conversation):
        # Merge: keep the summary/spans, replace conversation with the full WAL
        existing_session.conversation = conversation
        existing_session.metadata["source"] = "claude_code_hooks"
        existing_session.metadata["claude_session_id"] = session_id
        existing_session.save(existing_path, skip_validation=True)
        output_path = existing_path
    else:
        # No existing summary — create new file
        session = DevSession(session_id=session_id)
        session.metadata["working_directory"] = header.get("working_directory", cwd)
        session.metadata["project_root"] = str(project_root)
        session.metadata["source"] = "claude_code_hooks"
        session.metadata["claude_session_id"] = session_id
        session.conversation = conversation
        output_path = default_devsession_path(project_root)
        session.save(output_path, skip_validation=True)

    # Clean up WAL and live snapshot
    wal.unlink(missing_ok=True)
    live_snapshot = _devsession_dir(project_root) / f".live_{session_id}.devsession"
    live_snapshot.unlink(missing_ok=True)
    reminder_flag = wal.with_suffix(_REMINDER_SENT_SUFFIX)
    reminder_flag.unlink(missing_ok=True)

    # Spawn background: summarize (if no summary yet) + embed + index
    if len(conversation) >= 4:
        _spawn_background_finalize(output_path)

    return output_path


def _spawn_background_finalize(session_path: Path) -> None:
    """Spawn a detached process to summarize (if needed) + embed all layers + index."""
    script = (
        "import sys\n"
        "from pathlib import Path\n"
        "path = Path(sys.argv[1])\n"
        "from reccli.session.devsession import DevSession\n"
        "s = DevSession.load(path)\n"
        "if not s.conversation:\n"
        "    sys.exit(0)\n"
        "changed = False\n"
        "# Summarize only if no summary exists\n"
        "if not s.summary or s.summary.get('overview','') in ('','Session summarized without LLM','Placeholder summary'):\n"
        "    s.generate_summary()\n"
        "    changed = True\n"
        "# Always embed — catches new messages from WAL merge\n"
        "count = s.generate_embeddings(force=False, storage_mode='external')\n"
        "if count > 0:\n"
        "    changed = True\n"
        "if changed:\n"
        "    s.save(path)\n"
        "    from reccli.retrieval.vector_index import build_unified_index\n"
        "    build_unified_index(path.parent, verbose=False)\n"
    )
    try:
        subprocess.Popen(
            [sys.executable, "-c", script, str(session_path)],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
    except Exception:
        pass
