"""Shared detached-launch helpers for organization supervisors.

MCP, the local console, and approval continuations all use this module so
supervisor identity and process reaping behave consistently.
"""

from __future__ import annotations

import json
import os
import secrets
import shlex
import socket
import subprocess
import sys
import threading
import webbrowser
from pathlib import Path
from typing import Any, Dict, Optional


def _reap_child(process: subprocess.Popen[Any]) -> None:
    """Wait for a launched child when the current host process stays alive."""
    try:
        process.wait()
    except Exception:
        # Reaping is best-effort. The supervisor writes its own durable status.
        pass


def reap_detached_process(
    process: subprocess.Popen[Any],
    *,
    label: str = "background",
) -> None:
    """Attach a daemon waiter to a detached child of a long-lived host."""
    threading.Thread(
        target=_reap_child,
        args=(process,),
        name=f"reccli-{label}-reaper-{process.pid}",
        daemon=True,
    ).start()


def launch_organization_worker(request: Dict[str, Any]) -> Dict[str, Any]:
    """Launch one detached supervisor and publish its durable identity."""
    run_dir = Path(request["run_dir"]).expanduser().resolve()
    project_root = Path(request["project_root"]).expanduser().resolve()
    stdout_handle = (run_dir / "worker_stdout.txt").open("a", encoding="utf-8")
    stderr_handle = (run_dir / "worker_stderr.txt").open("a", encoding="utf-8")
    try:
        process = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "reccli.organization_worker",
                str(run_dir / "request.json"),
            ],
            cwd=project_root,
            env=os.environ.copy(),
            stdin=subprocess.DEVNULL,
            stdout=stdout_handle,
            stderr=stderr_handle,
            start_new_session=True,
        )
    finally:
        stdout_handle.close()
        stderr_handle.close()

    supervisor = {
        "pid": process.pid,
        "run_id": request["run_id"],
        "started_at": request["created_at"],
    }
    (run_dir / "supervisor.json").write_text(
        json.dumps(supervisor, indent=2) + "\n",
        encoding="utf-8",
    )

    # Popen children become zombies if a long-lived MCP/console host never
    # calls wait(). A daemon waiter keeps the detached session independent
    # while ensuring the local parent reaps it when it exits.
    reap_detached_process(process, label="org")

    from .hooks.session_recorder import register_bg_task

    register_bg_task(
        project_root,
        process.pid,
        f"organization:{request['run_id']}",
    )
    return {
        "pid": process.pid,
        "run_id": request["run_id"],
        "run_dir": str(run_dir),
    }


def start_organization_from_arguments(arguments: Dict[str, Any]) -> Dict[str, Any]:
    """Create and launch a run from one already-validated argument mapping."""
    from .organization import create_run_request

    request = create_run_request(**arguments)
    launched = launch_organization_worker(request)
    return {
        "status": "starting",
        "run_id": request["run_id"],
        "run_dir": request["run_dir"],
        "pid": launched["pid"],
        "provider": request["provider"],
        "provider_requested": request["provider_requested"],
        "host_provider": request["host_provider"],
        "provider_assignments": request["provider_assignments"],
        "blind_verifier_provider": request["blind_verifier_provider"],
        "topology": request["topology"],
        "mission_origin": request.get("mission_origin", "direct"),
        "continuation_from_run_id": request.get(
            "continuation_from_run_id",
        ),
        "continuation_conclusion_sha256": request.get(
            "continuation_conclusion_sha256",
        ),
        "human_promotion_required": request["human_promotion_required"],
        "evidence_paths": request["evidence_paths"],
        "protected_paths": request["protected_paths"],
        "context_manifest": request["context_manifest"],
        "experiment_policy": request.get("experiment_policy"),
        "max_experiments": request["max_experiments"],
        "next": (
            "Poll organization_status with this run_id until terminal, "
            "then report its conclusion verbatim before adding your own "
            "interpretation."
        ),
        "terminal_output": (
            "run-conclusion.json and run-conclusion.md; returned as "
            "organization_status.conclusion"
        ),
    }


def _running_console(
    project_root: Path,
    port: int,
) -> Optional[Dict[str, Any]]:
    """Find a matching token-bearing RecCli console already serving a project."""
    try:
        completed = subprocess.run(
            ["ps", "-axo", "pid=,stat=,command="],
            capture_output=True,
            text=True,
            timeout=3,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if completed.returncode != 0:
        return None
    expected_root = project_root.resolve()
    for raw_line in (completed.stdout or "").splitlines():
        pieces = raw_line.strip().split(None, 2)
        if len(pieces) != 3 or pieces[1].startswith("Z"):
            continue
        try:
            argv = shlex.split(pieces[2])
        except ValueError:
            continue
        if "reccli.organization_console" not in argv:
            continue
        try:
            module_index = argv.index("reccli.organization_console")
            served_root = Path(argv[module_index + 1]).expanduser().resolve()
        except (ValueError, IndexError, OSError):
            continue
        if served_root != expected_root:
            continue
        try:
            port_index = argv.index("--port")
            served_port = int(argv[port_index + 1])
            token_index = argv.index("--token")
            token = argv[token_index + 1]
        except (ValueError, IndexError):
            continue
        if served_port != int(port) or not token:
            continue
        return {
            "pid": int(pieces[0]),
            "token": token,
            "url": f"http://127.0.0.1:{int(port)}/?token={token}",
        }
    return None


def _port_is_listening(port: int) -> bool:
    try:
        with socket.create_connection(("127.0.0.1", int(port)), timeout=0.25):
            return True
    except OSError:
        return False


def launch_organization_console(
    project_root: Path,
    *,
    port: int = 8777,
    open_browser: bool = True,
) -> Dict[str, Any]:
    """Reuse or launch one token-protected localhost organization console."""
    root = project_root.expanduser().resolve()
    existing = _running_console(root, int(port))
    if existing is not None:
        if open_browser:
            webbrowser.open(existing["url"])
        return {
            "status": "running",
            "pid": existing["pid"],
            "url": existing["url"],
            "project_root": str(root),
            "reused": True,
        }
    if _port_is_listening(int(port)):
        raise RuntimeError(
            f"port {int(port)} is already used by a process that is not a "
            f"matching RecCli console for {root}"
        )

    token = secrets.token_urlsafe(24)
    log_dir = root / "devsession" / "organization-console"
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / f"console-{int(port)}.log"
    log_handle = log_path.open("a", encoding="utf-8")
    args = [
        sys.executable,
        "-m",
        "reccli.organization_console",
        str(root),
        "--port",
        str(int(port)),
        "--token",
        token,
    ]
    if not open_browser:
        args.append("--no-open")
    try:
        process = subprocess.Popen(
            args,
            cwd=root,
            env=os.environ.copy(),
            stdin=subprocess.DEVNULL,
            stdout=log_handle,
            stderr=log_handle,
            start_new_session=True,
        )
    finally:
        log_handle.close()
    reap_detached_process(process, label="org-console")

    from .hooks.session_recorder import register_bg_task

    register_bg_task(root, process.pid, f"organization-console:{int(port)}")
    return {
        "status": "starting",
        "pid": process.pid,
        "url": f"http://127.0.0.1:{int(port)}/?token={token}",
        "project_root": str(root),
        "log": str(log_path),
        "reused": False,
        "detail": (
            "The first launch may install and build frontend dependencies "
            "before opening the browser."
        ),
    }
