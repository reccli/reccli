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
import sys

from . import session_recorder


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

    if hook_name == "SessionStart":
        session_recorder.start_session(session_id, cwd)
        # Auto-inject project context if in a project with .devproject
        try:
            from .context_injector import get_session_start_context
            context = get_session_start_context(cwd)
            if context:
                print(context)
        except Exception:
            pass

    elif hook_name == "UserPromptSubmit":
        prompt = event.get("prompt", "")
        if prompt:
            session_recorder.record_user_prompt(session_id, prompt, cwd)

    elif hook_name == "Stop":
        message = event.get("last_assistant_message", "")
        if message and not event.get("stop_hook_active"):
            session_recorder.record_assistant_response(session_id, message, cwd)

    elif hook_name == "PostToolUse":
        tool_name = event.get("tool_name", "")
        if tool_name:
            session_recorder.record_tool_use(
                session_id,
                tool_name,
                event.get("tool_input"),
                event.get("tool_response"),
                cwd,
            )

    elif hook_name == "PostCompact":
        # Re-inject .devproject context after Claude Code compacts its context window.
        # Stdout from this hook gets added to Claude's context.
        try:
            from .context_injector import get_post_compact_context
            context = get_post_compact_context(cwd)
            if context:
                print(context)
        except Exception:
            pass

    elif hook_name == "SessionEnd":
        session_recorder.end_session(session_id, cwd)


if __name__ == "__main__":
    main()
