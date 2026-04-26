"""
Claude Code hook event dispatcher.

Single entry point for all hook events. Reads JSON from stdin,
dispatches to the appropriate session_recorder method.

Usage in hook config:
    PYTHONPATH=.../packages python3 -m reccli.hooks.handle_event

Exit codes:
    0 = allow (always — recording is passive, never blocks)
"""

import json
import re
import sys
from pathlib import Path

from . import session_recorder
from .session_recorder import _log_issue


# session_id is interpolated into filenames (.hooks_wal_<sid>.jsonl,
# active_sessions/<sid>.json). Reject anything that could escape those paths.
_SESSION_ID_RE = re.compile(r"^[A-Za-z0-9_\-]{1,128}$")


def main():
    try:
        raw = sys.stdin.read()
        if not raw.strip():
            return
        event = json.loads(raw)
    except (json.JSONDecodeError, IOError):
        return

    hook_name = event.get("hook_event_name", "")
    session_id = event.get("session_id", "")
    cwd = event.get("cwd", "")

    if not session_id or not cwd:
        return

    if not _SESSION_ID_RE.match(session_id):
        return

    # Resolve project once so _log_issue routes to <project>/devsession/.issues.jsonl
    # (where list_issues reads) rather than ~/.reccli/.issues.jsonl. Falls back to
    # None when no project has been activated yet — _log_issue handles None.
    project_root = session_recorder._find_project_root(cwd, session_id)

    if hook_name == "SessionStart":
        session_recorder.start_session(session_id, cwd)
        # SessionStart may have just created the project linkage; refresh.
        if project_root is None:
            project_root = session_recorder._find_project_root(cwd, session_id)
        # Auto-inject project context if in a project with .devproject
        try:
            from .context_injector import get_session_start_context
            context = get_session_start_context(cwd)
            if context:
                print(context)
        except Exception:
            _log_issue("hooks/SessionStart", "Failed to inject session start context",
                       project_root=project_root)

    elif hook_name == "UserPromptSubmit":
        prompt = event.get("prompt", "")
        if prompt:
            session_recorder.record_user_prompt(session_id, prompt, cwd)

        # Check if approaching compaction threshold — inject pre-compaction reminder
        try:
            reminder = session_recorder.check_precompaction_threshold(session_id, cwd)
            if reminder:
                print(reminder)
        except Exception:
            _log_issue("hooks/UserPromptSubmit", "Failed pre-compaction threshold check",
                       project_root=project_root)

        # Auto-reason / MMC injection (gated by config)
        if prompt:
            try:
                from ..runtime.config import Config
                config = Config()
                mmc_enabled = config.data.get("mmc", False)
                auto_reason_enabled = config.data.get("auto_reason", False)

                if mmc_enabled:
                    from .auto_reason import get_mmc_protocol
                    protocol = get_mmc_protocol(prompt)
                    if protocol:
                        print(protocol)
                elif auto_reason_enabled:
                    from .auto_reason import get_reasoning_scaffold
                    scaffold = get_reasoning_scaffold(prompt)
                    if scaffold:
                        print(scaffold)
            except Exception:
                _log_issue("hooks/UserPromptSubmit", "Failed auto-reason/MMC injection",
                           project_root=project_root)

    elif hook_name == "Stop":
        message = event.get("last_assistant_message", "")
        if message and not event.get("stop_hook_active"):
            session_recorder.record_assistant_response(session_id, message, cwd)

    elif hook_name == "PostToolUse":
        tool_name = event.get("tool_name", "")

        # When load_project_context is called, set the breadcrumb FIRST so
        # record_tool_use can resolve the project for the very call that
        # activates it (the WAL would otherwise miss this anchor message).
        if tool_name == "mcp__reccli__load_project_context":
            try:
                tool_input = event.get("tool_input") or {}
                wd = tool_input.get("working_directory", "")
                if wd:
                    from ..project.devproject import discover_project_root
                    root = discover_project_root(Path(wd).resolve())
                    if root:
                        session_recorder.set_active_project(session_id, root)
                        if project_root is None:
                            project_root = root
            except Exception:
                _log_issue("hooks/PostToolUse", "Failed to set active project breadcrumb",
                           project_root=project_root)

        if tool_name:
            session_recorder.record_tool_use(
                session_id,
                tool_name,
                event.get("tool_input"),
                event.get("tool_response"),
                cwd,
            )

    elif hook_name == "PostCompact":
        # 1. Flush WAL to .devsession and trigger background summarization
        try:
            session_recorder.compact_session(session_id, cwd)
        except Exception:
            _log_issue("hooks/PostCompact", "Failed to compact session",
                       project_root=project_root)

        # 2. Validate .devproject file paths and detect new files (fast, no LLM).
        # Pass session_id so MCP-activated sessions (cwd outside project) still
        # resolve the project via the active-session breadcrumb.
        stale_note = ""
        try:
            from .context_injector import validate_and_note_staleness
            stale_note = validate_and_note_staleness(cwd, session_id) or ""
        except Exception:
            _log_issue("hooks/PostCompact", "Failed devproject staleness check",
                       project_root=project_root)

        # 3. Re-inject .devproject context + staleness notes (stdout → Claude's context)
        try:
            from .context_injector import get_post_compact_context
            context = get_post_compact_context(cwd, session_id)
            if context:
                if stale_note:
                    context += "\n" + stale_note
                print(context)
        except Exception:
            _log_issue("hooks/PostCompact", "Failed post-compact context injection",
                       project_root=project_root)

    elif hook_name == "SessionEnd":
        session_recorder.end_session(session_id, cwd)


if __name__ == "__main__":
    main()
