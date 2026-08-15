"""Durable observation and operator control for RecCli organizations.

The organization worker owns the in-memory inboxes used by native agent
sessions. External callers therefore communicate through an append-only,
run-local command queue. The worker applies commands only at round boundaries,
records an acknowledgement, and mirrors operator messages into the normal
message trace.

This module is deliberately usable from MCP, the CLI, and the local web
console. None of those surfaces edits ``status.json`` or an agent inbox
directly.
"""

from __future__ import annotations

import hashlib
import json
import os
import signal
import subprocess
import uuid
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

from .project.devproject import discover_project_root


CONTROL_SCHEMA = "reccli.organization-control.v1"
CONTROL_ACTIONS = {"message", "pause", "resume", "cancel"}
TERMINAL_STATUSES = {
    "completed",
    "completed_no_promotion",
    "completed_pending_human",
    "failed",
    "cancelled",
    "round_limit",
    "stalled",
    # An experiment-driven run that authored no contract by its deadline. Kept
    # distinct from round_limit so the two are not confused: round_limit means
    # the run worked until it ran out of rounds, this means it never had
    # anything to execute and the remaining rounds would have produced prose.
    "no_experiment_contract",
    # The lead declared the admission's done condition already satisfied or a
    # stop condition triggered. A successful outcome, distinct from stalled:
    # the run judged that proceeding had lower value than stopping.
    "completed_no_op",
}
MAX_OPERATOR_MESSAGE_CHARS = 12_000
APPROVAL_REQUEST_SCHEMA = "reccli.organization-approval-request.v1"
APPROVAL_DECISION_SCHEMA = "reccli.organization-approval-decision.v1"
OPERATOR_DECISION_SCHEMA = "reccli.organization-operator-decision.v1"


def _utc_now() -> str:
    from .organization import _utc_now as organization_utc_now

    return organization_utc_now()


def _safe_name(value: str) -> str:
    from .organization import _safe_name as organization_safe_name

    return organization_safe_name(value)


def _read_json(path: Path, default: Any = None) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return default


def _atomic_write_json(path: Path, value: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.{uuid.uuid4().hex}.tmp")
    temporary.write_text(
        json.dumps(value, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def _tail_jsonl(path: Path, limit: int) -> List[Dict[str, Any]]:
    if limit <= 0 or not path.is_file():
        return []
    result: List[Dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines()[-limit:]:
        try:
            value = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            result.append(value)
    return result


def _experiment_ledger_status(path: Path) -> Dict[str, Any]:
    if not path.is_file():
        return {
            "verified": True,
            "records": 0,
            "head_sha256": None,
            "error": None,
        }
    records: List[Dict[str, Any]] = []
    try:
        for line_number, line in enumerate(
            path.read_text(encoding="utf-8").splitlines(),
            1,
        ):
            if not line.strip():
                continue
            value = json.loads(line)
            if not isinstance(value, dict):
                return {
                    "verified": False,
                    "records": len(records),
                    "head_sha256": None,
                    "error": f"line {line_number} is not an object",
                }
            records.append(value)
    except (OSError, json.JSONDecodeError) as exc:
        return {
            "verified": False,
            "records": len(records),
            "head_sha256": None,
            "error": f"ledger read failed: {exc}",
        }
    from .organization import verify_experiment_trial_records

    verified, head, error = verify_experiment_trial_records(records)
    return {
        "verified": verified,
        "records": len(records),
        "head_sha256": head,
        "error": error,
    }


def _resolve_run(working_directory: str, run_id: str) -> Optional[Path]:
    from .organization import find_run

    return find_run(working_directory, run_id)


def _organization_root(working_directory: str) -> Optional[Path]:
    from .organization import organization_root

    project_root = discover_project_root(
        Path(working_directory).expanduser().resolve(),
    )
    return organization_root(project_root) if project_root else None


def _supervisor_pid(run_dir: Path, status: Dict[str, Any]) -> int:
    try:
        pid = int(status.get("pid") or 0)
    except (TypeError, ValueError):
        pid = 0
    if pid > 1:
        return pid
    supervisor = _read_json(run_dir / "supervisor.json", {})
    try:
        return int((supervisor or {}).get("pid") or 0)
    except (TypeError, ValueError):
        return 0


def process_group_activity(
    pid: int,
    run_dir: Path,
    agent_ids: Iterable[str] = (),
) -> tuple[Optional[bool], List[str]]:
    """Return process liveness and agents with active native CLI children.

    The durable agent ``state`` is a scheduling intent returned by the model,
    not proof that its native subprocess is executing right now.  The console
    needs the latter, so inspect the detached process group and bind Claude or
    Codex commands back to their per-agent worktree/context-pack paths.

    ``None`` liveness means the operating system could not provide a reliable
    answer. Zombie-only groups are treated as stopped.
    """
    if pid <= 1:
        return False, []
    try:
        proc = subprocess.run(
            ["ps", "-o", "pid=,stat=,command=", "-g", str(pid)],
            capture_output=True,
            text=True,
            timeout=3,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None, []
    if proc.returncode != 0:
        return False, []
    request_path = str(run_dir / "request.json")
    saw_native_child = False
    active_agents: set[str] = set()
    known_agents = [str(agent_id) for agent_id in agent_ids]
    for raw_line in (proc.stdout or "").splitlines():
        pieces = raw_line.strip().split(None, 2)
        if len(pieces) < 3:
            continue
        stat, command = pieces[1], pieces[2]
        if stat.startswith("Z"):
            continue
        if "reccli.organization_worker" in command and request_path in command:
            saw_native_child = True
        is_native_agent = (
            command.startswith("claude ")
            or " claude " in f" {command} "
            or command.startswith("codex exec ")
            or " codex exec " in f" {command} "
        )
        if is_native_agent:
            saw_native_child = True
            for agent_id in known_agents:
                if (
                    f"/{run_dir.name}/{agent_id}" in command
                    or f"/context-packs/{agent_id}" in command
                ):
                    active_agents.add(agent_id)
                    break
    return saw_native_child, sorted(active_agents)


def process_group_is_live(pid: int, run_dir: Path) -> Optional[bool]:
    """Return whether the run's detached supervisor process group is live."""
    live, _ = process_group_activity(pid, run_dir)
    return live


def _topology_snapshot(run: Dict[str, Any], status: Dict[str, Any]) -> Dict[str, Any]:
    from .organization import get_topology

    topology_id = str(run.get("topology") or status.get("topology") or "")
    if not topology_id:
        return {"agents": [], "routes": []}
    try:
        topology = get_topology(topology_id)
    except (KeyError, ValueError):
        return {"agents": [], "routes": []}
    provider_assignments = (
        run.get("provider_assignments")
        or status.get("provider_assignments")
        or {}
    )
    states = status.get("agent_states") or {}
    configured_agents = list(topology.agents)
    if provider_assignments:
        # Historical runs persist their exact provider-assignment roster.
        # Filter newly added topology slots so replaying an older dashboard
        # does not make dormant agents appear retroactively.
        configured_agents = [
            agent for agent in configured_agents
            if agent.agent_id in provider_assignments
        ]
    configured_ids = {agent.agent_id for agent in configured_agents}
    agents = [
        {
            "id": agent.agent_id,
            "role": agent.role,
            "provider": provider_assignments.get(agent.agent_id),
            "state": states.get(agent.agent_id, "unknown"),
            "write_scope": agent.write_scope,
            "is_lead": agent.agent_id == topology.leader_id,
            "is_finalizer": agent.agent_id == topology.finalizer_id,
            "is_integrator": agent.agent_id in topology.integrator_ids,
        }
        for agent in configured_agents
    ]
    routes = [
        {
            "from": sender,
            "to": recipient,
            "tags": sorted(tags) if tags is not None else None,
        }
        for (sender, recipient), tags in sorted(topology.routes.items())
        if sender in configured_ids and recipient in configured_ids
    ]
    return {
        "id": topology.topology_id,
        "name": topology.name,
        "description": topology.description,
        "culture": topology.culture,
        "leader_id": topology.leader_id,
        "finalizer_id": topology.finalizer_id,
        "manager_ids": [
            agent_id for agent_id in topology.manager_ids
            if agent_id in configured_ids
        ],
        "worker_ids": [
            agent_id for agent_id in topology.worker_ids
            if agent_id in configured_ids
        ],
        "primary_manager_by_worker": {
            worker: manager
            for worker, manager in topology.primary_manager_by_worker.items()
            if worker in configured_ids and manager in configured_ids
        },
        "integrator_ids": sorted(
            agent_id for agent_id in topology.integrator_ids
            if agent_id in configured_ids
        ),
        "scheduler": topology.scheduler,
        "inbox_only_ids": sorted(topology.inbox_only_ids),
        "agents": agents,
        "routes": routes,
    }


def _control_records(run_dir: Path, limit: int = 50) -> List[Dict[str, Any]]:
    requests_dir = run_dir / "control" / "requests"
    acknowledgements_dir = run_dir / "control" / "acknowledgements"
    records: List[Dict[str, Any]] = []
    request_paths = sorted(requests_dir.glob("*.json"))[-max(0, limit):] if requests_dir.is_dir() else []
    for request_path in request_paths:
        request = _read_json(request_path, {})
        if not isinstance(request, dict):
            continue
        acknowledgement = _read_json(
            acknowledgements_dir / f"{request.get('id', '')}.json",
            None,
        )
        records.append({
            **request,
            "acknowledgement": acknowledgement,
            "queue_status": (
                acknowledgement.get("status", "acknowledged")
                if isinstance(acknowledgement, dict)
                else "queued"
            ),
        })
    return records


def organization_snapshot(
    working_directory: str,
    run_id: str,
    include_recent: int = 100,
) -> Dict[str, Any]:
    """Build one dashboard-ready, durable snapshot for a run."""
    run_dir = _resolve_run(working_directory, run_id)
    if run_dir is None:
        return {"status": "not_found", "run_id": run_id}
    status = _read_json(run_dir / "status.json", {}) or {}
    run = _read_json(run_dir / "run.json", {}) or {}
    if not run:
        run = _read_json(run_dir / "request.json", {}) or {}
    goal_state = _read_json(run_dir / "goal-state.json", {}) or {}
    worker_goals = (
        status.get("worker_goals")
        or goal_state.get("worker_goals")
        or {}
    )
    off_goal_flags = (
        status.get("off_goal_flags")
        or goal_state.get("off_goal_flags")
        or []
    )
    supervisor = _read_json(run_dir / "supervisor.json", {}) or {}
    pid = _supervisor_pid(run_dir, status)
    count = max(0, min(int(include_recent), 500))

    all_messages = _tail_jsonl(run_dir / "messages.jsonl", max(count, 5_000))
    messages = all_messages[-count:] if count else []
    events = _tail_jsonl(run_dir / "events.jsonl", count)
    telemetry = _tail_jsonl(run_dir / "activity.jsonl", count)
    activities: List[Dict[str, Any]] = []
    last_turns: Dict[str, Dict[str, Any]] = {}
    turns_dir = run_dir / "turns"
    if turns_dir.is_dir():
        for path in sorted(turns_dir.glob("*.jsonl")):
            turns = _tail_jsonl(path, count)
            for turn in turns:
                turn["source"] = f"turns/{path.name}"
                turn["activity_type"] = "turn"
                activities.append(turn)
            if turns:
                last_turns[str(turns[-1].get("agent_id") or path.stem)] = turns[-1]
    for message in messages:
        message["source"] = "messages.jsonl"
        message["activity_type"] = "message"
        activities.append(message)
    for event in events:
        event["source"] = "events.jsonl"
        event["activity_type"] = "event"
        activities.append(event)
    for activity in telemetry:
        activity["source"] = "activity.jsonl"
        activity["activity_type"] = "telemetry"
        activities.append(activity)
    activities.sort(key=lambda item: str(
        item.get("ts")
        or item.get("deliveredAt")
        or item.get("updated_at")
        or f"{int(item.get('round', 0) or 0):08d}"
    ))

    promotion = _read_json(run_dir / "promotion-request.json", None)
    approval_request = _approval_request(run_dir)
    approval_decision = _read_json(run_dir / "approval" / "decision.json", None)
    approval_execution = _read_json(
        run_dir / "approval" / "execution.json",
        None,
    )
    operator_decision = _read_json(run_dir / "operator-decision.json", None)
    artifact_manifest = _read_json(run_dir / "deliverables" / "manifest.json", None)
    conclusion = _read_json(run_dir / "run-conclusion.json", None)
    research_commissions = _tail_jsonl(
        run_dir / "research-cell" / "commissions.jsonl",
        max(count, 500),
    )
    research_fragments = _tail_jsonl(
        run_dir / "research-cell" / "fragments.jsonl",
        max(count, 500),
    )
    research_decisions = _tail_jsonl(
        run_dir / "research-cell" / "decisions.jsonl",
        max(count, 500),
    )
    experiment_contracts = _tail_jsonl(
        run_dir / "experiment-loop" / "contracts.jsonl",
        max(count, 500),
    )
    experiment_trials = _tail_jsonl(
        run_dir / "experiment-loop" / "trials.jsonl",
        max(count, 500),
    )
    experiment_ledger = _experiment_ledger_status(
        run_dir / "experiment-loop" / "trials.jsonl",
    )
    candidate_progress = _read_json(
        run_dir / "candidate-progress.json",
        None,
    )
    topology = _topology_snapshot(run, status)
    live, active_agent_ids = process_group_activity(
        pid,
        run_dir,
        (agent["id"] for agent in topology.get("agents", [])),
    )
    active_agents = set(active_agent_ids)
    for agent in topology.get("agents", []):
        logical_state = agent.get("state", "unknown")
        agent["logical_state"] = logical_state
        last = last_turns.get(agent["id"])
        if last:
            reply = last.get("reply") or {}
            agent["last_turn"] = {
                "round": last.get("round"),
                "status": last.get("status"),
                "duration_ms": last.get("duration_ms"),
                "summary": reply.get("summary") if isinstance(reply, dict) else None,
                "usage": last.get("usage") or {},
            }
            if agent.get("state") == "unknown":
                if last.get("status") == "failed":
                    agent["state"] = "blocked"
                elif isinstance(reply, dict) and reply.get("state"):
                    agent["state"] = reply["state"]
                else:
                    agent["state"] = "idle"
        if agent["id"] in topology.get("worker_ids", []):
            goal = worker_goals.get(agent["id"])
            agent["goal"] = goal if isinstance(goal, dict) else None
            primary = topology.get("primary_manager_by_worker", {}).get(agent["id"])
            assignments = [
                message for message in all_messages
                if message.get("status", "delivered") == "delivered"
                and message.get("from") == primary
                and message.get("to") == agent["id"]
                and message.get("tag") in {"plan", "handoff", "review"}
                and message.get("workItem")
                and message.get("risk") in {"routine", "high", "release"}
            ]
            agent["assignment"] = assignments[-1] if assignments else None
            if not goal and not assignments and not last:
                agent["state"] = "awaiting_goal"
        if agent["id"] in topology.get("research_specialist_ids", []):
            director = topology.get("research_director_id")
            assignments = [
                message for message in all_messages
                if message.get("status", "delivered") == "delivered"
                and message.get("from") == director
                and message.get("to") == agent["id"]
                and message.get("workItem")
                and message.get("risk") in {"routine", "high", "release"}
            ]
            agent["assignment"] = assignments[-1] if assignments else None
            if not assignments and not last:
                agent["state"] = "awaiting_assignment"
        if agent["id"] in active_agents:
            agent["state"] = "working"
        elif agent["id"] in set(
            status.get("experiment_loop_halted_workers") or []
        ):
            agent["state"] = "blocked"
        elif agent.get("state") not in {
            "awaiting_assignment", "awaiting_goal", "blocked", "done",
        }:
            # A model returning "working" means it wants another scheduled
            # turn; it does not mean a provider subprocess is still executing.
            agent["state"] = "idle"

    controls = _control_records(run_dir)
    completed_turns = status.get("completed_turns")
    if completed_turns is None:
        completed_turns = sum(
            1
            for activity in activities
            if activity.get("activity_type") == "turn"
            and activity.get("status") == "completed"
        )
    return {
        **status,
        "run_id": status.get("run_id") or run.get("run_id") or run_id,
        "run_dir": str(run_dir),
        "max_rounds": status.get("max_rounds") or run.get("max_rounds"),
        "max_closeout_rounds": (
            status.get("max_closeout_rounds")
            or run.get("max_closeout_rounds")
        ),
        "completed_turns": completed_turns,
        "attempted_turns": status.get("attempted_turns", len(last_turns)),
        "failed_turns": status.get(
            "failed_turns",
            sum(
                1
                for activity in activities
                if activity.get("activity_type") == "turn"
                and activity.get("status") == "failed"
            ),
        ),
        "mission": run.get("mission"),
        "created_at": run.get("created_at"),
        "pid": status.get("pid") or pid,
        "provider": run.get("provider") or status.get("provider"),
        "host_provider": run.get("host_provider") or status.get("host_provider"),
        "provider_assignments": (
            run.get("provider_assignments")
            or status.get("provider_assignments")
            or {}
        ),
        "human_promotion_required": bool(
            run.get("human_promotion_required")
            or status.get("human_promotion_required")
        ),
        "process": {
            "pid": pid,
            "live": live,
            "active_agents": active_agent_ids,
            "supervisor": supervisor,
        },
        "topology_graph": topology,
        "research_cell": {
            "director_id": topology.get("research_director_id"),
            "specialist_ids": topology.get("research_specialist_ids", []),
            "commissions": research_commissions,
            "fragments": research_fragments,
            "decisions": research_decisions,
        },
        "experiment_loop": {
            "enabled": bool(
                run.get("experiment_loop", {}).get("enabled")
                or status.get("experiment_loop_enabled")
            ),
            "policy": (
                run.get("experiment_loop", {}).get("source_policy")
                or run.get("experiment_policy")
            ),
            "contracts": experiment_contracts,
            "trials": experiment_trials,
            "ledger": experiment_ledger,
            "active_workers": (
                status.get("experiment_loop_active_workers") or []
            ),
            "halted_workers": (
                status.get("experiment_loop_halted_workers") or []
            ),
            "candidate_progress": candidate_progress,
        },
        "worker_goals": worker_goals,
        "off_goal_flags": off_goal_flags,
        "messages": messages,
        "events": events,
        "telemetry": telemetry,
        "activities": activities[-count:] if count else [],
        "controls": controls,
        "control_capabilities": {
            "protocol": (
                run.get("control_protocol")
                or status.get("control_protocol")
            ),
            "message": bool(
                run.get("control_protocol")
                or status.get("control_protocol")
            ) and status.get("status") not in TERMINAL_STATUSES,
            "pause": bool(
                run.get("control_protocol")
                or status.get("control_protocol")
            ) and status.get("status") in {"running", "starting"},
            "resume": bool(
                run.get("control_protocol")
                or status.get("control_protocol")
            ) and status.get("status") == "paused",
            "cancel": status.get("status") not in TERMINAL_STATUSES or live is True,
        },
        "promotion_request": promotion,
        "approval_request": approval_request,
        "approval_decision": approval_decision,
        "approval_execution": approval_execution,
        "operator_decision": operator_decision,
        "approval_capabilities": {
            "approve": (
                isinstance(approval_request, dict)
                and approval_request.get("schema") == APPROVAL_REQUEST_SCHEMA
                and approval_request.get("status")
                == "awaiting_human_authorization"
                and not (
                    isinstance(approval_execution, dict)
                    and approval_execution.get("status") == "applied"
                )
                and not (
                    isinstance(operator_decision, dict)
                    and operator_decision.get("decision") == "rejected"
                )
            ),
            "reject": (
                status.get("status") in TERMINAL_STATUSES
                and not (
                    isinstance(operator_decision, dict)
                    and operator_decision.get("decision") == "rejected"
                )
            ),
            "action": (
                (approval_request.get("action") or {}).get("type")
                if isinstance(approval_request, dict)
                else None
            ),
        },
        "artifact_manifest": artifact_manifest,
        "conclusion": conclusion,
    }


def list_organization_runs(
    working_directory: str,
    limit: int = 100,
) -> Dict[str, Any]:
    """List durable organization runs for one project."""
    root = _organization_root(working_directory)
    if root is None:
        return {"status": "not_found", "runs": []}
    project_root = discover_project_root(
        Path(working_directory).expanduser().resolve(),
    )
    runs: List[Dict[str, Any]] = []
    if root.is_dir():
        paths = sorted(
            (path for path in root.iterdir() if path.is_dir()),
            key=lambda path: path.stat().st_mtime,
            reverse=True,
        )
        for run_dir in paths[:max(1, min(int(limit), 500))]:
            status = _read_json(run_dir / "status.json", {}) or {}
            run = _read_json(run_dir / "run.json", {}) or _read_json(
                run_dir / "request.json", {},
            ) or {}
            approval_request = _approval_request(run_dir)
            approval_execution = _read_json(
                run_dir / "approval" / "execution.json",
                None,
            )
            pid = _supervisor_pid(run_dir, status)
            runs.append({
                "run_id": status.get("run_id") or run.get("run_id") or run_dir.name,
                "run_dir": str(run_dir),
                "status": status.get("status", "unknown"),
                "round": status.get("round", 0),
                "max_rounds": status.get("max_rounds") or run.get("max_rounds"),
                "phase": status.get("phase"),
                "closeout_round": status.get("closeout_round", 0),
                "max_closeout_rounds": (
                    status.get("max_closeout_rounds")
                    or run.get("max_closeout_rounds")
                ),
                "detail": status.get("detail"),
                "updated_at": status.get("updated_at"),
                "created_at": run.get("created_at"),
                "topology": run.get("topology") or status.get("topology"),
                "mission_origin": (
                    run.get("mission_origin")
                    or status.get("mission_origin")
                ),
                "continuation_from_run_id": (
                    run.get("continuation_from_run_id")
                    or status.get("continuation_from_run_id")
                ),
                "continuation_conclusion_sha256": (
                    run.get("continuation_conclusion_sha256")
                    or status.get("continuation_conclusion_sha256")
                ),
                "provider": run.get("provider") or status.get("provider"),
                "host_provider": run.get("host_provider") or status.get("host_provider"),
                "human_promotion_required": bool(
                    run.get("human_promotion_required")
                    or status.get("human_promotion_required")
                ),
                "process_live": process_group_is_live(pid, run_dir),
                "control_protocol": (
                    run.get("control_protocol")
                    or status.get("control_protocol")
                ),
                "approval_pending": bool(
                    isinstance(approval_request, dict)
                    and approval_request.get("schema")
                    == APPROVAL_REQUEST_SCHEMA
                    and approval_request.get("status")
                    == "awaiting_human_authorization"
                    and not (
                        isinstance(approval_execution, dict)
                        and approval_execution.get("status") == "applied"
                    )
                ),
            })
    outcomes = None
    if project_root is not None:
        try:
            from .organization_outcomes import summarize_outcomes

            outcomes = summarize_outcomes(project_root)
        except Exception:
            outcomes = None
    return {
        "status": "ok",
        "project_root": str(project_root) if project_root else None,
        "runs": runs,
        # The value plane: how many terminal runs produced anything a human
        # merged or a successor consumed, and what the rest cost. None until
        # the first post-ledger run records an outcome.
        "outcomes": outcomes,
    }


def _request_id(
    run_id: str,
    action: str,
    idempotency_key: Optional[str],
) -> str:
    if idempotency_key:
        digest = hashlib.sha256(
            f"{run_id}\0{action}\0{idempotency_key}".encode("utf-8"),
        ).hexdigest()[:24]
        return f"ctrl_{digest}"
    return f"ctrl_{uuid.uuid4().hex}"


def _validate_target(run: Dict[str, Any], target: Optional[str]) -> str:
    from .organization import get_topology

    value = str(target or "").strip()
    if not value:
        raise ValueError("message control requires a target")
    aliases = {"all", "lead", "finalizer", "managers", "workers", "integrators"}
    if value in aliases:
        return value
    topology = get_topology(str(run.get("topology") or "flat"))
    if value not in {agent.agent_id for agent in topology.agents}:
        raise ValueError(f"unknown organization target: {value}")
    return value


def queue_control_request(
    working_directory: str,
    run_id: str,
    action: str,
    *,
    target: Optional[str] = None,
    content: Optional[str] = None,
    tag: str = "plan",
    idempotency_key: Optional[str] = None,
    requested_by: str = "human-operator",
    allow_terminal_cancel: bool = False,
) -> Dict[str, Any]:
    """Queue one idempotent command for application at a safe boundary."""
    action = str(action).strip().lower()
    if action not in CONTROL_ACTIONS:
        raise ValueError(f"action must be one of {sorted(CONTROL_ACTIONS)}")
    run_dir = _resolve_run(working_directory, run_id)
    if run_dir is None:
        return {"status": "not_found", "run_id": run_id}
    status = _read_json(run_dir / "status.json", {}) or {}
    run = _read_json(run_dir / "run.json", {}) or _read_json(
        run_dir / "request.json", {},
    ) or {}
    if (
        status.get("status") in TERMINAL_STATUSES
        and not (action == "cancel" and allow_terminal_cancel)
    ):
        return {
            "status": "rejected",
            "run_id": run.get("run_id") or run_id,
            "detail": f"run is terminal: {status.get('status')}",
        }
    if action in {"message", "pause", "resume"} and not (
        run.get("control_protocol") or status.get("control_protocol")
    ):
        return {
            "status": "unsupported",
            "run_id": run.get("run_id") or run_id,
            "detail": "run predates the durable steering protocol",
        }
    normalized_target = None
    normalized_content = None
    if action == "message":
        from .organization import MESSAGE_TAGS

        normalized_target = _validate_target(run, target)
        normalized_content = str(content or "").strip()
        if not normalized_content:
            raise ValueError("message control requires non-empty content")
        if len(normalized_content) > MAX_OPERATOR_MESSAGE_CHARS:
            raise ValueError(
                f"message exceeds {MAX_OPERATOR_MESSAGE_CHARS} characters",
            )
        if tag not in MESSAGE_TAGS:
            raise ValueError(f"tag must be one of {sorted(MESSAGE_TAGS)}")
    else:
        tag = "status"

    resolved_run_id = str(run.get("run_id") or status.get("run_id") or run_id)
    control_id = _request_id(resolved_run_id, action, idempotency_key)
    requests_dir = run_dir / "control" / "requests"
    request_path = requests_dir / f"{control_id}.json"
    existing = _read_json(request_path, None)
    if isinstance(existing, dict):
        acknowledgement = _read_json(
            run_dir / "control" / "acknowledgements" / f"{control_id}.json",
            None,
        )
        return {
            **existing,
            "status": (
                acknowledgement.get("status", "acknowledged")
                if isinstance(acknowledgement, dict)
                else "queued"
            ),
            "acknowledgement": acknowledgement,
            "idempotent_replay": True,
        }
    request = {
        "schema": CONTROL_SCHEMA,
        "id": control_id,
        "run_id": resolved_run_id,
        "action": action,
        "target": normalized_target,
        "tag": tag,
        "content": normalized_content,
        "requested_by": str(requested_by or "human-operator"),
        "requested_at": _utc_now(),
        "idempotency_key": idempotency_key,
    }
    _atomic_write_json(request_path, request)
    return {**request, "status": "queued", "run_dir": str(run_dir)}


def pending_control_requests(run_dir: Path) -> List[Dict[str, Any]]:
    """Return well-formed requests that do not yet have acknowledgements."""
    requests_dir = run_dir / "control" / "requests"
    acknowledgements_dir = run_dir / "control" / "acknowledgements"
    if not requests_dir.is_dir():
        return []
    pending: List[Dict[str, Any]] = []
    for path in sorted(requests_dir.glob("*.json")):
        request = _read_json(path, None)
        if not isinstance(request, dict) or request.get("schema") != CONTROL_SCHEMA:
            continue
        control_id = str(request.get("id") or "")
        if not control_id or (acknowledgements_dir / f"{control_id}.json").exists():
            continue
        pending.append(request)
    pending.sort(key=lambda value: (
        str(value.get("requested_at") or ""),
        str(value.get("id") or ""),
    ))
    return pending


def acknowledge_control_request(
    run_dir: Path,
    request: Dict[str, Any],
    status: str,
    detail: str,
    *,
    applied_round: Optional[int] = None,
    targets: Optional[Iterable[str]] = None,
) -> Dict[str, Any]:
    acknowledgement = {
        "schema": CONTROL_SCHEMA,
        "id": request.get("id"),
        "run_id": request.get("run_id"),
        "action": request.get("action"),
        "status": status,
        "detail": detail,
        "applied_round": applied_round,
        "targets": list(targets or []),
        "acknowledged_at": _utc_now(),
    }
    _atomic_write_json(
        run_dir / "control" / "acknowledgements" / f"{request['id']}.json",
        acknowledgement,
    )
    return acknowledgement


def _approval_request(run_dir: Path) -> Optional[Dict[str, Any]]:
    request = _read_json(run_dir / "approval-request.json", None)
    if isinstance(request, dict):
        return request
    request = _read_json(run_dir / "promotion-request.json", None)
    return request if isinstance(request, dict) else None


def _verify_approval_request(request: Dict[str, Any]) -> str:
    if request.get("schema") != APPROVAL_REQUEST_SCHEMA:
        raise RuntimeError("unsupported or legacy approval request schema")
    expected = str(request.get("request_sha256") or "")
    if not expected:
        raise RuntimeError("approval request has no request_sha256")
    canonical_value = {
        key: value
        for key, value in request.items()
        if key != "request_sha256"
    }
    canonical = json.dumps(
        canonical_value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")
    actual = hashlib.sha256(canonical).hexdigest()
    if actual != expected:
        raise RuntimeError(
            "approval request hash mismatch; the staged packet changed after "
            "the organization bound it"
        )
    return actual


def _git_text(project_root: Path, args: List[str]) -> str:
    proc = subprocess.run(
        ["git", *args],
        cwd=project_root,
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode != 0:
        raise RuntimeError(
            f"git {' '.join(args)} failed: "
            f"{(proc.stderr or proc.stdout).strip()}"
        )
    return proc.stdout.strip()


def _require_approval_checkpoint(
    project_root: Path,
    request: Dict[str, Any],
) -> str:
    base_commit = str(request.get("base_commit") or "")
    if not base_commit:
        raise RuntimeError("approval request does not bind a base commit")
    head = _git_text(project_root, ["rev-parse", "HEAD"])
    if head != base_commit:
        raise RuntimeError(
            "project HEAD changed after approval staging; expected "
            f"{base_commit}, found {head}. Start a new review packet."
        )
    tracked = _git_text(
        project_root,
        ["status", "--porcelain", "--untracked-files=no"],
    )
    if tracked:
        raise RuntimeError(
            "project has tracked uncommitted changes; approval execution "
            "requires a clean checkpoint"
        )
    exact_candidate = str(
        request.get("report_candidate")
        or request.get("proposed_promotion_candidate")
        or ""
    )
    if not exact_candidate:
        raise RuntimeError("approval request does not bind an exact candidate")
    _git_text(
        project_root,
        ["cat-file", "-e", f"{exact_candidate}^{{commit}}"],
    )
    return head


def _approval_decision(
    request: Dict[str, Any],
    *,
    requested_by: str,
    idempotency_key: Optional[str],
) -> Dict[str, Any]:
    decision: Dict[str, Any] = {
        "schema": APPROVAL_DECISION_SCHEMA,
        "version": 1,
        "run_id": request.get("run_id"),
        "request_sha256": request.get("request_sha256"),
        "request_kind": request.get("request_kind"),
        "report_candidate": request.get("report_candidate"),
        "promotion_candidate": request.get("proposed_promotion_candidate"),
        "decision": "approved",
        "decided_by": str(requested_by or "human-operator"),
        "decided_at": _utc_now(),
        "idempotency_key": idempotency_key,
        "authorization_limits": list(
            request.get("authorization_limits")
            or request.get("authorization_required_for")
            or []
        ),
    }
    canonical = json.dumps(
        decision,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")
    decision["decision_sha256"] = hashlib.sha256(canonical).hexdigest()
    return decision


def _apply_gate_proposal(
    project_root: Path,
    request: Dict[str, Any],
    gate: Dict[str, Any],
) -> Dict[str, Any]:
    """Apply a human-ratified gate proposal from the exact approved candidate.

    The org stages proposed gate files (predicate policy edits, fixtures,
    scorer wiring) under its artifact staging, where its write scope allows;
    only this function, running on the human approval click, may place them
    at their protected-path targets. Content comes from the approved
    candidate's git tree byte-for-byte, never from the working directory.
    """
    candidate = str(
        request.get("gate_source_candidate")
        or request.get("report_candidate")
        or ""
    )
    applied: List[str] = []
    for entry in gate.get("files") or []:
        source = str(entry.get("path") or "")
        target = str(entry.get("target") or "")
        parts = Path(target).parts
        if not target or Path(target).is_absolute() or ".." in parts:
            raise RuntimeError(
                f"gate proposal target escapes the repository: {target!r}"
            )
        blob = subprocess.run(
            ["git", "cat-file", "-p", f"{candidate}:{source}"],
            cwd=project_root, capture_output=True, check=False,
        )
        if blob.returncode != 0:
            raise RuntimeError(
                f"gate proposal file is not in the approved candidate: {source}"
            )
        destination = project_root / target
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(blob.stdout)
        applied.append(target)
    if not applied:
        return {}
    _git_text(project_root, ["add", "--", *applied])
    _git_text(project_root, [
        "-c", "user.name=reccli", "-c", "user.email=reccli@local",
        "-c", "commit.gpgsign=false",
        "commit", "--no-verify", "--no-gpg-sign", "-m",
        (
            f"reccli: ratify gate {gate.get('predicate_id')} "
            f"from run {request.get('run_id')}"
        ),
    ])
    return {
        "gate_applied": True,
        "gate_predicate_id": gate.get("predicate_id"),
        "gate_applied_commit": _git_text(
            project_root, ["rev-parse", "HEAD"],
        ).strip(),
        "gate_files": applied,
    }


def stage_approval_from_record(
    working_directory: str,
    run_id: str,
    *,
    report_candidate: str,
    gate_candidate: Optional[str] = None,
    allow_no_gate: bool = False,
) -> Dict[str, Any]:
    """Derive a pending-human approval packet from a terminal run's record.

    A host defect can leave a run's closure state lagging its recorded
    approval chain (run thirteen held three durable exact-candidate NO_VETOs
    and no staged packet). Re-performing a fully performed, formally approved
    ceremony is waste; re-deriving the packet from the durable record is
    bookkeeping. Authority is not assumed: the recorded decisions are
    re-verified from messages.jsonl, the candidate from the object store, and
    the gate proposal through the same validator a live run uses.
    """
    run_dir = _resolve_run(working_directory, run_id)
    if run_dir is None:
        return {"status": "not_found", "run_id": run_id}
    existing = _read_json(run_dir / "approval-request.json", None)
    if isinstance(existing, dict) and existing.get("request_sha256"):
        return {
            "status": "already_staged",
            "run_id": run_id,
            "request_sha256": existing["request_sha256"],
        }
    status = _read_json(run_dir / "status.json", {}) or {}
    if status.get("status") not in TERMINAL_STATUSES:
        raise RuntimeError(
            "derivation is available only for terminal runs"
        )
    run_record = (
        _read_json(run_dir / "run.json", None)
        or _read_json(run_dir / "request.json", None)
        or {}
    )
    conclusion = _read_json(run_dir / "run-conclusion.json", {}) or {}
    project_root = Path(
        run_record.get("project_root") or working_directory,
    ).expanduser().resolve()
    exact = str(report_candidate or "").strip().lower()
    if len(exact) != 40 or any(c not in "0123456789abcdef" for c in exact):
        raise ValueError("report_candidate must be one exact 40-hex commit")
    _git_text(project_root, ["cat-file", "-e", f"{exact}^{{commit}}"])
    if _git_text(
        project_root, ["status", "--porcelain", "--untracked-files=no"],
    ).strip():
        raise RuntimeError(
            "derivation requires a clean tracked checkout"
        )

    from .organization import OrganizationRunner, Workspace, get_topology

    topology = get_topology(str(run_record.get("topology") or "flat"))
    approvals: List[Dict[str, Any]] = []
    vetoes: List[Dict[str, Any]] = []
    messages_path = run_dir / "messages.jsonl"
    for line in (
        messages_path.read_text(encoding="utf-8").splitlines()
        if messages_path.is_file() else []
    ):
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            continue
        if record.get("status") != "delivered":
            continue
        if str(record.get("candidate") or "").lower() != exact:
            continue
        # The live host records auditor dispositions under both tags
        # depending on whether normalization fired; the record's reader must
        # accept everything the record's writer produced.
        if record.get("tag") not in {"decision", "review"}:
            continue
        sender = str(record.get("from") or "")
        if (
            sender not in topology.final_reviewer_pool
            and sender not in topology.required_approvers
        ):
            continue
        from .organization import disposition_marker

        marker = disposition_marker(record.get("content"))
        entry = {
            "sender": sender,
            "round": record.get("round"),
            "content": record.get("content"),
        }
        if marker in {"BLOCKED", "VETO"}:
            vetoes.append(entry)
        elif marker in {"NO_VETO", "REVIEWED", "APPROVED"}:
            approvals.append(entry)
    if vetoes:
        raise RuntimeError(
            "the durable record holds a standing veto on this exact "
            f"candidate: {vetoes[-1]['sender']} round {vetoes[-1]['round']}"
        )
    if not approvals:
        raise RuntimeError(
            "the durable record holds no exact-candidate release approval "
            "from the reviewer pool; derivation cannot manufacture authority"
        )

    runner = OrganizationRunner(
        project_root,
        str(run_record.get("mission") or "derived"),
        "claude",
        topology.topology_id,
        str(run_record.get("run_id") or run_id),
        run_dir,
        admission=run_record.get("admission"),
    )
    probe_workspace = Workspace(project_root, "derived", "derived", project_root, [])
    # The gate proposal may live in a different candidate than the approved
    # dossier (run thirteen's did). Extracting only from the report candidate
    # silently degraded the packet to a pure checkpoint species, and the
    # first click faithfully executed the wrong thing. A null gate is now a
    # refusal unless deliberately requested.
    exact_gate = str(gate_candidate or exact).strip().lower()
    if len(exact_gate) != 40 or any(
        c not in "0123456789abcdef" for c in exact_gate
    ):
        raise ValueError("gate_candidate must be one exact 40-hex commit")
    _git_text(project_root, ["cat-file", "-e", f"{exact_gate}^{{commit}}"])
    gate_proposal = runner._extract_gate_proposal(
        exact_gate, workspace=probe_workspace,
    )
    if isinstance(gate_proposal, dict) and gate_proposal.get("error"):
        raise RuntimeError(
            f"the staged gate proposal fails validation: {gate_proposal['error']}"
        )
    if gate_proposal is None and not allow_no_gate:
        raise RuntimeError(
            f"candidate {exact_gate} stages no gate proposal; pass "
            "gate_candidate naming the commit that carries it, or "
            "allow_no_gate to deliberately stage a pure checkpoint packet"
        )

    base_commit = _git_text(project_root, ["rev-parse", "HEAD"]).strip()
    request: Dict[str, Any] = {
        "schema": APPROVAL_REQUEST_SCHEMA,
        "version": 1,
        "created_at": _utc_now(),
        "run_id": str(run_record.get("run_id") or run_id),
        "request_kind": "checkpoint_continuation",
        "title": "Derived checkpoint awaiting your decision",
        "question": (
            "Ratify the exact recorded approval chain and continue the "
            "mission in a fresh run?"
        ),
        "status": "awaiting_human_authorization",
        "canonical_effects_applied": False,
        "base_commit": base_commit,
        "report_candidate": exact,
        "derivation": {
            "staged_by": "host-derivation",
            "approvals": approvals,
            "reason": (
                "closure state lagged the recorded approval chain; packet "
                "re-derived from the durable record"
            ),
        },
        "gate_proposal": gate_proposal,
        "gate_source_candidate": (
            exact_gate if gate_proposal is not None else None
        ),
        "successor_admission": conclusion.get("proposed_successor_admission"),
        "conclusion": {
            key: conclusion.get(key)
            for key in (
                "summary", "conclusive_findings", "evidence_and_tests",
                "unresolved", "next_action", "limitations",
            )
        },
        "action": {"type": "start_successor", "remote_push": False},
        "continuation": {
            "provider": run_record.get("provider_requested")
            or run_record.get("provider") or "auto",
            "topology": topology.topology_id,
            "max_rounds": int(run_record.get("max_rounds") or 6),
            "max_concurrency": int(run_record.get("max_concurrency") or 5),
            "turn_timeout_seconds": int(
                run_record.get("turn_timeout_seconds") or 2400,
            ),
            "model": run_record.get("model") or "auto",
            "evidence_paths": list(run_record.get("evidence_paths") or []),
            "protected_paths": list(run_record.get("protected_paths") or []),
            "context_manifest": run_record.get("context_manifest"),
            "experiment_policy": run_record.get("experiment_policy"),
            "max_experiments": int(run_record.get("max_experiments") or 0),
        },
        "original_mission": str(run_record.get("mission") or ""),
        "authorization_limits": [
            "Approval applies only to the exact report candidate and "
            "recorded approval chain in this request.",
            "Approval does not authorize remote push or mutation of "
            "protected evidence beyond the ratified gate files.",
        ],
    }
    canonical = json.dumps(
        request, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
    ).encode("utf-8")
    request["request_sha256"] = hashlib.sha256(canonical).hexdigest()
    _atomic_write_json(run_dir / "approval-request.json", request)
    return {
        "status": "staged",
        "run_id": request["run_id"],
        "request_sha256": request["request_sha256"],
        "approvals": approvals,
        "gate_predicate_id": (
            gate_proposal.get("predicate_id")
            if isinstance(gate_proposal, dict) else None
        ),
    }


def _resolve_successor_admission(
    project_root: Path,
    request: Dict[str, Any],
    parent_run_id: str,
    approver: str,
) -> Dict[str, Any]:
    """Resolve the admission the click's auto-launched successor runs under.

    In order of authority: the packet's own successor_admission (a governance
    run staging a gate proposes the implementation contract there); the
    parent's terminal conclusion proposed_successor_admission (covers packets
    staged by supervisors that predate the packet field); the parent's
    recorded contract. Carrying a governance parent's own already-satisfied
    contract is the last resort because it makes the successor's lead no_op
    on arrival.
    """
    from .organization_admission import admission_for_approved_successor

    parent_dir = (
        project_root / "devsession" / "agent-organizations"
        / _safe_name(parent_run_id)
    )
    for candidate_block in (
        request.get("successor_admission"),
        (
            _read_json(parent_dir / "run-conclusion.json", {}) or {}
        ).get("proposed_successor_admission"),
    ):
        if isinstance(candidate_block, dict):
            try:
                return admission_for_approved_successor(
                    candidate_block, parent_run_id, approver,
                )
            except ValueError:
                continue
    parent_admission = (
        _read_json(parent_dir / "admission.json", None)
        if (parent_dir / "admission.json").is_file() else None
    )
    return admission_for_approved_successor(
        parent_admission, parent_run_id, approver,
    )


def _start_approved_successor(
    project_root: Path,
    request: Dict[str, Any],
    decision_path: Path,
) -> Dict[str, Any]:
    from .organization import create_run_request
    from .organization_admission import admission_for_approved_successor
    from .organization_launch import launch_organization_worker

    continuation = request.get("continuation") or {}
    original_mission = str(request.get("original_mission") or "").strip()
    if not original_mission:
        raise RuntimeError("approval request has no original mission")
    decision_sha = str(
        (_read_json(decision_path, {}) or {}).get("decision_sha256") or "",
    )
    gate = request.get("gate_proposal")
    gate_effect: Dict[str, Any] = {}
    if isinstance(gate, dict) and not gate.get("error"):
        gate_effect = _apply_gate_proposal(project_root, request, gate)
    gate_note = (
        (
            "\nYour approval also ratified and locally applied the proposed "
            f"gate `{gate_effect.get('gate_predicate_id')}` at commit "
            f"`{gate_effect.get('gate_applied_commit')}`; the successor "
            "works against it.\n"
        )
        if gate_effect.get("gate_applied") else ""
    )
    mission = f"""# Human-approved continuation

The operator approved the exact checkpoint request from predecessor run
`{request.get('run_id')}`.

- Approval request SHA-256: `{request.get('request_sha256')}`
- Approval decision SHA-256: `{decision_sha}`
- Reviewed report candidate: `{request.get('report_candidate')}`
- Durable decision source: `{decision_path}` (identified above by hash;
  intentionally not mounted as run evidence, because run output may never
  be cited as immutable evidence, ratification records included)

Treat only that exact decision as approved. It does not authorize remote push,
mutation of protected evidence, or scientific claims beyond the reviewed
dossier. This is a fresh organization; do not attempt to resume the predecessor
supervisor.
{gate_note}
# Original mission

{original_mission}
"""
    # The successor consumes the decision's IDENTITY (the SHA in its
    # mission), never the file: the decision record lives under the run tree,
    # and the evidence-authority guard categorically refuses run output as
    # immutable evidence. Mounting it made the first-ever ratification click
    # veto itself on two individually-correct guards.
    evidence_paths = list(continuation.get("evidence_paths") or [])
    decision_record = _read_json(decision_path, {}) or {}
    approver = str(
        decision_record.get("approved_by")
        or decision_record.get("approver")
        or "human-operator"
    )
    parent_run_id = str(request.get("run_id") or "unknown")
    successor_admission = _resolve_successor_admission(
        project_root, request, parent_run_id, approver,
    )
    successor = create_run_request(
        working_directory=str(project_root),
        mission=mission,
        provider=str(continuation.get("provider") or "auto"),
        topology=str(continuation.get("topology") or "flat"),
        max_rounds=int(continuation.get("max_rounds") or 8),
        max_concurrency=int(continuation.get("max_concurrency") or 5),
        turn_timeout_seconds=int(
            continuation.get("turn_timeout_seconds") or 1200,
        ),
        model=str(continuation.get("model") or "auto"),
        evidence_paths=evidence_paths,
        protected_paths=list(continuation.get("protected_paths") or []),
        context_manifest=continuation.get("context_manifest"),
        experiment_policy=continuation.get("experiment_policy"),
        max_experiments=int(continuation.get("max_experiments") or 0),
        admission=successor_admission,
    )
    successor.update({
        "parent_run_id": request.get("run_id"),
        "approval_request_sha256": request.get("request_sha256"),
        "approval_decision": str(decision_path),
        "approval_decision_sha256": decision_sha,
    })
    _atomic_write_json(Path(successor["run_dir"]) / "request.json", successor)
    successor_status_path = Path(successor["run_dir"]) / "status.json"
    successor_status = _read_json(successor_status_path, {}) or {}
    successor_status.update({
        "parent_run_id": request.get("run_id"),
        "approval_request_sha256": request.get("request_sha256"),
        "approval_decision_sha256": decision_sha,
    })
    _atomic_write_json(successor_status_path, successor_status)
    launched = launch_organization_worker(successor)
    return {
        "action": "start_successor",
        "successor_run_id": successor["run_id"],
        "successor_run_dir": successor["run_dir"],
        "successor_pid": launched["pid"],
        **gate_effect,
    }


def _apply_approved_promotion(
    project_root: Path,
    request: Dict[str, Any],
) -> Dict[str, Any]:
    candidate = str(request.get("proposed_promotion_candidate") or "")
    if not candidate:
        raise RuntimeError("promotion request has no proposed candidate")
    _git_text(project_root, ["cat-file", "-e", f"{candidate}^{{commit}}"])
    base_commit = str(request["base_commit"])
    expected_paths = sorted(
        str(path) for path in request.get("changed_paths", [])
    )
    actual_paths = sorted(filter(
        None,
        _git_text(
            project_root,
            ["diff", "--name-only", f"{base_commit}..{candidate}"],
        ).splitlines(),
    ))
    if actual_paths != expected_paths:
        raise RuntimeError(
            "promotion candidate paths no longer match the approval packet"
        )
    _git_text(project_root, ["merge", "--ff-only", candidate])
    return {
        "action": "fast_forward_local",
        "applied_commit": _git_text(project_root, ["rev-parse", "HEAD"]),
        "remote_push": False,
    }


def approve_organization_request(
    working_directory: str,
    run_id: str,
    *,
    request_sha256: Optional[str] = None,
    idempotency_key: Optional[str] = None,
    requested_by: str = "human-operator",
) -> Dict[str, Any]:
    """Approve one exact staged packet and execute its declared local action."""
    run_dir = _resolve_run(working_directory, run_id)
    if run_dir is None:
        return {"status": "not_found", "run_id": run_id}
    request = _approval_request(run_dir)
    if request is None:
        return {
            "status": "not_available",
            "run_id": run_id,
            "detail": "run has no staged approval request",
        }
    operator_decision = _read_json(run_dir / "operator-decision.json", None)
    if (
        isinstance(operator_decision, dict)
        and operator_decision.get("decision") == "rejected"
    ):
        raise RuntimeError(
            "the human operator already rejected this run's candidate; "
            "approval is permanently unavailable"
        )
    actual_request_sha = _verify_approval_request(request)
    if request_sha256 and request_sha256 != actual_request_sha:
        raise RuntimeError(
            "approval button referenced a stale request hash; refresh the run"
        )
    status = _read_json(run_dir / "status.json", {}) or {}
    if status.get("status") not in TERMINAL_STATUSES:
        raise RuntimeError("approval is available only after the run is terminal")

    source_request = _read_json(run_dir / "request.json", {}) or {}
    resolved_run_id = str(
        source_request.get("run_id")
        or status.get("run_id")
        or run_id
    )
    if request.get("run_id") != resolved_run_id:
        raise RuntimeError(
            "approval request run identity does not match the durable run"
        )
    project_root = Path(
        source_request.get("project_root") or working_directory,
    ).expanduser().resolve()
    _require_approval_checkpoint(project_root, request)

    approval_dir = run_dir / "approval"
    approval_dir.mkdir(parents=True, exist_ok=True)
    lock_path = approval_dir / "execution.lock"
    try:
        lock_fd = os.open(
            lock_path,
            os.O_CREAT | os.O_EXCL | os.O_WRONLY,
            0o600,
        )
    except FileExistsError:
        return {
            "status": "processing",
            "run_id": run_id,
            "detail": "another approval execution is already in progress",
        }
    os.close(lock_fd)
    decision_path = approval_dir / "decision.json"
    execution_path = approval_dir / "execution.json"
    try:
        existing_decision = _read_json(decision_path, None)
        if isinstance(existing_decision, dict):
            if existing_decision.get("request_sha256") != actual_request_sha:
                raise RuntimeError(
                    "existing approval decision belongs to another request"
                )
            decision = existing_decision
        else:
            decision = _approval_decision(
                request,
                requested_by=requested_by,
                idempotency_key=idempotency_key,
            )
            _atomic_write_json(decision_path, decision)

        existing_execution = _read_json(execution_path, None)
        if (
            isinstance(existing_execution, dict)
            and existing_execution.get("status") == "applied"
        ):
            return {
                **existing_execution,
                "idempotent_replay": True,
                "approval_decision": decision,
            }

        execution: Dict[str, Any] = {
            "schema": "reccli.organization-approval-execution.v1",
            "run_id": request.get("run_id") or run_id,
            "request_sha256": actual_request_sha,
            "decision_sha256": decision.get("decision_sha256"),
            "status": "processing",
            "started_at": _utc_now(),
        }
        _atomic_write_json(execution_path, execution)
        action = str((request.get("action") or {}).get("type") or "")
        if action == "start_successor":
            effect = _start_approved_successor(
                project_root,
                request,
                decision_path,
            )
        elif action == "fast_forward_local":
            effect = _apply_approved_promotion(project_root, request)
        else:
            raise RuntimeError(f"unsupported approval action: {action or 'missing'}")
        execution.update({
            **effect,
            "status": "applied",
            "completed_at": _utc_now(),
        })
        _atomic_write_json(execution_path, execution)
        try:
            from .organization_outcomes import record_outcome_event

            if action == "fast_forward_local":
                record_outcome_event(
                    project_root, "promotion_applied", resolved_run_id,
                    candidate=request.get("proposed_promotion_candidate"),
                    applied_commit=effect.get("applied_commit"),
                    decided_by=requested_by,
                )
            else:
                record_outcome_event(
                    project_root, "candidate_used", resolved_run_id,
                    used_by=effect.get("successor_run_id"),
                    report_candidate=request.get("report_candidate"),
                    decided_by=requested_by,
                )
        except Exception:
            # The ledger measures decisions; it must never undo one.
            pass
        return {
            **execution,
            "approval_decision": decision,
            "run_dir": str(run_dir),
        }
    except Exception as exc:
        execution = _read_json(execution_path, {}) or {}
        execution.update({
            "schema": "reccli.organization-approval-execution.v1",
            "run_id": request.get("run_id") or run_id,
            "request_sha256": actual_request_sha,
            "status": "failed",
            "error": f"{type(exc).__name__}: {exc}",
            "completed_at": _utc_now(),
        })
        _atomic_write_json(execution_path, execution)
        raise
    finally:
        lock_path.unlink(missing_ok=True)


def reject_organization_candidate(
    working_directory: str,
    run_id: str,
    *,
    candidate: str,
    reason: str,
    idempotency_key: Optional[str] = None,
    requested_by: str = "human-operator",
) -> Dict[str, Any]:
    """Permanently reject one exact terminal-run candidate.

    Rejection never mutates the caller's repository.  It records the failed
    route as a compact durable decision, disables later approval, and gives a
    successor mission an explicit instruction not to revive the candidate.
    """
    run_dir = _resolve_run(working_directory, run_id)
    if run_dir is None:
        return {"status": "not_found", "run_id": run_id}
    status = _read_json(run_dir / "status.json", {}) or {}
    if status.get("status") not in TERMINAL_STATUSES:
        raise RuntimeError("rejection is available only after the run is terminal")
    exact_candidate = str(candidate or "").strip().lower()
    if (
        len(exact_candidate) != 40
        or any(char not in "0123456789abcdef" for char in exact_candidate)
    ):
        raise ValueError("candidate must be one exact 40-character Git commit")
    exact_reason = " ".join(str(reason or "").split())
    if not exact_reason:
        raise ValueError("rejection reason must not be empty")
    if len(exact_reason) > 4_000:
        raise ValueError("rejection reason exceeds 4000 characters")

    source_request = _read_json(run_dir / "request.json", {}) or {}
    resolved_run_id = str(
        source_request.get("run_id")
        or status.get("run_id")
        or run_id
    )
    conclusion_path = run_dir / "run-conclusion.json"
    conclusion_raw = conclusion_path.read_bytes()
    conclusion = json.loads(conclusion_raw)
    candidates = {
        str(record.get("candidate") or "").lower()
        for record in conclusion.get("candidates", [])
        if isinstance(record, dict)
        and record.get("kind") == "implementation"
    }
    approval_request = _approval_request(run_dir)
    if isinstance(approval_request, dict):
        candidates.update(
            str(value or "").lower()
            for value in (
                approval_request.get("verified_candidate"),
                approval_request.get("proposed_promotion_candidate"),
                approval_request.get("report_candidate"),
            )
            if value
        )
    if exact_candidate not in candidates:
        raise RuntimeError(
            "candidate is not an implementation or staged candidate from "
            "this terminal run"
        )
    project_root = Path(
        source_request.get("project_root") or working_directory,
    ).expanduser().resolve()
    _git_text(project_root, ["cat-file", "-e", f"{exact_candidate}^{{commit}}"])

    decision_path = run_dir / "operator-decision.json"
    existing = _read_json(decision_path, None)
    if isinstance(existing, dict):
        if (
            existing.get("decision") == "rejected"
            and existing.get("candidate") == exact_candidate
        ):
            return {
                "status": "rejected",
                "run_id": resolved_run_id,
                "candidate": exact_candidate,
                "decision": existing,
                "idempotent_replay": True,
            }
        raise RuntimeError(
            "this run already has a different immutable operator decision"
        )

    decision: Dict[str, Any] = {
        "schema": OPERATOR_DECISION_SCHEMA,
        "version": 1,
        "run_id": resolved_run_id,
        "terminal_status": status.get("status"),
        "candidate": exact_candidate,
        "decision": "rejected",
        "reason": exact_reason,
        "effect": (
            "Candidate promotion is permanently disabled. The candidate may "
            "remain only in the compact failed-attempt audit record and must "
            "not seed or satisfy a successor mission."
        ),
        "canonical_effects_applied": False,
        "conclusion_sha256": hashlib.sha256(conclusion_raw).hexdigest(),
        "approval_request_sha256": (
            approval_request.get("request_sha256")
            if isinstance(approval_request, dict)
            else None
        ),
        "decided_by": requested_by,
        "decided_at": _utc_now(),
        "idempotency_key": idempotency_key,
    }
    canonical = json.dumps(
        decision,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")
    decision["decision_sha256"] = hashlib.sha256(canonical).hexdigest()
    _atomic_write_json(decision_path, decision)
    try:
        from .organization_outcomes import record_outcome_event

        record_outcome_event(
            project_root, "promotion_rejected", resolved_run_id,
            candidate=exact_candidate,
            reason=exact_reason,
            decided_by=requested_by,
        )
    except Exception:
        pass
    return {
        "status": "rejected",
        "run_id": resolved_run_id,
        "candidate": exact_candidate,
        "canonical_effects_applied": False,
        "decision": decision,
        "run_dir": str(run_dir),
    }


def cancel_organization_run(
    working_directory: str,
    run_id: str,
    *,
    idempotency_key: Optional[str] = None,
    requested_by: str = "human-operator",
    process_group_liveness: Any = process_group_is_live,
) -> Dict[str, Any]:
    """Durably request cancellation and enforce process-group termination."""
    run_dir = _resolve_run(working_directory, run_id)
    if run_dir is None:
        return {"status": "not_found", "run_id": run_id}
    status_path = run_dir / "status.json"
    status = _read_json(status_path, {}) or {}
    was_terminal = status.get("status") in TERMINAL_STATUSES
    request = queue_control_request(
        working_directory,
        run_id,
        "cancel",
        idempotency_key=idempotency_key,
        requested_by=requested_by,
        allow_terminal_cancel=True,
    )
    (run_dir / "cancel.requested").write_text(_utc_now() + "\n", encoding="utf-8")
    pid = _supervisor_pid(run_dir, status)
    live = process_group_liveness(pid, run_dir)
    should_signal = (
        pid > 1
        and pid != os.getpid()
        and (not was_terminal or live is not False)
    )
    signalled = False
    if should_signal:
        try:
            os.killpg(pid, signal.SIGTERM)
            signalled = True
        except (ProcessLookupError, PermissionError):
            pass
    if isinstance(request, dict) and request.get("id"):
        acknowledge_control_request(
            run_dir,
            request,
            "signalled" if signalled else "acknowledged",
            (
                "Cancellation marker persisted and the live process group was signalled."
                if signalled
                else "Cancellation marker persisted; no live process group required signalling."
            ),
        )
    if not was_terminal:
        status.update({
            "status": "cancelled",
            "detail": (
                "Cancellation requested; process group termination signalled"
                if signalled
                else "Cancellation requested"
            ),
            "updated_at": _utc_now(),
            "run_id": status.get("run_id", run_id),
            "cancellation_requested": True,
            "process_group_signalled": signalled,
        })
        _atomic_write_json(status_path, status)
    return {
        "status": status.get("status") if was_terminal else "cancelled",
        "run_id": status.get("run_id", run_id),
        "run_dir": str(run_dir),
        "process_group_live": live,
        "process_group_signalled": signalled,
        "detail": (
            "Terminal status had a live process group; termination was enforced."
            if was_terminal and signalled
            else (
                "Run is terminal and no live organization process group remains."
                if was_terminal
                else "Cancellation requested."
            )
        ),
    }
