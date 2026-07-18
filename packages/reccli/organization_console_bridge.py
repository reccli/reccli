"""JSON bridge used by the local Next.js organization console.

The bridge keeps filesystem policy and command validation in Python. The web
application sends one JSON object over stdin and receives one JSON object over
stdout, avoiding shell interpolation and duplicate control logic.
"""

from __future__ import annotations

import json
import sys
from typing import Any, Dict

from .organization_control import (
    cancel_organization_run,
    list_organization_runs,
    organization_snapshot,
    queue_control_request,
)


def dispatch(payload: Dict[str, Any]) -> Dict[str, Any]:
    command = str(payload.get("command") or "")
    working_directory = str(payload.get("working_directory") or "")
    if not working_directory:
        raise ValueError("working_directory is required")
    if command == "list":
        return list_organization_runs(
            working_directory,
            limit=int(payload.get("limit", 100) or 100),
        )
    run_id = str(payload.get("run_id") or "")
    if not run_id:
        raise ValueError("run_id is required")
    if command == "snapshot":
        return organization_snapshot(
            working_directory,
            run_id,
            include_recent=int(payload.get("include_recent", 150) or 150),
        )
    if command == "control":
        action = str(payload.get("action") or "")
        if action == "cancel":
            return cancel_organization_run(
                working_directory,
                run_id,
                idempotency_key=payload.get("idempotency_key"),
                requested_by="organization-console",
            )
        return queue_control_request(
            working_directory,
            run_id,
            action,
            target=payload.get("target"),
            content=payload.get("content"),
            tag=str(payload.get("tag") or "plan"),
            idempotency_key=payload.get("idempotency_key"),
            requested_by="organization-console",
        )
    raise ValueError(f"unknown bridge command: {command}")


def main() -> int:
    try:
        payload = json.loads(sys.stdin.read() or "{}")
        if not isinstance(payload, dict):
            raise ValueError("bridge payload must be an object")
        result = dispatch(payload)
        sys.stdout.write(json.dumps(result, ensure_ascii=False))
        return 0
    except Exception as exc:
        sys.stdout.write(json.dumps({
            "status": "bridge_error",
            "error": str(exc),
        }, ensure_ascii=False))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
