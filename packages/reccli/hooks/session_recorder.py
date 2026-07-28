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


def _min_summarizable() -> int:
    """Shared summarizable-length bound; see session.devsession."""
    from ..session.devsession import MIN_SUMMARIZABLE_MESSAGES
    return MIN_SUMMARIZABLE_MESSAGES


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



_FAILURE_MARKERS = ("Traceback (most recent call last)", "Error:", "Exception:",
                    "CRITICAL", "FATAL")


def _looks_like_failure(text: str) -> bool:
    """True if background-writer stderr indicates an actual failure.

    The finalize subprocess also prints normal progress to stderr, so presence of
    output alone means nothing.
    """
    return any(marker in text for marker in _FAILURE_MARKERS)


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

    # Promote background-writer stderr into the issue log before reaping. Capturing
    # stderr to a file only helps if something reads it; otherwise it is the same
    # silence in a different location.
    # Never reap while a background writer is still running: its sidecar is an open
    # file handle, and deleting it loses whatever the process has yet to write.
    any_alive = False
    try:
        for line in lines:
            try:
                rec = json.loads(line)
            except Exception:
                continue
            if isinstance(rec.get("pid"), int) and _pid_alive(rec["pid"]):
                any_alive = True
                break
    except Exception:
        pass

    if not any_alive:
        try:
            for err_file in (project_root / "devsession").glob(".bg_finalize_*.err"):
                try:
                    text = err_file.read_text(encoding="utf-8", errors="ignore").strip()
                except Exception:
                    continue
                # Progress output goes to stderr too, so "wrote anything" is not
                # "failed". Only a real traceback or error line counts, otherwise a
                # successful finalize was reported as a failure in both the issue log
                # and doctor.
                if text and _looks_like_failure(text):
                    _log_issue(
                        "session_recorder/background",
                        f"{err_file.stem.replace('.bg_finalize_', '')} finalize failed: {text[-400:]}",
                        severity="error",
                        project_root=project_root,
                    )
                err_file.unlink(missing_ok=True)
        except Exception:
            pass

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

# Lookahead-anchored captures so values containing `|` (e.g. shell pipelines,
# X|Y phrasing) don't truncate the goal/resolved fields. Without lookaheads,
# `[^|]*` would stop at the first pipe inside a value and the rest of the
# pattern would fail to match.
_SESSION_SIGNAL_RE = re.compile(
    r'<!--session-signal:\s*'
    r'(?:goal=(.*?)\s*\|\s*(?=resolved=))?'   # optional goal, anchored on `| resolved=`
    r'resolved=(.*?)\s*\|\s*(?=open=)'        # resolved, anchored on `| open=`
    r'open=(.*?)\s*-->',                       # open, anchored on closing `-->`
    re.IGNORECASE,
)


# A session-signal is a working set, not an accumulating ledger. Left uncapped,
# `open` grows monotonically because unchanged items are re-emitted every turn: one
# long session went from ~110 characters to ~900, and the growth is worst at the
# tail where each response is already most expensive.
#
# The cost is also misallocated. Drift detection, the consumer with the strongest
# claim to being useful, reads only `goal`. `resolved` has no consumer at all.
#
# 5 matches the resume brief's per-category cap. Items are labels, not prose.
_SIGNAL_MAX_ITEMS = 5
_SIGNAL_MAX_ITEM_CHARS = 120
_SIGNAL_MAX_GOAL_CHARS = 200


def _extract_session_signal(message: str) -> Optional[Dict[str, Any]]:
    """Parse a session-signal tag from an assistant message.

    Supports both formats:
      <!--session-signal: goal=X | resolved=Y | open=Z-->
      <!--session-signal: resolved=Y | open=Z-->

    When a message contains multiple tags (e.g. an example earlier in the
    body and the real trailing tag), the trailing tag wins. This mirrors
    the strip behaviour, which removes every match.
    """
    matches = list(_SESSION_SIGNAL_RE.finditer(message))
    if not matches:
        return None
    match = matches[-1]
    goal_raw = (match.group(1) or "").strip()
    resolved_raw = match.group(2).strip()
    open_raw = match.group(3).strip()

    def _items(raw: str) -> List[str]:
        return [t.strip()[:_SIGNAL_MAX_ITEM_CHARS] for t in raw.split(",") if t.strip()]

    resolved_all = _items(resolved_raw)
    open_all = _items(open_raw)

    signal: Dict[str, Any] = {
        "resolved": resolved_all[:_SIGNAL_MAX_ITEMS],
        "open": open_all[:_SIGNAL_MAX_ITEMS],
    }
    # Never drop silently. Every consumer keeps working on the capped lists, but the
    # counts make it visible that a cap applied, and save_session_notes still records
    # the full open-issue set into the session summary, which is what the resume
    # brief reads. Truncation here costs nothing durable.
    if len(open_all) > _SIGNAL_MAX_ITEMS:
        signal["open_truncated"] = len(open_all) - _SIGNAL_MAX_ITEMS
    if len(resolved_all) > _SIGNAL_MAX_ITEMS:
        signal["resolved_truncated"] = len(resolved_all) - _SIGNAL_MAX_ITEMS
    if goal_raw:
        signal["goal"] = goal_raw[:_SIGNAL_MAX_GOAL_CHARS]
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


# Approximate tokens per byte in conversational text (conservative estimate).
# Calibrated for Opus 4.7's 1M-context window: 800K is ~80% of capacity, the
# standard "yellow zone" that leaves headroom for save_session_notes plus a
# few follow-ups before compaction actually triggers. The previous 400K value
# was right for 200K-context models but fired at 40% on 1M-context, which
# trained users to ignore the reminder.
_BYTES_PER_TOKEN = 4
_PRECOMPACT_TOKEN_THRESHOLD = 800_000
_PRECOMPACT_BYTE_THRESHOLD = _PRECOMPACT_TOKEN_THRESHOLD * _BYTES_PER_TOKEN  # ~3.2MB WAL
_REMINDER_SENT_SUFFIX = ".precompact_reminded"

# Continuation hint — Stop hook writes, UserPromptSubmit hook reads + clears.
# Mirrors the pre-compaction reminder pattern (sidecar file next to WAL) since
# Stop-hook stdout is not reliably injected into the next turn's context.
_CONTINUATION_HINT_SUFFIX = ".continuation_hint.json"

# Drift detector: after this many consecutive turns on the same goal, fire a
# zoom-out re-evaluation prompt instead of a continuation prompt. Default
# calibrated from observed sessions where meta-loops became noticeable around
# ~7-10 turns. Override with RECCLI_DRIFT_THRESHOLD env var.
_DRIFT_TURN_THRESHOLD = int(os.environ.get("RECCLI_DRIFT_THRESHOLD", "10"))

# Heuristic verb list for "this user prompt is asking for code execution" —
# liberal by design (false positives just nudge the agent toward continuation,
# which it can ignore; false negatives lose the autonomy benefit entirely).
_CODE_INTENT_VERBS = {
    "implement", "fix", "add", "patch", "run", "build", "ship", "apply",
    "delete", "remove", "modify", "refactor", "write", "edit", "rename",
    "extract", "inline", "split", "merge", "create", "make", "configure",
    "update", "wire", "set up", "set-up", "scaffold", "generate", "rewrite",
    "test", "deploy", "migrate", "rollback", "revert",
}

# Open-item phrases that mean "this is waiting for user input." Items matching
# these are stripped before the continuation check fires, since auto-continuing
# on them would just produce a hint pointing at something the agent can't
# autonomously act on. Surfaced empirically by the first two live triggers of
# the continuation hook pointing at "user decides ..." items.
_USER_DECISION_PHRASES = (
    "user decides", "user picks", "user chooses", "user confirms",
    "user approves", "user signs off", "user reviews", "user wants",
    "user prefers", "ask user", "ask the user", "wait for user",
    "needs user", "pending user", "user input", "user feedback",
    "user direction", "user to decide", "user signal", "awaiting user",
)


def _is_user_decision_item(item: str) -> bool:
    """True if the open item describes work that waits on user input."""
    if not item:
        return False
    p = item.lower()
    return any(phrase in p for phrase in _USER_DECISION_PHRASES)


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


# ---------------------------------------------------------------------------
# Continuation + drift detection (autonomous-continuation autopilot)
# ---------------------------------------------------------------------------
# The Stop hook computes whether the agent should be nudged to either:
#   a) drive its own open items forward (continuation), or
#   b) zoom out and re-evaluate path relevance (drift).
# The hint is persisted to a sidecar file next to the WAL. The next
# UserPromptSubmit hook reads, prints (Claude Code injects stdout into the
# turn context), and clears the file. This mirrors how the pre-compaction
# reminder works — Stop-hook stdout is not reliably injected, but
# UserPromptSubmit-hook stdout is.

def _continuation_hint_path(wal: Path) -> Path:
    return wal.with_suffix(_CONTINUATION_HINT_SUFFIX)


def _is_code_intent_prompt(prompt: str) -> bool:
    """Liberal heuristic: does this user prompt ask for code-side execution?"""
    if not prompt:
        return False
    p = prompt.lower()[:400]
    return any(v in p for v in _CODE_INTENT_VERBS)


def _last_user_prompt_in_wal(wal: Path) -> Optional[str]:
    """Most recent user prompt in the WAL, or None."""
    if not wal.exists():
        return None
    try:
        last_prompt: Optional[str] = None
        for line in wal.read_text(encoding="utf-8").splitlines():
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            if rec.get("type") == "user_prompt":
                last_prompt = rec.get("content", "")
        return last_prompt
    except Exception:
        return None


def _consecutive_same_goal_turns(wal: Path) -> int:
    """Count consecutive trailing assistant_response records sharing the same goal.

    Goals are compared loosely: lowercased + first 80 chars. Empty goals don't
    count toward the streak (they reset it).
    """
    if not wal.exists():
        return 0
    goals: List[str] = []
    try:
        for line in wal.read_text(encoding="utf-8").splitlines():
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            if rec.get("type") != "assistant_response":
                continue
            sig = rec.get("session_signal") or {}
            g = (sig.get("goal") or "").strip().lower()[:80]
            goals.append(g)
    except Exception:
        return 0

    if not goals:
        return 0
    last = goals[-1]
    if not last:
        return 0
    count = 0
    for g in reversed(goals):
        if g == last:
            count += 1
        else:
            break
    return count


def _filter_open_items_by_goal(goal: str, open_items: List[str]) -> Dict[str, Any]:
    """Filter `open_items` to those plausibly related to `goal`.

    This is the only implementation. An mcp_server.evaluate_continuation tool
    once duplicated it and was removed: it asked the agent to judge its own
    progress against its own goal, which closes the loop with a single judge and
    supplies no independent signal. The hook path below is mechanical and fires
    without the agent having to remember anything.
    Returns a dict with keys: action, next, remaining, filtered.
    """
    if not open_items:
        return {"action": "done", "next": None, "remaining": [], "filtered": []}

    if not goal:
        return {
            "action": "continue", "next": open_items[0],
            "remaining": list(open_items[1:]), "filtered": [],
        }

    _STOP = {
        "the","a","an","and","or","but","in","on","at","to","for","of","with",
        "by","from","as","is","are","was","were","be","been","being","this","that",
    }
    goal_lower = goal.lower()
    goal_words = {w for w in goal_lower.split() if w not in _STOP and len(w) > 2}

    try:
        from ..retrieval.query_expansion import _SYNONYM_MAP
        expanded = set(goal_words)
        for w in list(goal_words):
            if w in _SYNONYM_MAP:
                expanded |= _SYNONYM_MAP[w]
        goal_words = expanded
    except Exception:
        pass

    actionable: List[str] = []
    filtered: List[str] = []
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
        return {"action": "wait", "next": None, "remaining": [], "filtered": filtered}
    return {
        "action": "continue", "next": actionable[0],
        "remaining": actionable[1:], "filtered": filtered,
    }


def compute_continuation_hint(session_id: str, cwd: str) -> None:
    """Compute and persist a continuation/drift hint. Called by Stop hook.

    Two checks (drift takes precedence):
    1. Drift: N consecutive turns on the same goal → zoom-out hint.
    2. Continuation: prior user prompt was code-intent AND open items exist
       AND open items pass the goal-relevance filter → continuation hint.
    """
    project_root = _find_project_root(cwd, session_id)
    if project_root is None:
        return
    wal = _wal_path(project_root, session_id)
    if not wal.exists():
        return

    # Read most recent assistant_response (just appended) for the latest signal.
    sig: Optional[Dict[str, Any]] = None
    try:
        for line in wal.read_text(encoding="utf-8").splitlines():
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            if rec.get("type") == "assistant_response" and rec.get("session_signal"):
                sig = rec["session_signal"]  # last write wins → latest
    except Exception:
        return
    if sig is None:
        return

    goal = (sig.get("goal") or "").strip()
    open_items = sig.get("open") or []
    if isinstance(open_items, str):
        open_items = [s.strip() for s in open_items.split(",") if s.strip()]

    # ---- Drift check (takes precedence over continuation) ----
    #
    # Fire once per threshold-length run, not on every turn past it. `streak >=
    # threshold` stays true for the rest of the session once tripped, so the
    # zoom-out re-fired every turn AND returned before the continuation check
    # below, permanently starving the branch it shares this function with.
    #
    # The modulo is deliberately stateless: it needs no sidecar to remember what
    # already fired, and it re-arms on its own. A goal change resets the streak to
    # 1 (_consecutive_same_goal_turns counts only trailing identical goals), so a
    # genuinely long stretch on one goal still gets reminded at 10, 20, 30 turns
    # while the turns in between fall through to continuation.
    streak = _consecutive_same_goal_turns(wal)
    if streak >= _DRIFT_TURN_THRESHOLD and streak % _DRIFT_TURN_THRESHOLD == 0:
        hint = {
            "kind": "zoomout",
            "streak": streak,
            "goal": goal,
            "text": (
                f"[RecCli zoom-out] {streak} consecutive turns on goal '{goal[:80]}'. "
                "Before continuing, briefly assess whether this is still the highest-leverage "
                "thing to be working on. Check: (a) project-level priorities (.devproject "
                "in-progress features, recent open issues across sessions), (b) whether the "
                "current path has hit diminishing returns. If a pivot is warranted, propose it "
                "to the user. If the current path is still right, say so explicitly and continue."
            ),
        }
        try:
            _continuation_hint_path(wal).write_text(json.dumps(hint), encoding="utf-8")
        except Exception:
            _log_issue("hooks/Stop", "Failed to write zoomout hint", project_root=project_root)
        return

    # ---- Continuation check ----
    if not open_items:
        return

    # Strip items that wait on user input — auto-continuing on those would
    # just point the agent at work it can't autonomously execute.
    actionable_items = [it for it in open_items if not _is_user_decision_item(it)]
    if not actionable_items:
        return

    last_prompt = _last_user_prompt_in_wal(wal) or ""
    if not _is_code_intent_prompt(last_prompt):
        return

    decision = _filter_open_items_by_goal(goal, actionable_items)
    if decision["action"] != "continue" or not decision.get("next"):
        return

    hint = {
        "kind": "continue",
        "goal": goal,
        "next": decision["next"],
        "remaining": decision.get("remaining", []),
        "text": (
            f"[RecCli continuation] Open item to drive next: '{decision['next']}'. "
            f"Remaining open items after this one: {len(decision.get('remaining', []))}. "
            "Proceed to execute. If you reach a natural decision point, ask the user; "
            "otherwise keep going to the next item."
        ),
    }
    try:
        _continuation_hint_path(wal).write_text(json.dumps(hint), encoding="utf-8")
    except Exception:
        _log_issue("hooks/Stop", "Failed to write continuation hint", project_root=project_root)


def consume_continuation_hint(session_id: str, cwd: str) -> Optional[str]:
    """Read + delete a pending continuation hint. Called by UserPromptSubmit hook."""
    project_root = _find_project_root(cwd, session_id)
    if project_root is None:
        return None
    wal = _wal_path(project_root, session_id)
    hint_path = _continuation_hint_path(wal)
    if not hint_path.exists():
        return None
    try:
        data = json.loads(hint_path.read_text(encoding="utf-8"))
    except Exception:
        try:
            hint_path.unlink()
        except Exception:
            pass
        return None
    try:
        hint_path.unlink()
    except Exception:
        pass
    return data.get("text")


def _recover_orphan_wals(project_root: Path, current_session_id: str) -> None:
    """Finalize WALs from previous sessions that never got a clean SessionEnd.

    This handles crashes, force-quits, and hook failures that left WALs behind.
    Called at the start of each new session.
    """
    sessions_dir = _devsession_dir(project_root)
    for wal_file in sessions_dir.glob(".hooks_wal_*.jsonl"):
        # Skip the current session's WAL.
        # removeprefix is exact (str.replace strips every occurrence and would
        # corrupt a session_id that happened to contain '.hooks_wal_').
        wal_sid = wal_file.stem.removeprefix(".hooks_wal_")
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

                # Spec field on assistant messages (DEVSESSION_FORMAT.md); dropped at every
                # flush site, so 0 of 87,379 stored messages carried it. Persisting it is what
                # lets drift history survive a compaction.
                if rec.get("session_signal"):
                    msg["session_signal"] = rec["session_signal"]
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
            if len(conversation) >= _min_summarizable():
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
                # Spec field on assistant messages (DEVSESSION_FORMAT.md); dropped at every
                # flush site, so 0 of 87,379 stored messages carried it. Persisting it is what
                # lets drift history survive a compaction.
                if rec.get("session_signal"):
                    msg["session_signal"] = rec["session_signal"]
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

        # Background summarize the compacted session.
        # This called _spawn_background_summarize, which does not exist anywhere in
        # the package - a rename that missed this site. Every PostCompact raised
        # NameError, and handle_event logged it as a routine "Failed to compact
        # session" warning, so post-compaction summarization had never once run.
        from ..session.devsession import MIN_SUMMARIZABLE_MESSAGES
        if len(session.conversation) >= MIN_SUMMARIZABLE_MESSAGES:
            _spawn_background_finalize(output_path)

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
        # Remove the continuation-hint sidecar with its WAL; leaving it behind
        # meant a stale hint from a finished session could be consumed by a later one.
        _continuation_hint_path(wal).unlink(missing_ok=True)
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
        # Remove the continuation-hint sidecar with its WAL; leaving it behind
        # meant a stale hint from a finished session could be consumed by a later one.
        _continuation_hint_path(wal).unlink(missing_ok=True)
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
        # Spec field on assistant messages (DEVSESSION_FORMAT.md); dropped at every
        # flush site, so 0 of 87,379 stored messages carried it. Persisting it is what
        # lets drift history survive a compaction.
        if rec.get("session_signal"):
            msg["session_signal"] = rec["session_signal"]
        conversation.append(msg)

    # Check if save_session_notes already created a .devsession with a summary.
    # If so, merge the full WAL conversation into it instead of creating a new file.
    from ..session.devsession import DevSession

    sessions_dir = _devsession_dir(project_root)
    existing_session = None
    existing_path = None

    # The merge target must belong to THIS session. Selecting purely by mtime let a
    # session overwrite an unrelated session's conversation while keeping that
    # session's summary, destroying a transcript and leaving every span_id and
    # message_range in the surviving summary pointing into the wrong conversation.
    #
    # Two ways to establish ownership, in order of strength:
    #   1. claude_session_id matches exactly.
    #   2. The file carries no claude_session_id (save_session_notes does not record
    #      one) but was created at or after this session started, so it cannot be a
    #      pre-existing session's file.
    session_started_at = str(header.get("started_at") or "")

    def _belongs_to_this_session(candidate) -> bool:
        meta = candidate.metadata or {}
        candidate_sid = meta.get("claude_session_id")
        if candidate_sid:
            return candidate_sid == session_id
        if not session_started_at:
            return False  # cannot establish ownership; refuse rather than guess
        created_at = str(meta.get("created_at") or "")
        return bool(created_at) and created_at >= session_started_at

    # Only files written during this session can be ours. Without this bound the
    # loop no longer stopped at the first summarized file and a SessionEnd with no
    # notes file - the common case - DevSession.load()ed the entire store.
    session_start_mtime = 0.0
    if session_started_at:
        try:
            session_start_mtime = datetime.fromisoformat(session_started_at).timestamp()
        except Exception:
            session_start_mtime = 0.0

    for sf in sorted(sessions_dir.glob("*.devsession"), key=lambda p: p.stat().st_mtime, reverse=True):
        if sf.name.startswith(".live_"):
            continue
        try:
            if session_start_mtime and sf.stat().st_mtime < session_start_mtime - 1:
                break   # older than this session; everything after is older still
            candidate = DevSession.load(sf)
            if not (candidate.summary and candidate.summary.get("overview", "").strip()):
                continue
            if not _belongs_to_this_session(candidate):
                continue
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
    # The normal path unlinked the WAL but not the hint sidecar, so every
    # finished session leaked one file that a later session could consume.
    _continuation_hint_path(wal).unlink(missing_ok=True)
    wal.unlink(missing_ok=True)
    live_snapshot = _devsession_dir(project_root) / f".live_{session_id}.devsession"
    live_snapshot.unlink(missing_ok=True)
    reminder_flag = wal.with_suffix(_REMINDER_SENT_SUFFIX)
    reminder_flag.unlink(missing_ok=True)
    # Drop the active-session breadcrumb so ~/.reccli/active_sessions doesn't
    # accumulate one stale file per Claude Code session.
    breadcrumb = ACTIVE_PROJECT_DIR / f"{session_id}.json"
    breadcrumb.unlink(missing_ok=True)

    # Spawn background: summarize (if no summary yet) + embed + index
    if len(conversation) >= _min_summarizable():
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
        "if __import__('reccli.session.devsession', fromlist=['x']).is_stub_summary(s.summary):\n"
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
        "            if isinstance(item, dict):\n"
        "                item.pop('embedding', None)\n"
        "    changed = True\n"
        "if changed:\n"
        "    s.save(path)\n"
        "    from reccli.retrieval.vector_index import build_unified_index\n"
        "    build_unified_index(path.parent, verbose=False)\n"
    )
    # Capture stderr to a file rather than discarding it. This subprocess does the
    # summarize + embed + reindex work, and with stderr=DEVNULL a crash inside it
    # was completely invisible: the session ended up with no summary, was never
    # indexed, and nothing anywhere recorded why. The doctor's issues.logged check
    # surfaces whatever lands here.
    project_root = _find_project_root(Path(str(session_path))) or Path(str(session_path)).parent.parent
    err_path = _devsession_dir(project_root) / f".bg_finalize_{Path(str(session_path)).stem}.err"
    try:
        err_handle = open(err_path, "w", encoding="utf-8")
    except Exception:
        err_handle = subprocess.DEVNULL
    try:
        proc = subprocess.Popen(
            [sys.executable, "-c", script, str(session_path)],
            stdout=subprocess.DEVNULL,
            stderr=err_handle,
            start_new_session=True,
        )
        register_bg_task(
            _find_project_root(Path(str(session_path))) or Path(str(session_path)).parent.parent,
            proc.pid,
            "end_session_summarize",
        )
    except Exception as e:
        _log_issue("session_recorder", f"background summarize spawn failed: {e}", severity="warning")
