"""Detached worker entry point for RecCli organization MCP runs."""

from __future__ import annotations

import json
import os
import sys
import traceback
from pathlib import Path

from .organization import _utc_now, run_request


def _write_status(path: Path, value: dict) -> None:
    """Atomically publish worker status without racing MCP status readers."""
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def main() -> int:
    if len(sys.argv) != 2:
        print("usage: python -m reccli.organization_worker <request.json>", file=sys.stderr)
        return 2
    request_path = Path(sys.argv[1]).expanduser().resolve()
    request = json.loads(request_path.read_text(encoding="utf-8"))
    run_dir = Path(request["run_dir"])
    status_path = run_dir / "status.json"
    try:
        status = json.loads(status_path.read_text(encoding="utf-8"))
        status.update({"status": "running", "pid": os.getpid(), "updated_at": _utc_now()})
        _write_status(status_path, status)
        run_request(request)
        return 0
    except BaseException as exc:
        try:
            current = json.loads(status_path.read_text(encoding="utf-8"))
        except Exception:
            current = {}
        failure = {
            **current,
            "run_id": request.get("run_id"), "status": "failed",
            "round": int(current.get("round", 0) or 0),
            "detail": str(exc), "error": str(exc), "pid": os.getpid(),
            "provider": request.get("provider"), "topology": request.get("topology"),
            "host_provider": request.get("host_provider"),
            "provider_assignments": request.get("provider_assignments"),
            "blind_verifier_provider": request.get("blind_verifier_provider"),
            "control_protocol": request.get("control_protocol"),
            "updated_at": _utc_now(), "run_dir": str(run_dir),
        }
        _write_status(status_path, failure)
        (run_dir / "worker_traceback.txt").write_text(traceback.format_exc(), encoding="utf-8")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
