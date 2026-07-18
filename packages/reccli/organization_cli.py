"""Dependency-light CLI for observing and steering RecCli organizations."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Optional, Sequence

from .project.devproject import discover_project_root


def _project_root(value: Optional[str]) -> Optional[Path]:
    if value:
        candidate = Path(value).expanduser().resolve()
        root = discover_project_root(candidate)
    else:
        root = discover_project_root(Path.cwd())
    if root is None:
        print(
            "No project found. Run inside a project or pass --project-root.",
            file=sys.stderr,
        )
    return root


def _cmd_list(args: argparse.Namespace) -> int:
    from .organization_control import list_organization_runs

    root = _project_root(args.project_root)
    if root is None:
        return 1
    payload = list_organization_runs(str(root), limit=args.limit)
    if args.json:
        print(json.dumps(payload, indent=2, ensure_ascii=False))
        return 0
    for run in payload.get("runs", []):
        live = "live" if run.get("process_live") else "stopped"
        if run.get("phase") == "closeout" or (
            int(run.get("round", 0) or 0)
            > int(run.get("max_rounds", 0) or 0)
        ):
            progress = (
                f"closeout {run.get('closeout_round', '?')}/"
                f"{run.get('max_closeout_rounds', '?')}"
            )
        else:
            progress = (
                f"round {run.get('round', 0)}/"
                f"{run.get('max_rounds', '?')}"
            )
        print(
            f"{run['run_id']}  {run.get('status', 'unknown'):<11} "
            f"{progress}  "
            f"{run.get('provider', '?')}  {live}"
        )
    return 0


def _cmd_status(args: argparse.Namespace) -> int:
    from .organization_control import organization_snapshot

    root = _project_root(args.project_root)
    if root is None:
        return 1
    payload = organization_snapshot(
        str(root),
        args.run_id,
        include_recent=args.recent,
    )
    print(json.dumps(payload, indent=2, ensure_ascii=False))
    return 0 if payload.get("status") != "not_found" else 1


def _cmd_control(args: argparse.Namespace) -> int:
    from .organization_control import (
        cancel_organization_run,
        queue_control_request,
    )

    root = _project_root(args.project_root)
    if root is None:
        return 1
    if args.control_action == "cancel":
        payload = cancel_organization_run(
            str(root),
            args.run_id,
            idempotency_key=args.idempotency_key,
            requested_by="reccli-cli",
        )
    else:
        payload = queue_control_request(
            str(root),
            args.run_id,
            args.control_action,
            target=getattr(args, "target", None),
            content=getattr(args, "message", None),
            tag=getattr(args, "tag", "plan"),
            idempotency_key=args.idempotency_key,
            requested_by="reccli-cli",
        )
    print(json.dumps(payload, indent=2, ensure_ascii=False))
    return 0 if payload.get("status") not in {
        "not_found",
        "rejected",
        "unsupported",
    } else 1


def _cmd_console(args: argparse.Namespace) -> int:
    from .organization_console import serve_console

    root = _project_root(args.project_root)
    if root is None:
        return 1
    try:
        return serve_console(
            root,
            port=args.port,
            open_browser=not args.no_open,
            development=args.dev,
        )
    except RuntimeError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="reccli organization",
        description="Observe and steer durable multi-agent organization runs",
    )
    subparsers = parser.add_subparsers(
        dest="organization_command",
        required=True,
    )

    list_parser = subparsers.add_parser(
        "list",
        help="List organization runs for a project",
    )
    list_parser.add_argument("--project-root")
    list_parser.add_argument("--limit", type=int, default=100)
    list_parser.add_argument("--json", action="store_true")
    list_parser.set_defaults(func=_cmd_list)

    status_parser = subparsers.add_parser(
        "status",
        help="Read a dashboard-ready durable run snapshot",
    )
    status_parser.add_argument("run_id")
    status_parser.add_argument("--project-root")
    status_parser.add_argument("--recent", type=int, default=150)
    status_parser.set_defaults(func=_cmd_status)

    message_parser = subparsers.add_parser(
        "message",
        help="Steer an agent or role group at the next safe boundary",
    )
    message_parser.add_argument("run_id")
    message_parser.add_argument("message")
    message_parser.add_argument("--target", required=True)
    message_parser.add_argument(
        "--tag",
        default="plan",
        choices=[
            "plan",
            "question",
            "answer",
            "handoff",
            "review",
            "decision",
            "status",
            "blocker",
        ],
    )
    message_parser.add_argument("--idempotency-key")
    message_parser.add_argument("--project-root")
    message_parser.set_defaults(
        func=_cmd_control,
        control_action="message",
    )

    for action, help_text in (
        ("pause", "Pause after the current synchronized round"),
        ("resume", "Resume a run paused at a round boundary"),
        ("cancel", "Cancel and terminate a live organization process group"),
    ):
        control_parser = subparsers.add_parser(action, help=help_text)
        control_parser.add_argument("run_id")
        control_parser.add_argument("--idempotency-key")
        control_parser.add_argument("--project-root")
        control_parser.set_defaults(
            func=_cmd_control,
            control_action=action,
        )

    console_parser = subparsers.add_parser(
        "console",
        help="Open the localhost organization viewer and steering console",
    )
    console_parser.add_argument("--project-root")
    console_parser.add_argument("--port", type=int, default=8777)
    console_parser.add_argument("--no-open", action="store_true")
    console_parser.add_argument("--dev", action="store_true")
    console_parser.set_defaults(func=_cmd_console)
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
