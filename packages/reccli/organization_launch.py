"""Shared detached-launch helpers for organization supervisors.

MCP, the local console, and approval continuations all use this module so
supervisor identity and process reaping behave consistently.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
from pathlib import Path
from typing import Any, Dict


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
