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


def _find_project_root(cwd: str) -> Optional[Path]:
    resolved = Path(cwd).resolve()
    return discover_project_root(resolved)


def _append_to_wal(wal_file: Path, record: Dict[str, Any]) -> None:
    """Append a single JSON record to the WAL. Fsync for crash safety."""
    with open(wal_file, "a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")
        f.flush()
        os.fsync(f.fileno())


def start_session(session_id: str, cwd: str) -> None:
    """Create a new WAL file for this Claude Code session."""
    project_root = _find_project_root(cwd)
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
    project_root = _find_project_root(cwd)
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
    project_root = _find_project_root(cwd)
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
    project_root = _find_project_root(cwd)
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


def end_session(session_id: str, cwd: str) -> Optional[Path]:
    """Finalize the WAL into a .devsession file.

    Must complete within ~1.5s (SessionEnd hook timeout).
    Summarization and indexing are deferred to next search or explicit command.
    """
    project_root = _find_project_root(cwd)
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

    # Build .devsession
    from ..session.devsession import DevSession

    session = DevSession(session_id=session_id)
    session.metadata["working_directory"] = header.get("working_directory", cwd)
    session.metadata["project_root"] = str(project_root)
    session.metadata["source"] = "claude_code_hooks"
    session.metadata["claude_session_id"] = session_id
    session.conversation = conversation

    # Save (fast — no validation needed for hooks-recorded sessions)
    output_path = default_devsession_path(project_root)
    session.save(output_path, skip_validation=True)

    # Clean up WAL and live snapshot
    wal.unlink(missing_ok=True)
    live_snapshot = _devsession_dir(project_root) / f".live_{session_id}.devsession"
    live_snapshot.unlink(missing_ok=True)

    return output_path
