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
import re
import subprocess
import sys
import traceback
from datetime import datetime
from pathlib import Path
from typing import Optional, Dict, Any, List

from ..project.devproject import discover_project_root, default_devsession_path


# ---------------------------------------------------------------------------
# Issue logging — replaces silent `except: pass` in critical paths
# ---------------------------------------------------------------------------

def _log_issue(
    component: str,
    message: str,
    severity: str = "warning",
    project_root: Optional[Path] = None,
) -> None:
    """Append a structured issue to the project's issue log.

    Never raises — safe to call from any exception handler.
    Issues accumulate in <project>/devsession/.issues.jsonl and are
    surfaced via the list_issues MCP tool or SessionStart injection.
    """
    try:
        log_dir = (project_root / "devsession") if project_root else (Path.home() / ".reccli")
        log_dir.mkdir(parents=True, exist_ok=True)
        log_file = log_dir / ".issues.jsonl"
        record = {
            "timestamp": datetime.now().isoformat(),
            "component": component,
            "severity": severity,
            "message": message,
            "traceback": traceback.format_exc() if sys.exc_info()[0] else None,
        }
        with open(log_file, "a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
    except Exception:
        pass  # Last resort — can't log the logger


def get_issues(project_root: Path, max_items: int = 50) -> List[Dict[str, Any]]:
    """Read accumulated issues from the project's issue log."""
    log_file = project_root / "devsession" / ".issues.jsonl"
    if not log_file.exists():
        return []
    issues = []
    try:
        for line in log_file.read_text().strip().splitlines():
            try:
                issues.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    except Exception:
        pass
    return issues[-max_items:]


def clear_issues(project_root: Path) -> int:
    """Clear the issue log. Returns count of cleared issues."""
    log_file = project_root / "devsession" / ".issues.jsonl"
    if not log_file.exists():
        return 0
    try:
        count = sum(1 for _ in log_file.read_text().strip().splitlines())
        log_file.unlink()
        return count
    except Exception:
        return 0


# ---------------------------------------------------------------------------
# Background task registry — tracks detached subprocesses so they don't orphan
# ---------------------------------------------------------------------------

def _bg_tasks_file(project_root: Path) -> Path:
    return project_root / "devsession" / ".bg_tasks.jsonl"


def register_bg_task(project_root: Path, pid: int, purpose: str) -> None:
    """Record a spawned background subprocess for later reaping.

    Never raises — registry is best-effort.
    """
    try:
        f = _bg_tasks_file(project_root)
        f.parent.mkdir(parents=True, exist_ok=True)
        record = {
            "pid": pid,
            "purpose": purpose,
            "started_at": datetime.now().isoformat(),
        }
        with open(f, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(record) + "\n")
    except Exception:
        pass


def _pid_alive(pid: int) -> bool:
    """Return True if the given PID is still alive."""
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def cleanup_bg_tasks(project_root: Path, stale_hours: int = 24) -> int:
    """Reap dead background tasks from the registry.

    - Drops entries whose PIDs are no longer alive.
    - Drops entries older than stale_hours regardless of liveness (protection
      against PID reuse on very long-lived registries).
    - Returns count of entries removed.
    """
    f = _bg_tasks_file(project_root)
    if not f.exists():
        return 0
    try:
        lines = f.read_text().strip().splitlines()
    except Exception:
        return 0

    now = datetime.now()
    kept: List[str] = []
    removed = 0
    for line in lines:
        if not line.strip():
            continue
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            removed += 1
            continue
        pid = rec.get("pid")
        started_at = rec.get("started_at", "")
        try:
            age_hours = (now - datetime.fromisoformat(started_at)).total_seconds() / 3600
        except Exception:
            age_hours = 0
        if not isinstance(pid, int) or not _pid_alive(pid) or age_hours > stale_hours:
            removed += 1
            continue
        kept.append(line)

    try:
        if kept:
            f.write_text("\n".join(kept) + "\n", encoding="utf-8")
        else:
            f.unlink()
    except Exception:
        pass
    return removed


# ---------------------------------------------------------------------------
# Session-signal extraction (forward pointers)
# ---------------------------------------------------------------------------

_SESSION_SIGNAL_RE = re.compile(
    r'<!--session-signal:\s*'
    r'(?:goal=([^|]*)\|\s*)?'        # optional goal field
    r'resolved=([^|]*)\|\s*'
    r'open=(.*?)-->',                 # non-greedy: handles > in values
    re.IGNORECASE,
)


def _extract_session_signal(message: str) -> Optional[Dict[str, Any]]:
    """Parse a session-signal tag from an assistant message.

    Supports both formats:
      <!--session-signal: goal=X | resolved=Y | open=Z-->
      <!--session-signal: resolved=Y | open=Z-->
    """
    match = _SESSION_SIGNAL_RE.search(message)
    if not match:
        return None
    goal_raw = (match.group(1) or "").strip()
    resolved_raw = match.group(2).strip()
    open_raw = match.group(3).strip()
    signal: Dict[str, Any] = {
        "resolved": [t.strip() for t in resolved_raw.split(",") if t.strip()],
        "open": [t.strip() for t in open_raw.split(",") if t.strip()],
    }
    if goal_raw:
        signal["goal"] = goal_raw
    return signal


def _strip_session_signal(message: str) -> str:
    """Remove the session-signal tag from message content."""
    return _SESSION_SIGNAL_RE.sub('', message).rstrip()


def get_latest_signal(project_root: Path) -> Optional[Dict[str, Any]]:
    """Read the most recent session_signal from the current (newest) WAL.

    Only checks the single most-recently-modified WAL file to avoid
    returning stale signals from a previous session.
    """
    sessions_dir = project_root / "devsession"
    if not sessions_dir.exists():
        return None
    wals = sorted(
        sessions_dir.glob(".hooks_wal_*.jsonl"),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    if not wals:
        return None
    # Only check the newest WAL (current session)
    wal = wals[0]
    try:
        lines = wal.read_text().strip().splitlines()
    except Exception:
        return None
    for line in reversed(lines):
        try:
            record = json.loads(line)
        except Exception:
            continue
        if record.get("session_signal"):
            return record["session_signal"]
    return None


# ---------------------------------------------------------------------------


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


def _recover_orphan_wals(project_root: Path, current_session_id: str) -> None:
    """Finalize WALs from previous sessions that never got a clean SessionEnd.

    This handles crashes, force-quits, and hook failures that left WALs behind.
    Called at the start of each new session.
    """
    sessions_dir = _devsession_dir(project_root)
    for wal_file in sessions_dir.glob(".hooks_wal_*.jsonl"):
        # Skip the current session's WAL
        wal_sid = wal_file.stem.replace(".hooks_wal_", "")
        if wal_sid == current_session_id:
            continue

        try:
            lines = wal_file.read_text(encoding="utf-8").strip().split("\n")
            if len(lines) < 2:
                wal_file.unlink(missing_ok=True)
                continue

            header = json.loads(lines[0])
            records = []
            for line in lines[1:]:
                if line.strip():
                    try:
                        records.append(json.loads(line))
                    except json.JSONDecodeError:
                        continue

            if not records:
                wal_file.unlink(missing_ok=True)
                continue

            # Build conversation
            from ..session.devsession import DevSession

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

            session = DevSession(session_id=wal_sid)
            session.metadata["working_directory"] = header.get("working_directory", "")
            session.metadata["project_root"] = str(project_root)
            session.metadata["source"] = "claude_code_hooks_recovered"
            session.metadata["claude_session_id"] = wal_sid
            session.conversation = conversation

            started_at = datetime.fromisoformat(header["started_at"])
            output_path = default_devsession_path(project_root, timestamp=started_at)
            session.save(output_path, skip_validation=True)

            # Clean up
            wal_file.unlink(missing_ok=True)
            live = sessions_dir / f".live_{wal_sid}.devsession"
            live.unlink(missing_ok=True)

            # Background finalize (summarize + embed + index)
            if len(conversation) >= 4:
                _spawn_background_finalize(output_path)

        except Exception:
            _log_issue(
                "session_recorder/orphan_recovery",
                f"Failed to recover orphan WAL: {wal_file.name}",
                severity="warning",
                project_root=project_root,
            )
            continue


def _recover_all_registered_projects(current_session_id: str) -> None:
    """Run orphan WAL recovery across all registered projects.

    Called when cwd is outside any project (common: cwd=/Users/will).
    Uses the project registry to find all known projects and recover
    their orphaned WALs.
    """
    registry = Path.home() / ".reccli" / "projects.json"
    if not registry.exists():
        return
    try:
        data = json.loads(registry.read_text())
        projects = data.get("projects", []) if isinstance(data, dict) else data
    except Exception:
        return
    for entry in projects:
        project_path = Path(entry.get("path", ""))
        if project_path.exists():
            try:
                _recover_orphan_wals(project_path, current_session_id)
            except Exception:
                _log_issue(
                    "session_recorder/orphan_recovery",
                    f"Failed registry-based recovery for {project_path.name}",
                    severity="warning",
                    project_root=project_path,
                )


def start_session(session_id: str, cwd: str) -> None:
    """Create a new WAL file for this Claude Code session."""
    project_root = _find_project_root(cwd, session_id)

    if project_root is None:
        # cwd is outside any project — still recover orphans via registry
        try:
            _recover_all_registered_projects(session_id)
        except Exception:
            pass
        return

    # Recover orphaned WALs from previous sessions that didn't get a clean end
    try:
        _recover_orphan_wals(project_root, session_id)
    except Exception:
        _log_issue(
            "session_recorder/start_session",
            "Failed orphan WAL recovery",
            severity="warning",
            project_root=project_root,
        )

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


def _ensure_wal(session_id: str, cwd: str) -> Optional[Path]:
    """Find project root and ensure WAL exists. Creates it lazily if needed."""
    project_root = _find_project_root(cwd, session_id)
    if project_root is None:
        return None

    wal = _wal_path(project_root, session_id)
    if not wal.exists():
        # Lazy WAL creation — covers cases where SessionStart fired before
        # load_project_context set the breadcrumb (cwd not inside project).
        header = {
            "format": "reccli-hooks-wal",
            "version": 1,
            "session_id": session_id,
            "started_at": datetime.now().isoformat(),
            "working_directory": cwd,
            "project_root": str(project_root),
        }
        _append_to_wal(wal, header)
    return wal


def record_user_prompt(session_id: str, prompt: str, cwd: str) -> None:
    """Append a user prompt to the active session WAL."""
    wal = _ensure_wal(session_id, cwd)
    if wal is None:
        return

    _append_to_wal(wal, {
        "type": "user_prompt",
        "timestamp": datetime.now().isoformat(),
        "role": "user",
        "content": prompt,
    })


def record_assistant_response(session_id: str, message: str, cwd: str) -> None:
    """Append an assistant response to the active session WAL.

    If a session-signal tag is present, it is extracted into a separate
    field and stripped from the stored content.
    """
    wal = _ensure_wal(session_id, cwd)
    if wal is None:
        return

    signal = _extract_session_signal(message)
    clean_message = _strip_session_signal(message) if signal else message

    record = {
        "type": "assistant_response",
        "timestamp": datetime.now().isoformat(),
        "role": "assistant",
        "content": clean_message,
    }
    if signal:
        record["session_signal"] = signal

    _append_to_wal(wal, record)


def record_tool_use(
    session_id: str,
    tool_name: str,
    tool_input: Any,
    tool_response: Any,
    cwd: str,
) -> None:
    """Append a tool use event to the active session WAL."""
    wal = _ensure_wal(session_id, cwd)
    if wal is None:
        return

    input_str = json.dumps(tool_input, ensure_ascii=False) if tool_input else ""
    response_str = json.dumps(tool_response, ensure_ascii=False) if tool_response else ""

    # For Edit/Write tools: store the diff inline (small) and full response
    # in a sidecar field. The full response contains complete file content
    # needed for recovery (e.g. env files), but bloats .devsession files
    # (~45KB per edit vs ~2KB for just the diff).
    full_response = None
    if tool_name in ("Edit", "Write", "edit", "write"):
        # Always stash the full response for artifact extraction
        full_response = response_str
        # Build a compact inline representation: just file_path + diff
        compact_input = {}
        if tool_input:
            for key in ("file_path", "path", "old_string", "new_string", "content"):
                if key in tool_input:
                    val = tool_input[key]
                    # Truncate long strings in the inline version
                    if isinstance(val, str) and len(val) > 500:
                        compact_input[key] = val[:500] + f"...[{len(val)} chars, full in sidecar]"
                    else:
                        compact_input[key] = val
        input_str = json.dumps(compact_input, ensure_ascii=False) if compact_input else input_str
        # Compact response: just success/failure indicator
        try:
            resp_data = json.loads(response_str) if response_str else {}
            if isinstance(resp_data, dict):
                compact_resp = {k: resp_data[k] for k in ("success", "error", "message") if k in resp_data}
                if compact_resp:
                    response_str = json.dumps(compact_resp, ensure_ascii=False)
                else:
                    response_str = response_str[:200] + (f"...[{len(full_response)} chars in sidecar]" if len(response_str) > 200 else "")
            else:
                response_str = response_str[:200] + (f"...[{len(full_response)} chars in sidecar]" if len(response_str) > 200 else "")
        except (json.JSONDecodeError, TypeError):
            response_str = response_str[:200] + (f"...[{len(full_response)} chars in sidecar]" if len(response_str) > 200 else "")
    else:
        # Non-Edit tools: for very large outputs (>50KB), keep preview + sidecar
        _LARGE_THRESHOLD = 50_000
        if len(input_str) > _LARGE_THRESHOLD:
            full_input_str = input_str
            input_str = input_str[:2000] + f"...[full content in full_input, {len(full_input_str)} chars]"
        if len(response_str) > _LARGE_THRESHOLD:
            full_response = response_str
            response_str = response_str[:4000] + f"...[full content in full_response, {len(full_response)} chars]"

    record = {
        "type": "tool_use",
        "timestamp": datetime.now().isoformat(),
        "role": "tool",
        "tool_name": tool_name,
        "content": f"{tool_name}: {input_str}\n→ {response_str}",
    }
    if full_response:
        record["full_response"] = full_response

    _append_to_wal(wal, record)


def _extract_file_artifacts(records: list, output_dir: Path, session_id: str) -> Optional[Path]:
    """Extract file snapshots from Edit/Write tool results into a sidecar artifacts file.

    Scans WAL records for tool_use events where the tool is Edit or Write,
    and extracts the file path + full file content for point-in-time recovery.
    The full_response field contains the complete tool response (including file
    content) that was stripped from the inline .devsession content to save space.

    Returns path to artifacts file, or None if no artifacts found.
    """
    import re

    artifacts = []

    for rec in records:
        if rec.get("type") != "tool_use":
            continue
        tool_name = rec.get("tool_name", "")
        content = rec.get("content", "")
        full_response = rec.get("full_response") or ""
        timestamp = rec.get("timestamp", "")

        if tool_name not in ("Edit", "Write", "edit", "write"):
            continue

        # Extract file_path from inline content
        file_path = None
        try:
            parts = content.split("\n→ ", 1)
            if len(parts) >= 1:
                input_part = parts[0].replace(f"{tool_name}: ", "", 1)
                try:
                    input_data = json.loads(input_part)
                    file_path = input_data.get("file_path") or input_data.get("path")
                except json.JSONDecodeError:
                    match = re.search(r'"file_path"\s*:\s*"([^"]+)"', input_part)
                    if match:
                        file_path = match.group(1)
        except Exception:
            pass

        if not file_path:
            continue

        # Extract full file content from full_response sidecar field
        file_content = None
        if full_response:
            try:
                resp_data = json.loads(full_response)
                if isinstance(resp_data, dict):
                    # Claude Code Edit response has originalFile + oldString/newString.
                    # Reconstruct post-edit content by applying the replacement.
                    original = resp_data.get("originalFile")
                    old_str = resp_data.get("oldString")
                    new_str = resp_data.get("newString")
                    if original and old_str is not None and new_str is not None:
                        file_content = original.replace(old_str, new_str, 1)
                    elif original:
                        file_content = original
                    else:
                        # Fallback: check other common keys
                        file_content = (
                            resp_data.get("new_content")
                            or resp_data.get("content")
                            or resp_data.get("file_content")
                            or resp_data.get("text")
                        )
                elif isinstance(resp_data, str):
                    file_content = resp_data
            except (json.JSONDecodeError, TypeError):
                # full_response might be raw text
                if len(full_response) > 100:
                    file_content = full_response

        artifact = {
            "type": "file_snapshot",
            "tool": tool_name,
            "file_path": file_path,
            "timestamp": timestamp,
        }
        if file_content:
            artifact["file_content"] = file_content
        else:
            # Fallback: store the full response as-is for manual recovery
            artifact["raw_response"] = full_response[:100_000] if full_response else None

        artifacts.append(artifact)

    if not artifacts:
        return None

    artifacts_path = output_dir / f".artifacts_{session_id}.json"
    with open(artifacts_path, "w", encoding="utf-8") as f:
        json.dump({
            "session_id": session_id,
            "extracted_at": datetime.now().isoformat(),
            "artifact_count": len(artifacts),
            "artifacts": artifacts,
        }, f, indent=2, ensure_ascii=False)

    return artifacts_path


def flush_active_wals(project_root: Path) -> list:
    """Snapshot all active WAL files into .devsession files for mid-session search.

    Unlike end_session, this does NOT delete the WAL — the session is still
    recording. It writes/overwrites a live snapshot .devsession so search can
    find current-session messages.

    Uses a lock file to prevent concurrent flushes from racing.
    """
    from ..session.devsession import DevSession

    sessions_dir = _devsession_dir(project_root)
    lock_file = sessions_dir / ".flush_lock"

    # Skip if another flush is in progress (non-blocking)
    try:
        if lock_file.exists():
            # Stale lock check — if lock is older than 30s, remove it
            lock_age = datetime.now().timestamp() - lock_file.stat().st_mtime
            if lock_age < 30:
                return []  # Another flush is running
            lock_file.unlink(missing_ok=True)
        lock_file.touch()
    except Exception:
        pass

    flushed = []
    try:
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
                if rec.get("full_response"):
                    msg["tool_response"] = rec["full_response"]
                conversation.append(msg)

            session = DevSession(session_id=sid)
            session.metadata["working_directory"] = header.get("working_directory", "")
            session.metadata["project_root"] = str(project_root)
            session.metadata["source"] = "claude_code_hooks_live"
            session.metadata["claude_session_id"] = sid
            session.conversation = conversation

            # Embed any messages that don't have embeddings yet
            try:
                from ..retrieval.embeddings import get_embedding_provider
                provider = get_embedding_provider()
                to_embed = []
                to_embed_indices = []
                for i, msg in enumerate(session.conversation):
                    if msg.get("deleted") or "embedding" in msg:
                        continue
                    to_embed.append(msg)
                    to_embed_indices.append(i)

                if to_embed:
                    texts = [m["content"] for m in to_embed]
                    embeddings = provider.embed_batch(texts)
                    embed_ts = datetime.now().isoformat()
                    for msg, emb in zip(to_embed, embeddings):
                        msg["embedding"] = emb
                        msg["embed_model"] = provider.model_name
                        msg["embed_provider"] = provider.provider_name
                        msg["embed_dim"] = provider.dimensions
                        msg["embed_ts"] = embed_ts
                        msg["text_hash"] = provider.compute_text_hash(msg["content"])
            except Exception:
                pass  # Fall back to BM25-only search

            # Write to a stable snapshot path keyed by session_id (overwrites on each flush)
            snapshot_path = sessions_dir / f".live_{sid}.devsession"
            session.save(snapshot_path, skip_validation=True)
            flushed.append(snapshot_path)
    finally:
        lock_file.unlink(missing_ok=True)

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
        if rec.get("full_response"):
            msg["tool_response"] = rec["full_response"]
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
        started_at = datetime.fromisoformat(header.get("started_at", ""))
        output_path = default_devsession_path(project_root, timestamp=started_at)
        session.save(output_path, skip_validation=True)

    # Extract file artifacts (Edit/Write snapshots) before cleaning up WAL
    try:
        _extract_file_artifacts(records, sessions_dir, session_id)
    except Exception:
        pass

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
        "# Strip inline embeddings from spans and summary items (indexed, not needed inline)\n"
        "for span in s.spans:\n"
        "    span.pop('embedding', None)\n"
        "if s.summary:\n"
        "    for cat in ['decisions','code_changes','problems_solved','open_issues','next_steps']:\n"
        "        for item in s.summary.get(cat, []):\n"
        "            item.pop('embedding', None)\n"
        "    changed = True\n"
        "if changed:\n"
        "    s.save(path)\n"
        "    from reccli.retrieval.vector_index import build_unified_index\n"
        "    build_unified_index(path.parent, verbose=False)\n"
    )
    try:
        proc = subprocess.Popen(
            [sys.executable, "-c", script, str(session_path)],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
        register_bg_task(
            _find_project_root(Path(str(session_path))) or Path(str(session_path)).parent.parent,
            proc.pid,
            "end_session_summarize",
        )
    except Exception as e:
        _log_issue("session_recorder", f"background summarize spawn failed: {e}", severity="warning")
