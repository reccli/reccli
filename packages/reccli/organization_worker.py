"""Detached worker entry point for RecCli organization MCP runs."""

from __future__ import annotations

import json
import os
import sys
import traceback
from pathlib import Path

from .organization import (
    _utc_now,
    _write_run_conclusion_files,
    get_topology,
    run_request,
)


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
        candidate_records = []
        candidate_path = run_dir / "candidates.jsonl"
        if candidate_path.exists():
            for raw in candidate_path.read_text(encoding="utf-8").splitlines():
                try:
                    candidate_records.append(json.loads(raw))
                except json.JSONDecodeError:
                    continue
        try:
            lead_id = get_topology(
                str(request.get("topology") or "google-rotating")
            ).leader_id
        except Exception:
            lead_id = "lead"
        failure_detail = f"{type(exc).__name__}: {str(exc)}"
        conclusion = {
            "schema": "reccli.organization-run-conclusion.v1",
            "run_id": request.get("run_id"),
            "admission": request.get("admission"),
            "terminal_status": "failed",
            "generated_at": _utc_now(),
            "generated_by": "host-fallback",
            "lead_agent_id": lead_id,
            "lead_provider": (
                request.get("provider_assignments") or {}
            ).get(lead_id),
            "summary": (
                "The organization supervisor failed before a terminal lead "
                "conclusion could complete."
            ),
            "accomplishments": [
                (
                    f"Recorded {int(current.get('completed_turns', 0) or 0)} "
                    "completed turn(s) before the supervisor failure."
                ),
            ],
            "conclusive_findings": [],
            "evidence_and_tests": [],
            "scientific_or_product_blockers": [],
            "infrastructure_failures": [failure_detail],
            "unresolved": [
                "The partial durable record requires inspection before retry.",
            ],
            "promotion_readiness": (
                "not_ready"
                if any(
                    record.get("kind") == "implementation"
                    for record in candidate_records
                )
                else "no_candidate"
            ),
            "next_action": (
                "Inspect worker_traceback.txt and the partial run artifacts, "
                "repair the supervisor failure, and retry from a clean "
                "checkpoint."
            ),
            "limitations": [
                (
                    "This is a conservative host fallback; the lead could not "
                    "perform terminal synthesis."
                ),
            ],
            "candidates": candidate_records,
            "integrated_candidates": {},
            "verified_candidate": None,
            "promotion_candidate": None,
            "promotion_request": None,
            "artifacts": [],
            "turn_counts": {
                "attempted": int(current.get("attempted_turns", 0) or 0),
                "completed": int(current.get("completed_turns", 0) or 0),
                "failed": int(current.get("failed_turns", 0) or 0) + 1,
            },
            "experiment_budget": {
                "maximum": int(request.get("max_experiments", 0) or 0),
                "used": int(current.get("candidate_artifact_bundles", 0) or 0),
                "remaining": max(
                    0,
                    int(request.get("max_experiments", 0) or 0)
                    - int(current.get("candidate_artifact_bundles", 0) or 0),
                ),
            },
            "canonical_effects_applied": False,
        }
        try:
            _write_run_conclusion_files(run_dir, conclusion)
        except Exception:
            pass
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
            "conclusion": conclusion,
            "updated_at": _utc_now(), "run_dir": str(run_dir),
        }
        _write_status(status_path, failure)
        (run_dir / "worker_traceback.txt").write_text(traceback.format_exc(), encoding="utf-8")
        try:
            from .organization_outcomes import record_outcome_event

            admission = request.get("admission") or {}
            record_outcome_event(
                Path(request["project_root"]), "run_terminal",
                str(request.get("run_id") or run_dir.name),
                terminal_status="failed",
                work_class=admission.get("work_class"),
                consumer=(admission.get("consumer") or {}).get("name"),
                usage=current.get("usage") or {},
                completed_turns=int(current.get("completed_turns", 0) or 0),
                promotion_readiness=conclusion.get("promotion_readiness"),
            )
        except Exception:
            # The ledger must never mask the original failure.
            pass
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
