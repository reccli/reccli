"""RecCli-native multi-agent organization runner.

The runner intentionally dispatches the installed Claude Code and Codex CLIs.
It does not use model API keys.  Each organization member owns a resumable
provider session and an isolated Git worktree.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shlex
import shutil
import stat
import subprocess
import tempfile
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any, Dict, List, Optional, Set, Tuple

from .project.devproject import DevProjectManager, discover_project_root


MESSAGE_TAGS = {
    "plan", "question", "answer", "handoff", "review",
    "decision", "status", "blocker",
}
RISKS = {"routine", "high", "release"}
STATES = {"working", "idle", "blocked", "done"}
ARTIFACT_STAGING_ROOT = ".reccli-org-artifacts"
CONTEXT_PACK_SCHEMA = "reccli.organization-context-packs.v1"
HOST_CANDIDATE = "RECCLI_HOST_CANDIDATE"
DEFAULT_CLOSEOUT_ROUNDS = 4

AGENT_REPLY_SCHEMA: Dict[str, Any] = {
    "type": "object",
    "properties": {
        "messages": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "to": {"type": "string", "minLength": 1},
                    "tag": {"type": "string", "enum": sorted(MESSAGE_TAGS)},
                    "content": {"type": "string", "minLength": 1},
                    "candidate": {"anyOf": [{"type": "string", "minLength": 1}, {"type": "null"}]},
                    "workItem": {"anyOf": [{"type": "string", "minLength": 1}, {"type": "null"}]},
                    "risk": {"anyOf": [{"type": "string", "enum": sorted(RISKS)}, {"type": "null"}]},
                },
                "required": ["to", "tag", "content", "candidate", "workItem", "risk"],
                "additionalProperties": False,
            },
        },
        "summary": {"type": "string", "minLength": 1},
        "state": {"type": "string", "enum": sorted(STATES)},
        "artifacts": {"type": "array", "items": {"type": "string"}},
        "candidate": {"anyOf": [{"type": "string", "minLength": 1}, {"type": "null"}]},
        "risk": {"anyOf": [{"type": "string", "enum": sorted(RISKS)}, {"type": "null"}]},
        "final": {"type": "boolean"},
    },
    "required": ["messages", "summary", "state", "artifacts", "candidate", "risk", "final"],
    "additionalProperties": False,
}

BLIND_REVIEW_SCHEMA: Dict[str, Any] = {
    "type": "object",
    "properties": {
        "candidate": {"type": "string", "minLength": 1},
        "verdict": {"type": "string", "enum": ["approved", "blocked"]},
        "summary": {"type": "string", "minLength": 1},
        "evidence": {"type": "array", "items": {"type": "string"}},
        "blockers": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["candidate", "verdict", "summary", "evidence", "blockers"],
    "additionalProperties": False,
}

DEFAULT_CLAUDE_ALLOWED_TOOLS = [
    "Bash(git status*)", "Bash(git diff*)", "Bash(git log*)",
    "Bash(git show*)", "Bash(git rev-parse*)", "Bash(git branch*)",
    "Bash(git ls-files*)", "Bash(git ls-tree*)", "Bash(git grep*)",
    "Bash(git -C * status*)", "Bash(git -C * diff*)",
    "Bash(git -C * log*)", "Bash(git -C * show*)",
    "Bash(git -C * rev-parse*)", "Bash(git -C * branch*)",
    "Bash(git -C * ls-files*)", "Bash(git -C * ls-tree*)",
    "Bash(git -C * grep*)",
    "Bash(npm test*)", "Bash(npm run *)", "Bash(pnpm test*)",
    "Bash(pnpm run *)", "Bash(yarn test*)", "Bash(yarn run *)",
    "Bash(bun test*)", "Bash(bun run *)", "Bash(cargo test*)",
    "Bash(go test*)", "Bash(pytest*)", "Bash(python -m pytest*)",
    "Bash(make test*)",
]


@dataclass(frozen=True)
class AgentSpec:
    agent_id: str
    role: str
    instructions: str
    writable: bool = True
    reasoning: str = "medium"
    write_scope: str = "workspace"

    def __post_init__(self) -> None:
        valid = {"none", "artifacts", "integration", "workspace"}
        if self.write_scope not in valid:
            raise ValueError(
                f"write_scope must be one of {sorted(valid)}, got {self.write_scope!r}"
            )
        if not self.writable and self.write_scope != "none":
            object.__setattr__(self, "write_scope", "none")


@dataclass
class Topology:
    topology_id: str
    name: str
    description: str
    culture: str
    agents: List[AgentSpec]
    routes: Dict[Tuple[str, str], Optional[Set[str]]]
    leader_id: str
    finalizer_id: str
    integrator_ids: Set[str]
    scheduler: str = "event"
    always_wake: Set[str] = field(default_factory=set)
    inbox_only_ids: Set[str] = field(default_factory=set)
    delegation_gate: bool = False
    required_approvers: Set[str] = field(default_factory=set)
    manager_ids: List[str] = field(default_factory=list)
    worker_ids: List[str] = field(default_factory=list)
    primary_manager_by_worker: Dict[str, str] = field(default_factory=dict)
    release_manager_id: Optional[str] = None
    final_reviewer_pool: List[str] = field(default_factory=list)
    blind_final_review: bool = False
    review_policy: str = "approval"
    human_promotion_required: bool = False

    def agent(self, agent_id: str) -> AgentSpec:
        for agent in self.agents:
            if agent.agent_id == agent_id:
                return agent
        raise KeyError(f"Unknown organization agent: {agent_id}")

    def can_route(self, sender: str, recipient: str, tag: str) -> Tuple[bool, str]:
        if sender == recipient:
            return False, "agents cannot message themselves"
        allowed = self.routes.get((sender, recipient), "missing")
        if allowed == "missing":
            return False, f"no communication edge from {sender} to {recipient}"
        if allowed is not None and tag not in allowed:
            return False, f"edge {sender} -> {recipient} does not allow tag {tag}"
        return True, ""

    def neighbors(self, agent_id: str) -> List[str]:
        return sorted({recipient for sender, recipient in self.routes if sender == agent_id})


@dataclass
class Workspace:
    cwd: Path
    branch: str
    integration_branch: str
    integration_workspace: Path
    additional_directories: List[Path]
    base_commit: Optional[str] = None
    runtime_paths: Set[str] = field(default_factory=set)


@dataclass(frozen=True)
class ProviderPlan:
    """Resolved native subscription providers for one organization run."""

    mode: str
    requested: str
    host_provider: str
    available_providers: List[str]
    provider_assignments: Dict[str, str]
    blind_verifier_provider: str
    authentication: Dict[str, str]

    def as_dict(self) -> Dict[str, Any]:
        return {
            "mode": self.mode,
            "requested": self.requested,
            "host_provider": self.host_provider,
            "available_providers": list(self.available_providers),
            "provider_assignments": dict(self.provider_assignments),
            "blind_verifier_provider": self.blind_verifier_provider,
            "authentication": dict(self.authentication),
        }


def _route(
    routes: Dict[Tuple[str, str], Optional[Set[str]]],
    left: str,
    right: str,
    tags: Optional[Set[str]] = None,
    bidirectional: bool = True,
) -> None:
    routes[(left, right)] = tags
    if bidirectional:
        routes[(right, left)] = tags


def get_topology(name: str = "google-rotating") -> Topology:
    normalized = (name or "google-rotating").strip().lower()
    supported = {"google-rotating", "google", "scientific"}
    if normalized not in supported:
        raise ValueError(f"topology must be one of {sorted(supported)}")

    manager_ids = [f"manager-{letter}" for letter in "abcd"]
    worker_ids = [f"worker-{letter}" for letter in "abcd"]
    routes: Dict[Tuple[str, str], Optional[Set[str]]] = {}
    for manager in manager_ids:
        _route(routes, "lead", manager)
    for manager in manager_ids:
        for worker in worker_ids:
            _route(routes, manager, worker)

    if normalized == "scientific":
        for left_index, left in enumerate(manager_ids):
            for right in manager_ids[left_index + 1:]:
                _route(routes, left, right)

        release = "manager-d"
        primary = {
            "worker-a": "manager-a", "worker-b": "manager-b",
            "worker-c": "manager-a", "worker-d": "manager-b",
        }
        agents = [
            AgentSpec(
                "lead", "scientific mission lead",
                "Own the scientific question and hard resource budget. Use the first turn for macro reconnaissance and send every manager a falsifiable plan or handoff with a named work item and risk; never task workers directly. A non-implementation lane still receives an explicit research, review, or standby assignment. Thereafter synthesize specialist research from managers and worker progress summarized through managers, intervening only on scope, priority, dependencies, or promotion readiness. Let the organization choose reversible experiments, keep canonical promotion outside the org, and approve only the exact sandbox candidate as a complete promotion proposal.",
                False, "high", "none",
            ),
            AgentSpec(
                "manager-a", "evidence and novelty manager",
                "Reconcile authority documents against primary receipts and the experiment ledger. On your first turn, refine the lead map into explicit plan or handoff assignments with named work items and risks for worker-a and worker-c. Surface the nearest prior attempts and contradictions as advice; do not pretend novelty or scientific merit is machine-decidable.",
                False, "high", "none",
            ),
            AgentSpec(
                "manager-b", "hypothesis and model manager",
                "Coordinate competing hypotheses, model choices, assumptions, and evidence needed to distinguish them. On your first turn, refine the lead map into explicit plan or handoff assignments with named work items and risks for worker-b and worker-d. Allocate the bounded experiment budget by expected information gain, not by ceremony.",
                False, "high", "none",
            ),
            AgentSpec(
                "manager-c", "topology and validation manager",
                "Act as the fully-sighted adversarial auditor. Read the complete durable evidence, candidate diff, generated bundle, prior attempts, and decision record. You may veto or annotate; you cannot integrate, promote, or turn an absence of veto into a scientific truth claim.",
                False, "high", "none",
            ),
            AgentSpec(
                "manager-d", "archive and release manager",
                "Integrate only exact sandbox patches after adversarial review completes without a veto. Never author the project implementation or mutate canonical authority. Assemble a promotion proposal that binds code, generated bundles, objections, nearest prior attempts, and proposed authority changes; finalization does not apply it to the caller's main branch or archive.",
                True, "high", "integration",
            ),
            AgentSpec(
                "worker-a", "reproduction experimenter",
                "Choose and run reversible baseline or receipt-reproduction experiments in this disposable worktree. Use temporary run-local identifiers, preserve primary outputs through the artifacts channel, and hand off host-materialized exact candidates without claiming canonical acceptance.",
                True, "medium", "workspace",
            ),
            AgentSpec(
                "worker-b", "hypothesis and model experimenter",
                "Choose and run reversible hypothesis or model-comparison experiments in your project-owned lane. Make assumptions and discriminators explicit, compare alternatives against the selected evidence, seal generated outputs, and do not mint canonical attempt IDs.",
                True, "high", "workspace",
            ),
            AgentSpec(
                "worker-c", "structural and integration validator",
                "Choose and run reversible structural, interface, integration, or measurement experiments in your project-owned lane. Try to falsify candidate claims using the project's declared invariants, end-to-end checks, quantitative evidence, and direct review; preserve outputs without declaring canonical acceptance.",
                True, "high", "workspace",
            ),
            AgentSpec(
                "worker-d", "uncertainty and alternative-explanation experimenter",
                "Choose and run reversible uncertainty, missing-information, or alternative-explanation experiments within the mission's existing authority. Use temporary run-local identifiers and seal evidence. Escalate any request to mutate immutable evidence, introduce new authoritative inputs, invent unsupported facts, or expand the acceptance standard.",
                True, "high", "workspace",
            ),
        ]
        return Topology(
            "scientific",
            "Scientific Reversible Exploration",
            "An autonomous scientific organization that may reason, modify disposable branches, and run bounded sandbox experiments while canonical promotion remains human-authorized.",
            "Cut authority at reversibility, not deliberation versus execution. Deterministic checks protect identity, hashes, paths, and budgets; agents and humans judge scientific meaning. Auditors are fully sighted, veto-only, and unable to promote.",
            agents, routes, "lead", release, {release},
            scheduler="event", always_wake=set(), inbox_only_ids={"lead"},
            delegation_gate=True, required_approvers={"lead"},
            manager_ids=manager_ids, worker_ids=worker_ids,
            primary_manager_by_worker=primary, release_manager_id=release,
            final_reviewer_pool=["manager-c"], blind_final_review=False,
            review_policy="veto", human_promotion_required=True,
        )

    if normalized == "google":
        agents = [
            AgentSpec(
                "lead", "leader and final integrator",
                "Set priorities, enforce design-doc consensus, integrate reviewed changes, validate the composed artifact, and finalize.",
                True, "high",
            ),
            *[
                AgentSpec(
                    manager, "middle integrator",
                    "Review design documents and code against the brief, request evidence for claims, and integrate only approved changes.",
                    True, "high",
                ) for manager in manager_ids
            ],
            *[
                AgentSpec(
                    worker, "implementation worker",
                    "Read the brief and task-relevant documentation, write a short design and acceptance-to-test mapping, implement a bounded slice, and hand off a host-materialized immutable candidate.",
                    True, "medium",
                ) for worker in worker_ids
            ],
        ]
        return Topology(
            "google", "Google Design-Doc Baseline",
            "Faithful two-layer bipartite organization with no worker peer or leader access.",
            "Design docs plus data-driven consensus. Claims require observable evidence.",
            agents, routes, "lead", "lead", {"lead", *manager_ids},
            scheduler="all",
        )

    for left_index, left in enumerate(manager_ids):
        for right in manager_ids[left_index + 1:]:
            _route(routes, left, right)

    release = "manager-d"
    primary = {worker: manager for worker, manager in zip(worker_ids, manager_ids)}
    agents = [
        AgentSpec(
            "lead", "mission leader",
            "Own scope, priorities, budget, and risk acceptance. Use the first turn to map the landscape and send every manager a plan or handoff with a named work item and risk, never delegating directly to workers. A lane that should not implement still receives an explicit standby, research, or review assignment. Thereafter synthesize manager research and manager-summarized worker progress, waking on new information or an operator steering message. Approve the exact final candidate for scope only; do not implement or merge.",
            False, "high",
        ),
        *[
            AgentSpec(
                manager,
                "engineering manager and release integrator" if manager == release else "engineering manager and integrator",
                (
                    f"Primary manager for worker-{manager[-1]}. On your first turn, refine the lead's assignment and send your worker a plan or handoff with a named work item and risk; use an explicit research, review, or standby task when implementation is not yet justified. Publish concise design decisions, answer routine questions, coordinate with peer managers, and inspect evidence. "
                    + ("Own the integration decision, let RecCli apply only independently approved candidates, run composed checks, and finalize after release gates pass."
                       if manager == release else
                       "Wait for alternate-manager approval before forwarding your worker's immutable candidate to manager-d.")
                ),
                True, "high",
            ) for manager in manager_ids
        ],
        *[
            AgentSpec(
                worker, "implementation worker",
                "Stay inside the bounded assignment. Read the brief, acceptance criteria, task-relevant repository docs, published design decisions, and interfaces. Make cohesive changes and hand the host-materialized immutable candidate to your primary manager.",
                True, "medium",
            ) for worker in worker_ids
        ],
    ]
    return Topology(
        "google-rotating", "Google with Rotating Cross-Manager Review",
        "Selective escalation, primary worker ownership, deterministic alternate-manager review, a release manager, and fresh final verification.",
        "Workers receive code plus task-relevant durable documentation. Managers coordinate routine dependencies. Raw management deliberation stays need-to-know.",
        agents, routes, "lead", release, set(manager_ids),
        scheduler="event", always_wake=set(), inbox_only_ids={"lead"},
        delegation_gate=True, required_approvers={"lead"},
        manager_ids=manager_ids, worker_ids=worker_ids,
        primary_manager_by_worker=primary, release_manager_id=release,
        final_reviewer_pool=manager_ids, blind_final_review=True,
    )


class Governance:
    def __init__(
        self,
        topology: Topology,
        run_id: str,
        provider_by_agent: Optional[Dict[str, str]] = None,
    ):
        self.topology = topology
        self.run_id = run_id
        self.provider_by_agent = dict(provider_by_agent or {})
        eligible = [
            manager for manager in topology.final_reviewer_pool
            if manager != topology.release_manager_id
        ]
        release_provider = self.provider_by_agent.get(
            topology.release_manager_id or "",
        )
        cross_provider = [
            manager for manager in eligible
            if release_provider
            and self.provider_by_agent.get(manager) != release_provider
        ]
        reviewer_pool = cross_provider or eligible
        self.release_reviewer_id = (
            reviewer_pool[_stable_index(run_id, len(reviewer_pool))]
            if reviewer_pool else None
        )
        self.assignments: Dict[str, Dict[str, Any]] = {}
        self.candidate_approvals: Dict[str, str] = {}
        self.candidate_vetoes: Dict[str, str] = {}
        self.review_cursor = _stable_index(run_id, 2**31 - 1)

    def required_final_approvers(self) -> Set[str]:
        result = set(self.topology.required_approvers)
        if self.release_reviewer_id:
            result.add(self.release_reviewer_id)
        return result

    def process_message(
        self, sender: str, message: Dict[str, Any], round_number: int,
    ) -> Tuple[bool, str, Optional[Dict[str, Any]]]:
        if not self.topology.manager_ids or message.get("tag") != "handoff":
            return True, "", None

        if sender in self.topology.worker_ids:
            primary = self.topology.primary_manager_by_worker.get(sender)
            if message.get("to") != primary:
                return False, f"worker handoff must go to primary manager {primary}", None
            if not message.get("candidate") or not message.get("workItem") or not message.get("risk"):
                return False, "worker handoff requires candidate, workItem, and risk metadata", None
            candidate = message["candidate"]
            if candidate in self.assignments:
                return True, "", None
            if self.topology.review_policy == "veto":
                preferred = [
                    manager for manager in self.topology.final_reviewer_pool
                    if manager not in {primary, self.topology.release_manager_id}
                ]
            else:
                preferred = [
                    manager for manager in self.topology.manager_ids
                    if manager not in {primary, self.topology.release_manager_id}
                ]
            eligible = preferred or [
                manager for manager in self.topology.manager_ids if manager != primary
            ]
            worker_provider = self.provider_by_agent.get(sender)
            cross_provider = [
                manager for manager in eligible
                if worker_provider
                and self.provider_by_agent.get(manager) != worker_provider
            ]
            eligible = cross_provider or eligible
            reviewer = eligible[self.review_cursor % len(eligible)]
            self.review_cursor += 1
            assignment = {
                "candidate": candidate,
                "workItem": message["workItem"],
                "risk": message["risk"],
                "workerId": sender,
                "primaryManagerId": primary,
                "reviewerId": reviewer,
                "workerProvider": worker_provider,
                "reviewerProvider": self.provider_by_agent.get(reviewer),
                "crossProvider": bool(
                    worker_provider
                    and self.provider_by_agent.get(reviewer) != worker_provider
                ),
                "status": "assigned",
            }
            self.assignments[candidate] = assignment
            system_message = {
                "runId": self.run_id,
                "round": round_number,
                "from": "orchestrator",
                "to": reviewer,
                "tag": "review",
                "content": (
                    (
                        f"Adversarial review assignment for {assignment['workItem']}. Inspect exact candidate {candidate}, the full relevant decision record, primary evidence, prior attempts, and any sealed generated-output bundle. "
                        f"You cannot approve scientific truth or integrate. Send NO_VETO with annotations, or BLOCKED with falsifying evidence, for this exact candidate to {primary}."
                    )
                    if self.topology.review_policy == "veto" else
                    (
                        f"Independent review assignment for {assignment['workItem']}. Inspect candidate {candidate} without relying on the author's claims. "
                        f"Review the diff, contracts, focused tests, and integration risk. Send APPROVED or BLOCKED for this exact candidate to {primary}."
                    )
                ),
                "candidate": candidate,
                "workItem": assignment["workItem"],
                "risk": assignment["risk"],
                "deliveredAt": _utc_now(),
            }
            return True, "", system_message

        candidate = message.get("candidate")
        assignment = self.assignments.get(candidate) if candidate else None
        if (
            assignment
            and sender == assignment["primaryManagerId"]
            and message.get("to") == self.topology.release_manager_id
            and assignment["status"] not in {
                "reviewed" if self.topology.review_policy == "veto" else "approved"
            }
        ):
            requirement = "completed no-veto review" if self.topology.review_policy == "veto" else "approval"
            return False, f"candidate {candidate} lacks {requirement} from assigned reviewer {assignment['reviewerId']}", None
        return True, "", None

    def record_decision(self, sender: str, message: Dict[str, Any]) -> None:
        if message.get("tag") != "decision" or not message.get("candidate"):
            return
        content = str(message.get("content", "")).lstrip().upper()
        candidate = message["candidate"]
        assignment = self.assignments.get(candidate)
        if assignment and sender == assignment["reviewerId"]:
            if self.topology.review_policy == "veto" and content.startswith(("NO_VETO", "REVIEWED", "APPROVED")):
                assignment["status"] = "reviewed"
                assignment["decision"] = message.get("content")
            elif self.topology.review_policy != "veto" and content.startswith("APPROVED"):
                assignment["status"] = "approved"
                assignment["decision"] = message.get("content")
            elif content.startswith(("BLOCKED", "VETO")):
                assignment["status"] = "vetoed" if self.topology.review_policy == "veto" else "blocked"
                assignment["decision"] = message.get("content")
        if message.get("to") == self.topology.finalizer_id and sender in self.required_final_approvers():
            if content.startswith(("BLOCKED", "VETO")):
                self.candidate_vetoes[sender] = candidate
                self.candidate_approvals.pop(sender, None)
            elif (
                self.topology.review_policy == "veto"
                and sender == self.release_reviewer_id
                and content.startswith(("NO_VETO", "REVIEWED", "APPROVED"))
            ) or (
                sender != self.release_reviewer_id and content.startswith("APPROVED")
            ) or (
                self.topology.review_policy != "veto" and content.startswith("APPROVED")
            ):
                self.candidate_approvals[sender] = candidate
                self.candidate_vetoes.pop(sender, None)

    def missing_final_approvers(self, candidate: str) -> List[str]:
        return sorted(
            approver for approver in self.required_final_approvers()
            if self.candidate_approvals.get(approver) != candidate
        )

    def render(self, agent_id: str) -> str:
        if not self.topology.manager_ids:
            return "No rotating review policy is configured."
        lines = [
            f"Release manager: {self.topology.release_manager_id}.",
            f"Rotating final reviewer: {self.release_reviewer_id}.",
            f"Required final reviewers: {', '.join(sorted(self.required_final_approvers())) or 'none'}.",
            f"Review policy: {self.topology.review_policy}.",
        ]
        if self.provider_by_agent:
            lines.append(
                f"Your provider: {self.provider_by_agent.get(agent_id, 'unknown')}. "
                "Cross-provider reviews are preferred to reduce correlated blind spots."
            )
        primary = self.topology.primary_manager_by_worker.get(agent_id)
        if primary:
            lines.append(f"Your primary manager: {primary}.")
        relevant = [
            assignment for assignment in self.assignments.values()
            if agent_id in {
                assignment["workerId"], assignment["primaryManagerId"],
                assignment["reviewerId"], self.topology.release_manager_id,
            }
        ]
        for assignment in relevant:
            lines.append(
                f"- {assignment['workItem']}: {assignment['candidate']} ({assignment['risk']}); "
                f"primary={assignment['primaryManagerId']}; reviewer={assignment['reviewerId']}; status={assignment['status']}"
            )
        return "\n".join(lines)

    def snapshot(self) -> Dict[str, Any]:
        return {
            "releaseReviewerId": self.release_reviewer_id,
            "releaseReviewerProvider": self.provider_by_agent.get(
                self.release_reviewer_id or "",
            ),
            "requiredFinalApprovers": sorted(self.required_final_approvers()),
            "assignments": list(self.assignments.values()),
            "approvals": dict(self.candidate_approvals),
            "vetoes": dict(self.candidate_vetoes),
            "reviewPolicy": self.topology.review_policy,
            "humanPromotionRequired": self.topology.human_promotion_required,
        }


class SubscriptionSession:
    """One persistent native Claude Code or Codex CLI session."""

    def __init__(
        self,
        provider: str,
        workspace: Workspace,
        writable: bool,
        session_key: str,
        run_dir: Path,
        model: Optional[str] = None,
        reasoning: str = "medium",
        fresh: bool = False,
    ):
        self.provider = provider
        self.workspace = workspace
        self.writable = writable
        self.session_key = session_key
        self.run_dir = run_dir
        self.model = model
        self.reasoning = reasoning
        self.fresh = fresh
        self.session_id: Optional[str] = None
        self.turn = 0

    def run(self, prompt: str, schema: Dict[str, Any], timeout_seconds: int) -> Dict[str, Any]:
        self.turn += 1
        if self.provider == "claude":
            return self._run_claude(prompt, schema, timeout_seconds)
        if self.provider == "codex":
            return self._run_codex(prompt, schema, timeout_seconds)
        raise ValueError(f"Unsupported subscription provider: {self.provider}")

    def _process_environment(self) -> Dict[str, str]:
        """Bind subprocesses to the isolated worktree's source and runtime.

        A repository-local virtual environment is normally ignored by Git and
        therefore absent from command-line worktrees.  RecCli creates a small
        worktree-local launcher when the canonical project has `.venv`; this
        environment makes both native agent CLIs and their child processes use
        that launcher while resolving imports from the candidate worktree.
        """
        env = os.environ.copy()
        source_roots = [
            self.workspace.cwd / "src",
            self.workspace.cwd,
        ]
        python_path = [str(path) for path in source_roots if path.exists()]
        existing_python_path = env.get("PYTHONPATH")
        if existing_python_path:
            python_path.append(existing_python_path)
        if python_path:
            env["PYTHONPATH"] = os.pathsep.join(python_path)
        bridge_bin = self.workspace.cwd / ".venv" / "bin"
        if ".venv" in self.workspace.runtime_paths and bridge_bin.is_dir():
            env["PATH"] = os.pathsep.join([
                str(bridge_bin),
                env.get("PATH", ""),
            ])
            env["VIRTUAL_ENV"] = str(self.workspace.cwd / ".venv")
        env["RECCLI_ORGANIZATION_WORKTREE"] = str(self.workspace.cwd)
        return env

    def _run_claude(self, prompt: str, schema: Dict[str, Any], timeout_seconds: int) -> Dict[str, Any]:
        if shutil.which("claude") is None:
            raise RuntimeError("claude CLI not found on PATH")
        requested_id = self.session_id or str(uuid.uuid4())
        args = [
            "claude", "-p", "--output-format", "json", "--json-schema", json.dumps(schema),
            "--permission-mode", "acceptEdits" if self.writable else "plan",
        ]
        if self.session_id:
            args += ["--resume", self.session_id]
        else:
            args += ["--session-id", requested_id]
        if self.model:
            args += ["--model", self.model]
        args += ["--effort", "low" if self.reasoning == "minimal" else self.reasoning]
        for directory in self.workspace.additional_directories:
            args += ["--add-dir", str(directory)]
        if self.writable:
            args += ["--allowedTools", *DEFAULT_CLAUDE_ALLOWED_TOOLS]
        else:
            args += ["--disallowedTools", "Edit", "Write", "NotebookEdit"]
        if self.fresh:
            args += ["--setting-sources", "project", "--no-session-persistence", "--disable-slash-commands"]
        proc = subprocess.run(
            args, input=prompt, text=True, cwd=self.workspace.cwd,
            env=self._process_environment(), capture_output=True,
            timeout=timeout_seconds, check=False,
        )
        self._persist_process_output(proc)
        if proc.returncode != 0:
            raise RuntimeError(f"Claude Code exited {proc.returncode}: {(proc.stderr or '').strip()}")
        try:
            envelope = json.loads((proc.stdout or "").strip())
        except json.JSONDecodeError as exc:
            raise RuntimeError("Claude Code returned invalid JSON") from exc
        if envelope.get("is_error"):
            raise RuntimeError(f"Claude Code error: {envelope.get('result', 'unknown error')}")
        self.session_id = envelope.get("session_id") or requested_id
        value = envelope.get("structured_output")
        if value is None:
            try:
                value = json.loads(envelope.get("result", ""))
            except (json.JSONDecodeError, TypeError) as exc:
                raise RuntimeError("Claude Code returned no structured output") from exc
        usage = envelope.get("usage") or {}
        return {
            "value": value,
            "session_id": self.session_id,
            "usage": {
                "input_tokens": int(usage.get("input_tokens", 0) or 0)
                + int(usage.get("cache_creation_input_tokens", 0) or 0)
                + int(usage.get("cache_read_input_tokens", 0) or 0),
                "cached_input_tokens": int(usage.get("cache_read_input_tokens", 0) or 0),
                "output_tokens": int(usage.get("output_tokens", 0) or 0),
            },
        }

    def _run_codex(self, prompt: str, schema: Dict[str, Any], timeout_seconds: int) -> Dict[str, Any]:
        if shutil.which("codex") is None:
            raise RuntimeError("codex CLI not found on PATH")
        schema_path = self.run_dir / f"{_safe_name(self.session_key)}_schema.json"
        output_path = self.run_dir / f"{_safe_name(self.session_key)}_turn_{self.turn:03d}_output.json"
        schema_path.write_text(json.dumps(schema, indent=2) + "\n", encoding="utf-8")
        if self.session_id:
            args = [
                "codex", "exec", "resume", "--json", "--output-schema", str(schema_path),
                "--output-last-message", str(output_path),
            ]
            if self.model:
                args += ["--model", self.model]
            args += ["-c", f'model_reasoning_effort="{self.reasoning}"', self.session_id, "-"]
        else:
            args = [
                "codex", "exec", "--cd", str(self.workspace.cwd),
                "--sandbox", "workspace-write" if self.writable else "read-only",
                "--json", "--output-schema", str(schema_path),
                "--output-last-message", str(output_path),
            ]
            if self.fresh:
                args += ["--ephemeral"]
            if self.model:
                args += ["--model", self.model]
            args += ["-c", f'model_reasoning_effort="{self.reasoning}"']
            for directory in self.workspace.additional_directories:
                args += ["--add-dir", str(directory)]
            args += ["-"]
        proc = subprocess.run(
            args, input=prompt, text=True, cwd=self.workspace.cwd,
            env=self._process_environment(), capture_output=True,
            timeout=timeout_seconds, check=False,
        )
        self._persist_process_output(proc)
        events = []
        for line in (proc.stdout or "").splitlines():
            try:
                events.append(json.loads(line))
            except json.JSONDecodeError:
                continue
        for event in events:
            if event.get("type") == "thread.started":
                self.session_id = event.get("thread_id") or event.get("threadId") or self.session_id
        if proc.returncode != 0:
            raise RuntimeError(f"Codex exited {proc.returncode}: {(proc.stderr or '').strip()}")
        if not output_path.exists():
            raise RuntimeError("Codex returned no final structured output")
        try:
            value = json.loads(output_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise RuntimeError("Codex final response was not valid JSON") from exc
        usage = {"input_tokens": 0, "cached_input_tokens": 0, "output_tokens": 0}
        for event in events:
            event_usage = event.get("usage") or {}
            if event_usage:
                usage = {
                    "input_tokens": int(event_usage.get("input_tokens", usage["input_tokens"]) or 0),
                    "cached_input_tokens": int(event_usage.get("cached_input_tokens", usage["cached_input_tokens"]) or 0),
                    "output_tokens": int(event_usage.get("output_tokens", usage["output_tokens"]) or 0),
                }
        return {"value": value, "session_id": self.session_id, "usage": usage}

    def _persist_process_output(self, proc: subprocess.CompletedProcess) -> None:
        stem = self.run_dir / f"{_safe_name(self.session_key)}_turn_{self.turn:03d}"
        stem.with_name(stem.name + "_stdout.txt").write_text(proc.stdout or "", encoding="utf-8")
        stem.with_name(stem.name + "_stderr.txt").write_text(proc.stderr or "", encoding="utf-8")


def validate_agent_reply(value: Any) -> Dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError("agent reply must be an object")
    required = {"messages", "summary", "state", "artifacts", "candidate", "risk", "final"}
    if set(value) != required:
        raise ValueError(f"agent reply fields must be exactly {sorted(required)}")
    if not isinstance(value["messages"], list) or not isinstance(value["artifacts"], list):
        raise ValueError("messages and artifacts must be arrays")
    if any(not isinstance(path, str) or not path.strip() for path in value["artifacts"]):
        raise ValueError("artifacts must contain non-empty paths")
    if value["state"] not in STATES or not isinstance(value["final"], bool):
        raise ValueError("invalid state or final flag")
    if not isinstance(value["summary"], str) or not value["summary"].strip():
        raise ValueError("summary is required")
    if value["risk"] is not None and value["risk"] not in RISKS:
        raise ValueError("invalid top-level risk")
    for message in value["messages"]:
        if not isinstance(message, dict):
            raise ValueError("message must be an object")
        fields = {"to", "tag", "content", "candidate", "workItem", "risk"}
        if set(message) != fields or message.get("tag") not in MESSAGE_TAGS:
            raise ValueError("invalid message fields or tag")
        if message.get("risk") is not None and message["risk"] not in RISKS:
            raise ValueError("invalid message risk")
    return value


def _path_is_within(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
        return True
    except ValueError:
        return False


def resolve_evidence_paths(
    project_root: Path, evidence_paths: Optional[List[str]],
) -> List[Path]:
    """Resolve explicit evidence inputs without broadening them implicitly."""
    project_root = project_root.resolve()
    organization_runs = organization_root(project_root).resolve()
    git_path = project_root / ".git"
    resolved: List[Path] = []
    seen: Set[Path] = set()
    for raw in evidence_paths or []:
        if not isinstance(raw, str) or not raw.strip():
            raise ValueError("evidence_paths entries must be non-empty paths")
        candidate = Path(raw).expanduser()
        if not candidate.is_absolute():
            candidate = project_root / candidate
        if candidate.is_symlink():
            raise ValueError(f"evidence path may not be a symlink: {candidate}")
        candidate = candidate.resolve()
        if not candidate.exists():
            raise FileNotFoundError(f"evidence path does not exist: {candidate}")
        if candidate.is_symlink():
            raise ValueError(f"evidence path may not be a symlink: {candidate}")
        if candidate == project_root:
            raise ValueError("select evidence files or subdirectories, not the project root")
        if (
            _path_is_within(organization_runs, candidate)
            or _path_is_within(candidate, organization_runs)
        ):
            raise ValueError(
                f"evidence path may not contain organization run output: {candidate}"
            )
        if candidate == git_path or _path_is_within(candidate, git_path):
            raise ValueError(f"Git administrative files are not evidence inputs: {candidate}")
        if candidate not in seen:
            seen.add(candidate)
            resolved.append(candidate)
    return resolved


def resolve_protected_paths(
    project_root: Path, protected_paths: Optional[List[str]],
) -> List[str]:
    """Resolve tracked paths that organization worktrees must never change."""
    project_root = project_root.resolve()
    resolved: List[str] = []
    seen: Set[str] = set()
    for raw in protected_paths or []:
        if not isinstance(raw, str) or not raw.strip():
            raise ValueError("protected_paths entries must be non-empty paths")
        supplied = Path(raw).expanduser()
        if supplied.is_absolute():
            raise ValueError("protected_paths must be relative to the project root")
        candidate = (project_root / supplied).resolve()
        if candidate == project_root or not _path_is_within(candidate, project_root):
            raise ValueError(f"protected path escapes or selects the project root: {raw}")
        if not candidate.exists():
            raise FileNotFoundError(f"protected path does not exist: {candidate}")
        relative = candidate.relative_to(project_root).as_posix().rstrip("/")
        tracked = _git(project_root, ["ls-files", "--", relative]).strip()
        if not tracked:
            raise ValueError(
                f"protected path is not tracked; pass ignored/external inputs via evidence_paths: {relative}"
            )
        if relative not in seen:
            seen.add(relative)
            resolved.append(relative)
    return resolved


def resolve_context_manifest(
    project_root: Path, context_manifest: Optional[str],
) -> Optional[Path]:
    """Resolve one tracked, project-local organization context manifest."""
    if context_manifest is None:
        return None
    if not isinstance(context_manifest, str) or not context_manifest.strip():
        raise ValueError("context_manifest must be a non-empty project-relative path")
    supplied = Path(context_manifest).expanduser()
    if supplied.is_absolute():
        raise ValueError("context_manifest must be relative to the project root")
    project_root = project_root.resolve()
    candidate = (project_root / supplied).resolve()
    if candidate == project_root or not _path_is_within(candidate, project_root):
        raise ValueError("context_manifest escapes or selects the project root")
    if candidate.is_symlink() or not candidate.is_file():
        raise FileNotFoundError(
            f"context_manifest must name a regular tracked file: {candidate}"
        )
    relative = candidate.relative_to(project_root).as_posix()
    tracked = _git(project_root, ["ls-files", "--error-unmatch", "--", relative]).strip()
    if tracked != relative:
        raise ValueError(f"context_manifest is not tracked: {relative}")
    return candidate


def _context_files_for_path(project_root: Path, raw: str) -> List[str]:
    project_root = project_root.resolve()
    if not isinstance(raw, str) or not raw.strip():
        raise ValueError("context pack paths must be non-empty project-relative paths")
    supplied = Path(raw).expanduser()
    if supplied.is_absolute():
        raise ValueError(f"context pack paths must be project-relative: {raw}")
    candidate = (project_root / supplied).resolve()
    if candidate == project_root or not _path_is_within(candidate, project_root):
        raise ValueError(f"context pack path escapes or selects the project root: {raw}")
    if candidate.is_symlink() or not candidate.exists():
        raise FileNotFoundError(f"context pack path is missing or unsafe: {candidate}")
    relative = candidate.relative_to(project_root).as_posix().rstrip("/")
    tracked = [
        line for line in _git(
            project_root, ["ls-files", "--", relative],
        ).splitlines() if line
    ]
    if not tracked:
        raise ValueError(f"context pack path has no tracked files: {relative}")
    result: List[str] = []
    for tracked_relative in tracked:
        source = project_root / tracked_relative
        if source.is_symlink() or not source.is_file():
            raise ValueError(
                f"context packs support tracked regular files only: {tracked_relative}"
            )
        result.append(tracked_relative)
    return result


def _load_context_definition(
    project_root: Path, manifest_path: Path, topology: Topology,
) -> Dict[str, Any]:
    try:
        definition = json.loads(manifest_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"context manifest is not valid JSON: {manifest_path}") from exc
    if not isinstance(definition, dict):
        raise ValueError("context manifest must be a JSON object")
    if definition.get("schema") != CONTEXT_PACK_SCHEMA:
        raise ValueError(
            f"context manifest schema must be {CONTEXT_PACK_SCHEMA!r}"
        )
    common = definition.get("common")
    agents = definition.get("agents")
    full_context_agents = definition.get("full_context_agents", [])
    if not isinstance(common, dict) or not isinstance(common.get("paths"), list):
        raise ValueError("context manifest common.paths must be an array")
    if not isinstance(agents, dict):
        raise ValueError("context manifest agents must be an object")
    if not isinstance(full_context_agents, list) or any(
        not isinstance(agent_id, str) for agent_id in full_context_agents
    ):
        raise ValueError("context manifest full_context_agents must be an array of IDs")
    known_agents = {agent.agent_id for agent in topology.agents}
    unknown = (set(agents) | set(full_context_agents)) - known_agents
    if unknown:
        raise ValueError(
            f"context manifest names agents outside the topology: {sorted(unknown)}"
        )

    def normalize_pack(name: str, value: Any) -> Dict[str, Any]:
        if not isinstance(value, dict) or not isinstance(value.get("paths"), list):
            raise ValueError(f"context manifest {name}.paths must be an array")
        library_paths = value.get("library_paths", [])
        if not isinstance(library_paths, list):
            raise ValueError(
                f"context manifest {name}.library_paths must be an array"
            )
        purpose = value.get("purpose", "")
        if purpose and not isinstance(purpose, str):
            raise ValueError(f"context manifest {name}.purpose must be a string")

        def expand_paths(
            field: str, raw_paths: List[Any],
        ) -> Tuple[List[str], List[str]]:
            declared: List[str] = []
            files: List[str] = []
            seen_files: Set[str] = set()
            for raw in raw_paths:
                if not isinstance(raw, str):
                    raise ValueError(
                        f"context manifest {name}.{field} must contain strings"
                    )
                declared.append(raw)
                for relative in _context_files_for_path(project_root, raw):
                    if relative not in seen_files:
                        seen_files.add(relative)
                        files.append(relative)
            return declared, files

        declared, files = expand_paths("paths", value["paths"])
        declared_library, library_files = expand_paths(
            "library_paths", library_paths,
        )
        return {
            "purpose": purpose.strip(),
            "declared_paths": declared,
            "files": files,
            "declared_library_paths": declared_library,
            "library_files": library_files,
        }

    normalized_agents = {
        agent_id: normalize_pack(f"agents.{agent_id}", value)
        for agent_id, value in agents.items()
    }
    return {
        "schema": CONTEXT_PACK_SCHEMA,
        "description": str(definition.get("description", "")).strip(),
        "common": normalize_pack("common", common),
        "agents": normalized_agents,
        "full_context_agents": list(dict.fromkeys(full_context_agents)),
    }


def prepare_context_packs(
    project_root: Path,
    run_dir: Path,
    context_manifest: Optional[str],
    topology: Topology,
) -> Optional[Dict[str, Any]]:
    """Materialize hash-bound, read-only educational context per agent."""
    project_root = project_root.resolve()
    manifest_path = resolve_context_manifest(project_root, context_manifest)
    if manifest_path is None:
        return None
    definition = _load_context_definition(project_root, manifest_path, topology)
    context_root = run_dir / "context-packs"
    context_root.mkdir(parents=True, exist_ok=False)
    union_files: List[str] = []
    seen_union: Set[str] = set()
    for pack in [definition["common"], *definition["agents"].values()]:
        for relative in [*pack["files"], *pack["library_files"]]:
            if relative not in seen_union:
                seen_union.add(relative)
                union_files.append(relative)

    source_relatives = [
        manifest_path.relative_to(project_root).as_posix(), *union_files,
    ]
    source_files: List[Dict[str, Any]] = []
    for relative in dict.fromkeys(source_relatives):
        source = project_root / relative
        info = source.stat()
        source_files.append({
            "path": relative,
            "bytes": info.st_size,
            "mtime_ns": info.st_mtime_ns,
            "mode": stat.S_IMODE(info.st_mode),
            "sha256": _sha256_file(source),
        })

    agent_packs: Dict[str, Dict[str, Any]] = {}
    full_agents = set(definition["full_context_agents"])
    for agent in topology.agents:
        lane = definition["agents"].get(agent.agent_id)
        full_union = agent.agent_id in full_agents
        selected_files = [
            *definition["common"]["files"],
            *definition["common"]["library_files"],
        ]
        declared_paths = list(definition["common"]["declared_paths"])
        declared_library_paths = list(
            definition["common"]["declared_library_paths"]
        )
        purposes = [
            (
                "Common project context: "
                f"{definition['common']['purpose']}"
            )
            if definition["common"]["purpose"] else ""
        ]
        if full_union:
            for lane_agent_id, lane_pack in definition["agents"].items():
                selected_files.extend([
                    *lane_pack["files"], *lane_pack["library_files"],
                ])
                declared_library_paths.extend([
                    *lane_pack["declared_paths"],
                    *lane_pack["declared_library_paths"],
                ])
                purposes.append(
                    (
                        f"Project lane for {lane_agent_id}: "
                        f"{lane_pack['purpose']}"
                    )
                    if lane_pack["purpose"] else ""
                )
        elif lane:
            selected_files.extend([*lane["files"], *lane["library_files"]])
            declared_paths.extend(lane["declared_paths"])
            declared_library_paths.extend(lane["declared_library_paths"])
            purposes.append(
                (
                    f"Your project-owned lane ({agent.agent_id}): "
                    f"{lane['purpose']}"
                )
                if lane["purpose"] else ""
            )
        selected_files = list(dict.fromkeys(selected_files))
        declared_paths = list(dict.fromkeys(declared_paths))
        declared_library_paths = list(dict.fromkeys(declared_library_paths))

        pack_root = context_root / _safe_name(agent.agent_id)
        canonical_root = pack_root / "canonical"
        canonical_root.mkdir(parents=True, exist_ok=False)
        for relative in selected_files:
            source = project_root / relative
            destination = canonical_root / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, destination)
        pack_description = "\n\n".join(
            purpose for purpose in purposes if purpose
        )
        pack_document = {
            "schema": "reccli.organization-agent-context.v1",
            "agent_id": agent.agent_id,
            "role": agent.role,
            "scope": "full-union" if full_union else (
                "common+lane" if lane else "common"
            ),
            "description": pack_description,
            "source_manifest": str(manifest_path),
            "source_manifest_sha256": _sha256_file(manifest_path),
            "canonical_access_remains_available": True,
            "declared_reading_paths": declared_paths,
            "declared_library_paths": declared_library_paths,
            "materialized_files": selected_files,
        }
        pack_index = pack_root / "CONTEXT-PACK.json"
        pack_index.write_text(
            json.dumps(pack_document, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        _make_tree_read_only(pack_root)
        files, directories = _snapshot_inventory(pack_root)
        agent_packs[agent.agent_id] = {
            "root": str(pack_root),
            "index": str(pack_index),
            "scope": pack_document["scope"],
            "description": pack_description,
            "declared_reading_paths": declared_paths,
            "declared_library_paths": declared_library_paths,
            "materialized_files": selected_files,
            "files": files,
            "directories": directories,
        }
    _make_tree_read_only(context_root)

    manifest: Dict[str, Any] = {
        "version": 1,
        "created_at": _utc_now(),
        "project_root": str(project_root.resolve()),
        "source_manifest": str(manifest_path),
        "source_manifest_sha256": _sha256_file(manifest_path),
        "context_root": str(context_root),
        "read_only": True,
        "source_files": source_files,
        "agent_packs": agent_packs,
    }
    canonical = json.dumps(
        manifest, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
    ).encode("utf-8")
    manifest["manifest_sha256"] = hashlib.sha256(canonical).hexdigest()
    (run_dir / "context-pack-manifest.json").write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    return manifest


def verify_context_packs(manifest: Dict[str, Any], full: bool = False) -> None:
    """Detect mutation, deletion, or injection in any materialized context box."""
    for pack in manifest.get("agent_packs", {}).values():
        root = Path(pack["root"])
        expected_files = {item["path"]: item for item in pack["files"]}
        expected_directories = {item["path"]: item for item in pack["directories"]}
        files, directories = _snapshot_inventory(root)
        actual_files = {item["path"]: item for item in files}
        actual_directories = {item["path"]: item for item in directories}
        if set(actual_files) != set(expected_files) or set(actual_directories) != set(expected_directories):
            raise RuntimeError(f"context pack path inventory changed: {root}")
        for relative, expected in expected_directories.items():
            actual = actual_directories[relative]
            if (
                actual["mtime_ns"] != expected["mtime_ns"]
                or actual["mode"] != expected["mode"]
            ):
                raise RuntimeError(f"context pack directory metadata changed: {root / relative}")
        for relative, expected in expected_files.items():
            actual = actual_files[relative]
            if (
                actual["bytes"] != expected["bytes"]
                or actual["mtime_ns"] != expected["mtime_ns"]
                or actual["mode"] != expected["mode"]
            ):
                raise RuntimeError(f"context pack file metadata changed: {root / relative}")
            if full and actual["sha256"] != expected["sha256"]:
                raise RuntimeError(f"context pack content changed: {root / relative}")


def verify_context_sources_unchanged(
    project_root: Path, manifest: Dict[str, Any], full: bool = False,
) -> None:
    """Ensure canonical documentation selected for context did not change."""
    for expected in manifest.get("source_files", []):
        path = project_root / expected["path"]
        if path.is_symlink() or not path.is_file():
            raise RuntimeError(f"context source was removed or replaced: {path}")
        info = path.stat()
        if (
            info.st_size != expected["bytes"]
            or info.st_mtime_ns != expected["mtime_ns"]
            or stat.S_IMODE(info.st_mode) != expected["mode"]
        ):
            raise RuntimeError(f"context source metadata changed: {path}")
        if full and _sha256_file(path) != expected["sha256"]:
            raise RuntimeError(f"context source content changed: {path}")


def _protect_worktree_paths(worktree: Path, protected_paths: List[str]) -> None:
    for relative in protected_paths:
        candidate = worktree / relative
        if candidate.exists():
            _make_tracked_tree_read_only(candidate)


def _make_tracked_tree_read_only(path: Path) -> None:
    """Remove write bits without following tracked repository symlinks.

    Protected paths are Git-controlled worktree content, not evidence
    snapshots. A tracked compatibility symlink is therefore valid and must
    retain its link identity. The containing protected directories become
    non-writable, while post-turn Git scope validation remains authoritative
    for detecting any attempted protected-path mutation.
    """
    if path.is_symlink():
        return
    paths = [path]
    if path.is_dir():
        for root, directories, files in os.walk(path, followlinks=False):
            root_path = Path(root)
            paths.extend(root_path / name for name in [*directories, *files])
    for candidate in sorted(paths, key=lambda item: len(item.parts), reverse=True):
        if candidate.is_symlink():
            continue
        mode = stat.S_IMODE(candidate.stat().st_mode)
        candidate.chmod(mode & ~0o222)


def _assert_snapshot_source_has_no_symlinks(source: Path) -> None:
    if source.is_symlink():
        raise ValueError(f"evidence snapshots do not accept symlinks: {source}")
    if not source.is_dir():
        return
    for root, directories, files in os.walk(source, followlinks=False):
        root_path = Path(root)
        for name in [*directories, *files]:
            candidate = root_path / name
            if candidate.is_symlink():
                raise ValueError(
                    f"evidence snapshots do not accept symlinks: {candidate}"
                )


def _clone_or_copy(source: Path, destination: Path) -> None:
    """Prefer an APFS clone, with a portable byte-copy fallback."""
    cloned = False
    if hasattr(os, "uname") and os.uname().sysname == "Darwin":
        args = ["cp", "-cR", str(source), str(destination)] if source.is_dir() else [
            "cp", "-c", str(source), str(destination),
        ]
        proc = subprocess.run(args, capture_output=True, text=True, check=False)
        cloned = proc.returncode == 0
        if not cloned:
            if destination.is_dir():
                shutil.rmtree(destination, ignore_errors=True)
            else:
                destination.unlink(missing_ok=True)
    if cloned:
        return
    if source.is_dir():
        shutil.copytree(source, destination, symlinks=False)
    else:
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination)


def _make_tree_read_only(path: Path) -> None:
    paths = [path]
    if path.is_dir():
        paths.extend(path.rglob("*"))
    for candidate in sorted(paths, key=lambda item: len(item.parts), reverse=True):
        if candidate.is_symlink():
            raise ValueError(f"evidence snapshot unexpectedly contains a symlink: {candidate}")
        mode = stat.S_IMODE(candidate.stat().st_mode)
        candidate.chmod(mode & ~0o222)


def _remove_tree_even_if_read_only(path: Path) -> None:
    if not path.exists():
        return
    paths = [path, *path.rglob("*")] if path.is_dir() else [path]
    for candidate in paths:
        try:
            candidate.chmod(stat.S_IMODE(candidate.stat().st_mode) | 0o700)
        except OSError:
            pass
    if path.is_dir():
        shutil.rmtree(path, ignore_errors=True)
    else:
        path.unlink(missing_ok=True)


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _snapshot_inventory(snapshot: Path) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    files: List[Dict[str, Any]] = []
    directories: List[Dict[str, Any]] = []
    if snapshot.is_file():
        info = snapshot.stat()
        files.append({
            "path": ".", "bytes": info.st_size, "mtime_ns": info.st_mtime_ns,
            "mode": stat.S_IMODE(info.st_mode), "sha256": _sha256_file(snapshot),
        })
        return files, directories
    candidates = [snapshot, *snapshot.rglob("*")]
    for candidate in sorted(candidates, key=lambda item: item.as_posix()):
        if candidate.is_symlink():
            raise ValueError(f"evidence snapshot unexpectedly contains a symlink: {candidate}")
        relative = "." if candidate == snapshot else candidate.relative_to(snapshot).as_posix()
        info = candidate.stat()
        if candidate.is_dir():
            directories.append({
                "path": relative, "mtime_ns": info.st_mtime_ns,
                "mode": stat.S_IMODE(info.st_mode),
            })
        elif candidate.is_file():
            files.append({
                "path": relative, "bytes": info.st_size,
                "mtime_ns": info.st_mtime_ns, "mode": stat.S_IMODE(info.st_mode),
                "sha256": _sha256_file(candidate),
            })
        else:
            raise ValueError(f"unsupported evidence object: {candidate}")
    return files, directories


def prepare_evidence_snapshot(
    project_root: Path, run_dir: Path, evidence_paths: Optional[List[str]],
) -> Optional[Dict[str, Any]]:
    """Clone selected ignored/external evidence into a sealed run-owned view."""
    sources = resolve_evidence_paths(project_root, evidence_paths)
    if not sources:
        return None
    snapshot_root = run_dir / "evidence-snapshot"
    snapshot_root.mkdir(parents=True, exist_ok=False)
    entries: List[Dict[str, Any]] = []
    total_bytes = 0
    total_files = 0
    for index, source in enumerate(sources, 1):
        _assert_snapshot_source_has_no_symlinks(source)
        destination = snapshot_root / f"{index:03d}_{_safe_name(source.name)}"
        _clone_or_copy(source, destination)
        _make_tree_read_only(destination)
        files, directories = _snapshot_inventory(destination)
        entry_bytes = sum(item["bytes"] for item in files)
        total_bytes += entry_bytes
        total_files += len(files)
        entries.append({
            "source": str(source), "snapshot": str(destination),
            "kind": "directory" if destination.is_dir() else "file",
            "file_count": len(files), "bytes": entry_bytes,
            "files": files, "directories": directories,
        })
    _make_tree_read_only(snapshot_root)
    manifest: Dict[str, Any] = {
        "version": 1, "created_at": _utc_now(),
        "project_root": str(project_root.resolve()),
        "snapshot_root": str(snapshot_root), "read_only": True,
        "file_count": total_files, "bytes": total_bytes, "sources": entries,
    }
    canonical = json.dumps(
        manifest, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
    ).encode("utf-8")
    manifest["manifest_sha256"] = hashlib.sha256(canonical).hexdigest()
    manifest_path = run_dir / "evidence-manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    return manifest


def verify_evidence_snapshot(manifest: Dict[str, Any], full: bool = False) -> None:
    """Detect mutation, deletion, or injection into a sealed evidence view."""
    for source in manifest.get("sources", []):
        snapshot = Path(source["snapshot"])
        expected_files = {item["path"]: item for item in source["files"]}
        expected_directories = {item["path"]: item for item in source["directories"]}
        actual_files: Dict[str, Path] = {}
        actual_directories: Dict[str, Path] = {}
        candidates = [snapshot] if snapshot.is_file() else [snapshot, *snapshot.rglob("*")]
        for candidate in candidates:
            if candidate.is_symlink():
                raise RuntimeError(f"evidence snapshot was mutated with a symlink: {candidate}")
            relative = "." if candidate == snapshot else candidate.relative_to(snapshot).as_posix()
            if candidate.is_file():
                actual_files[relative] = candidate
            elif candidate.is_dir():
                actual_directories[relative] = candidate
            else:
                raise RuntimeError(f"evidence snapshot contains an unsupported object: {candidate}")
        if set(actual_files) != set(expected_files) or set(actual_directories) != set(expected_directories):
            raise RuntimeError(f"evidence snapshot path inventory changed: {snapshot}")
        for relative, expected in expected_directories.items():
            info = actual_directories[relative].stat()
            if (
                info.st_mtime_ns != expected["mtime_ns"]
                or stat.S_IMODE(info.st_mode) != expected["mode"]
            ):
                raise RuntimeError(f"evidence snapshot directory metadata changed: {actual_directories[relative]}")
        for relative, expected in expected_files.items():
            path = actual_files[relative]
            info = path.stat()
            if (
                info.st_size != expected["bytes"]
                or info.st_mtime_ns != expected["mtime_ns"]
                or stat.S_IMODE(info.st_mode) != expected["mode"]
            ):
                raise RuntimeError(f"evidence snapshot file metadata changed: {path}")
            if full and _sha256_file(path) != expected["sha256"]:
                raise RuntimeError(f"evidence snapshot content changed: {path}")


def verify_evidence_sources_unchanged(
    manifest: Dict[str, Any], full: bool = False,
) -> None:
    """Ensure the original selected evidence did not change during the run."""
    for entry in manifest.get("sources", []):
        source = Path(entry["source"])
        expected_files = {item["path"]: item for item in entry["files"]}
        actual_files: Dict[str, Path] = {}
        candidates = [source] if source.is_file() else [
            candidate for candidate in source.rglob("*") if candidate.is_file()
        ]
        for candidate in candidates:
            if candidate.is_symlink():
                raise RuntimeError(f"original evidence gained a symlink: {candidate}")
            relative = "." if candidate == source else candidate.relative_to(source).as_posix()
            actual_files[relative] = candidate
        if set(actual_files) != set(expected_files):
            raise RuntimeError(f"original evidence path inventory changed: {source}")
        for relative, expected in expected_files.items():
            path = actual_files[relative]
            info = path.stat()
            if info.st_size != expected["bytes"] or info.st_mtime_ns != expected["mtime_ns"]:
                raise RuntimeError(f"original evidence metadata changed: {path}")
            if full and _sha256_file(path) != expected["sha256"]:
                raise RuntimeError(f"original evidence content changed: {path}")


def _prepare_python_runtime_bridge(
    project_root: Path,
    worktree: Path,
) -> Set[str]:
    """Expose the canonical project interpreter without sharing its source.

    Copying or symlinking an entire virtual environment into a worktree is
    unsafe: editable installs can silently import the canonical checkout and a
    writable symlink can mutate shared dependencies.  These launchers execute
    the canonical interpreter read-only while forcing candidate source ahead
    of any editable-install path.
    """
    canonical_python = project_root / ".venv" / "bin" / "python"
    if not canonical_python.exists() or not os.access(canonical_python, os.X_OK):
        return set()
    bridge_bin = worktree / ".venv" / "bin"
    if bridge_bin.exists():
        return set()
    bridge_bin.mkdir(parents=True)
    candidate_paths = [worktree / "src", worktree]
    python_path = os.pathsep.join(str(path) for path in candidate_paths)
    launcher = (
        "#!/bin/sh\n"
        f"export PYTHONPATH={shlex.quote(python_path)}"
        '${PYTHONPATH:+:"$PYTHONPATH"}\n'
        f"exec {shlex.quote(str(canonical_python.resolve()))} \"$@\"\n"
    )
    for name in ("python", "python3"):
        path = bridge_bin / name
        path.write_text(launcher, encoding="utf-8")
        path.chmod(0o755)
    return {".venv"}


def prepare_workspaces(
    project_root: Path,
    topology: Topology,
    run_id: str,
    additional_directories: Optional[List[Path]] = None,
    protected_paths: Optional[List[str]] = None,
) -> Dict[str, Workspace]:
    project_root = project_root.resolve()
    _validate_clean_repository(project_root)
    safe_run = _safe_name(run_id)
    prefix = f"reccli-org/{_safe_name(topology.topology_id)}/{safe_run}"
    integration_branch = f"{prefix}/main"
    _git(project_root, ["branch", integration_branch, "HEAD"])
    worktree_root = Path(tempfile.gettempdir()) / "reccli-org-worktrees" / _safe_name(project_root.name) / safe_run
    worktree_root.mkdir(parents=True, exist_ok=True)
    shared_directories = [
        path.resolve() for path in additional_directories or []
    ]
    base_commit = _git(project_root, ["rev-parse", integration_branch]).strip()
    integration_workspace = worktree_root / _safe_name(topology.finalizer_id)
    result: Dict[str, Workspace] = {}
    ordered = [topology.agent(topology.finalizer_id)] + [
        agent for agent in topology.agents if agent.agent_id != topology.finalizer_id
    ]
    for agent in ordered:
        is_finalizer = agent.agent_id == topology.finalizer_id
        branch = integration_branch if is_finalizer else f"{prefix}/{_safe_name(agent.agent_id)}"
        cwd = worktree_root / _safe_name(agent.agent_id)
        args = ["worktree", "add", str(cwd), integration_branch] if is_finalizer else [
            "worktree", "add", "-b", branch, str(cwd), integration_branch,
        ]
        _git(project_root, args)
        _protect_worktree_paths(cwd, list(protected_paths or []))
        runtime_paths = _prepare_python_runtime_bridge(project_root, cwd)
        result[agent.agent_id] = Workspace(
            cwd=cwd, branch=branch, integration_branch=integration_branch,
            integration_workspace=integration_workspace,
            additional_directories=list(shared_directories),
            base_commit=base_commit,
            runtime_paths=runtime_paths,
        )
    return result


class OrganizationRunner:
    def __init__(
        self,
        project_root: Path,
        mission: str,
        provider: str,
        topology_name: str,
        run_id: str,
        run_dir: Path,
        max_rounds: int = 8,
        max_concurrency: int = 5,
        turn_timeout_seconds: int = 1200,
        model: Optional[str] = None,
        provider_assignments: Optional[Dict[str, str]] = None,
        host_provider: Optional[str] = None,
        blind_verifier_provider: Optional[str] = None,
        evidence_paths: Optional[List[str]] = None,
        protected_paths: Optional[List[str]] = None,
        context_manifest: Optional[str] = None,
        max_experiments: int = 3,
        max_closeout_rounds: int = DEFAULT_CLOSEOUT_ROUNDS,
    ):
        self.project_root = project_root.resolve()
        self.mission = mission.strip()
        self.provider = provider
        self.topology = get_topology(topology_name)
        if provider_assignments is None:
            if provider not in {"claude", "codex"}:
                raise ValueError("mixed organization runners require provider assignments")
            provider_assignments = {
                agent.agent_id: provider for agent in self.topology.agents
            }
        expected_agents = {agent.agent_id for agent in self.topology.agents}
        if set(provider_assignments) != expected_agents:
            raise ValueError("provider assignments must cover every organization agent exactly")
        if any(value not in {"claude", "codex"} for value in provider_assignments.values()):
            raise ValueError("provider assignments may contain only claude or codex")
        self.provider_by_agent = dict(provider_assignments)
        self.host_provider = host_provider or self.provider_by_agent[self.topology.finalizer_id]
        self.blind_verifier_provider = (
            blind_verifier_provider or self.host_provider
        )
        self.evidence_paths = list(evidence_paths or [])
        self.protected_paths = list(protected_paths or [])
        self.context_manifest = context_manifest
        self.evidence_manifest: Optional[Dict[str, Any]] = None
        self.evidence_verified_at: Optional[str] = None
        self.context_pack_manifest: Optional[Dict[str, Any]] = None
        self.context_verified_at: Optional[str] = None
        self.run_id = run_id
        self.run_dir = run_dir
        self.candidate_artifact_root = self.run_dir / "candidate-artifacts"
        self.candidate_artifact_manifests: List[Dict[str, Any]] = []
        self.artifact_staging_prefix = (
            f"{ARTIFACT_STAGING_ROOT}/{_safe_name(run_id)}"
        )
        self.max_rounds = max(1, int(max_rounds))
        self.max_closeout_rounds = max(0, int(max_closeout_rounds))
        self.max_experiments = max(0, int(max_experiments))
        self.max_concurrency = max(1, int(max_concurrency))
        self.turn_timeout_seconds = max(30, int(turn_timeout_seconds))
        self.model = model
        self.governance = Governance(
            self.topology, run_id, self.provider_by_agent,
        )
        self.inboxes: Dict[str, List[Dict[str, Any]]] = {
            agent.agent_id: [] for agent in self.topology.agents
        }
        self.states = {agent.agent_id: "idle" for agent in self.topology.agents}
        self.sessions: Dict[str, SubscriptionSession] = {}
        self.turned: Set[str] = set()
        self.usage = {"input_tokens": 0, "cached_input_tokens": 0, "output_tokens": 0}
        self.usage_by_provider = {
            provider_name: {
                "input_tokens": 0, "cached_input_tokens": 0, "output_tokens": 0,
            }
            for provider_name in sorted(set(self.provider_by_agent.values()) | {self.blind_verifier_provider})
        }
        self.delivered_messages = 0
        self.dropped_messages = 0
        self.failed_turns = 0
        self.completed_turns = 0
        self.attempted_turns = 0
        self._trace_lock = threading.Lock()
        self.workspaces: Dict[str, Workspace] = {}
        self.project_context = build_project_context(self.project_root)
        self.caller_head: Optional[str] = None
        self.control_protocol = "reccli.organization-control.v1"
        self.paused = False
        self.integrated_candidates: Dict[str, str] = {}

    def run(self) -> Dict[str, Any]:
        if not self.mission:
            raise ValueError("mission must not be empty")
        self.run_dir.mkdir(parents=True, exist_ok=True)
        _validate_clean_repository(self.project_root)
        self.caller_head = _git(self.project_root, ["rev-parse", "HEAD"]).strip()
        self.candidate_artifact_root.mkdir(parents=True, exist_ok=False)
        self.candidate_artifact_root.chmod(0o555)
        self.evidence_manifest = prepare_evidence_snapshot(
            self.project_root, self.run_dir, self.evidence_paths,
        )
        self.context_pack_manifest = prepare_context_packs(
            self.project_root, self.run_dir, self.context_manifest, self.topology,
        )
        evidence_directories: List[Path] = [self.candidate_artifact_root]
        if self.evidence_manifest:
            verify_evidence_snapshot(self.evidence_manifest, full=True)
            self.evidence_verified_at = _utc_now()
            evidence_directories.append(Path(self.evidence_manifest["snapshot_root"]))
        if self.context_pack_manifest:
            verify_context_packs(self.context_pack_manifest, full=True)
            verify_context_sources_unchanged(
                self.project_root, self.context_pack_manifest, full=True,
            )
            self.context_verified_at = _utc_now()
        self.workspaces = prepare_workspaces(
            self.project_root, self.topology, self.run_id,
            additional_directories=evidence_directories,
            protected_paths=self.protected_paths,
        )
        if self.context_pack_manifest:
            for agent_id, pack in self.context_pack_manifest["agent_packs"].items():
                self.workspaces[agent_id].additional_directories.append(
                    Path(pack["root"])
                )
        self._write_json("run.json", {
            "run_id": self.run_id, "created_at": _utc_now(),
            "project_root": str(self.project_root), "provider": self.provider,
            "host_provider": self.host_provider,
            "provider_assignments": self.provider_by_agent,
            "blind_verifier_provider": self.blind_verifier_provider,
            "topology": self.topology.topology_id, "mission": self.mission,
            "scheduler": self.topology.scheduler,
            "delegation_gate": self.topology.delegation_gate,
            "coordination_cadence": (
                "round-1-lead-recon; round-2-manager-delegation; "
                "round-3+-event-driven-parallel-work"
                if self.topology.delegation_gate
                else self.topology.scheduler
            ),
            "max_rounds": self.max_rounds,
            "max_closeout_rounds": self.max_closeout_rounds,
            "max_concurrency": self.max_concurrency,
            "max_experiments": self.max_experiments,
            "protected_paths": self.protected_paths,
            "turn_timeout_seconds": self.turn_timeout_seconds,
            "integration_branch": self.workspaces[self.topology.finalizer_id].integration_branch,
            "integration_workspace": str(self.workspaces[self.topology.finalizer_id].integration_workspace),
            "artifact_staging_prefix": self.artifact_staging_prefix,
            "artifact_export_directory": str(self.run_dir / "deliverables"),
            "evidence_manifest": str(self.run_dir / "evidence-manifest.json") if self.evidence_manifest else None,
            "evidence_snapshot_root": self.evidence_manifest.get("snapshot_root") if self.evidence_manifest else None,
            "evidence_verified_at": self.evidence_verified_at,
            "context_manifest": self.context_manifest,
            "context_pack_manifest": str(self.run_dir / "context-pack-manifest.json") if self.context_pack_manifest else None,
            "context_verified_at": self.context_verified_at,
            "candidate_artifact_root": str(self.candidate_artifact_root),
            "human_promotion_required": self.topology.human_promotion_required,
            "canonical_effects_applied": False,
            "control_protocol": self.control_protocol,
            "git_ownership": "reccli-host",
            "runtime_binding": {
                agent_id: {
                    "runtime_paths": sorted(workspace.runtime_paths),
                    "candidate_python_path": [
                        str(workspace.cwd / "src"),
                        str(workspace.cwd),
                    ],
                }
                for agent_id, workspace in self.workspaces.items()
            },
            "governance": self.governance.snapshot(),
        })
        self._status("running", round_number=0, detail="Organization workspaces prepared")
        finalized_by: Optional[str] = None
        final_summary: Optional[str] = None
        final_review: Optional[Dict[str, Any]] = None
        verified_candidate: Optional[str] = None
        promotion_candidate: Optional[str] = None
        promotion_branch: Optional[str] = None
        artifact_manifest: Optional[Dict[str, Any]] = None
        promotion_request: Optional[Dict[str, Any]] = None
        status = "round_limit"
        rounds = 0

        total_round_limit = self.max_rounds + self.max_closeout_rounds
        for round_number in range(1, total_round_limit + 1):
            closeout = round_number > self.max_rounds
            if (self.run_dir / "cancel.requested").exists():
                status = "cancelled"
                break
            if self._apply_control_requests(round_number - 1) == "cancelled":
                status = "cancelled"
                break
            if self._wait_while_paused(round_number - 1):
                status = "cancelled"
                break
            scheduled = (
                self._select_closeout_agents()
                if closeout else self._select_agents(round_number)
            )
            if not scheduled:
                if not closeout:
                    status = "stalled"
                break
            rounds = round_number
            phase_detail = (
                f"Closeout {round_number - self.max_rounds}/"
                f"{self.max_closeout_rounds} after {self.max_rounds} working rounds"
                if closeout else
                f"Round {round_number}/{self.max_rounds}"
            )
            self._status(
                "running", round_number=round_number,
                detail=(
                    f"{phase_detail}: running "
                    f"{len(scheduled)} agent turns; "
                    f"{self.completed_turns} completed previously"
                ),
                scheduled_turns=len(scheduled),
                phase="closeout" if closeout else None,
            )
            completed: List[Dict[str, Any]] = []
            with ThreadPoolExecutor(max_workers=min(self.max_concurrency, len(scheduled))) as executor:
                futures = {executor.submit(self._run_turn, agent, round_number): agent for agent in scheduled}
                for future in as_completed(futures):
                    agent = futures[future]
                    try:
                        completed.append(future.result())
                    except Exception as exc:
                        completed.append({"agent": agent, "error": str(exc), "duration_ms": 0})
            if self.evidence_manifest:
                verify_evidence_snapshot(self.evidence_manifest, full=False)
                verify_evidence_sources_unchanged(self.evidence_manifest, full=False)
                self.evidence_verified_at = _utc_now()
            if self.context_pack_manifest:
                verify_context_packs(self.context_pack_manifest, full=False)
                verify_context_sources_unchanged(
                    self.project_root, self.context_pack_manifest, full=False,
                )
                self.context_verified_at = _utc_now()
            self._verify_caller_repository_unchanged()
            self._verify_candidate_artifacts(full=False)
            for item in completed:
                if item.get("error") or not item.get("reply", {}).get("artifacts"):
                    continue
                try:
                    item["candidate_artifact_bundle"] = self._seal_reported_artifacts(
                        item["agent"], item["reply"], round_number,
                    )
                except Exception as exc:
                    item["error"] = f"generated artifact sealing failed: {exc}"
            completed.sort(key=lambda item: next(
                index for index, agent in enumerate(self.topology.agents)
                if agent.agent_id == item["agent"].agent_id
            ))
            self.attempted_turns += len(completed)
            self.completed_turns += sum(1 for item in completed if not item.get("error"))
            final_attempts: List[Dict[str, Any]] = []
            for item in completed:
                agent = item["agent"]
                self.turned.add(agent.agent_id)
                if item.get("error"):
                    self.failed_turns += 1
                    self.states[agent.agent_id] = "working"
                    self._append_jsonl(f"turns/{_safe_name(agent.agent_id)}.jsonl", {
                        "round": round_number, "agent_id": agent.agent_id,
                        "provider": self.provider_by_agent[agent.agent_id],
                        "status": "failed", "error": item["error"],
                        "duration_ms": item.get("duration_ms", 0),
                    })
                    continue
                reply = item["reply"]
                self.states[agent.agent_id] = reply["state"]
                self._add_usage(item.get("usage", {}), item.get("provider"))
                self._append_jsonl(f"turns/{_safe_name(agent.agent_id)}.jsonl", {
                    "round": round_number, "agent_id": agent.agent_id,
                    "provider": item.get("provider"),
                    "status": "completed", "duration_ms": item["duration_ms"],
                    "session_id": item.get("session_id"), "usage": item.get("usage", {}),
                    "reply": reply,
                })
                for message in reply["messages"]:
                    self._deliver_message(agent.agent_id, message, round_number)
                bundle = item.get("candidate_artifact_bundle")
                if bundle:
                    recipient = self.topology.primary_manager_by_worker.get(
                        agent.agent_id,
                    ) or self.topology.finalizer_id
                    if recipient != agent.agent_id:
                        self._system_message(
                            recipient, "review",
                            "RecCli sealed generated/ignored outputs for exact candidate "
                            f"{bundle['candidate']} at {bundle['bundle_root']}. Verify "
                            f"{bundle['manifest_path']} (manifest SHA-256 "
                            f"{bundle['manifest_sha256']}) with the code candidate.",
                            round_number, bundle["candidate"],
                            bundle["work_item"], bundle["risk"],
                        )
                if reply["final"]:
                    final_attempts.append(item)

            self._assert_delegation_barrier(round_number)

            for item in final_attempts:
                agent = item["agent"]
                reply = item["reply"]
                candidate = reply.get("candidate")
                if agent.agent_id != self.topology.finalizer_id:
                    self._event("finalization.rejected", round_number, agent_id=agent.agent_id, reason="agent is not the finalizer")
                    continue
                if not candidate or reply.get("risk") != "release":
                    self.states[agent.agent_id] = "working"
                    self._event("finalization.rejected", round_number, agent_id=agent.agent_id, reason="release candidate and release risk are required")
                    continue
                head = _git(self.workspaces[agent.agent_id].cwd, ["rev-parse", "HEAD"]).strip()
                if candidate != head:
                    self.states[agent.agent_id] = "working"
                    self._event("finalization.rejected", round_number, agent_id=agent.agent_id, candidate=candidate, integration_head=head, reason="candidate is not exact integration HEAD")
                    continue
                uncommitted_paths = self._uncommitted_paths(
                    self.workspaces[agent.agent_id],
                )
                if uncommitted_paths:
                    self.states[agent.agent_id] = "working"
                    self._event(
                        "finalization.rejected", round_number,
                        agent_id=agent.agent_id, candidate=candidate,
                        reason="integration worktree is not clean",
                    )
                    self._system_message(
                        agent.agent_id, "blocker",
                        "Finalization requires a clean integration worktree. Resolve or remove unrelated untracked files; RecCli commits allowed staged artifacts automatically before finalization.",
                        round_number, candidate, "final-release", "release",
                    )
                    continue
                missing = self.governance.missing_final_approvers(candidate)
                if missing:
                    self.states[agent.agent_id] = "working"
                    self._event("finalization.rejected", round_number, agent_id=agent.agent_id, candidate=candidate, missing_approvers=missing, reason="candidate lacks required approvals")
                    continue
                if self.topology.blind_final_review:
                    if self.evidence_manifest:
                        verify_evidence_snapshot(self.evidence_manifest, full=True)
                        self.evidence_verified_at = _utc_now()
                    if self.context_pack_manifest:
                        verify_context_packs(self.context_pack_manifest, full=True)
                        verify_context_sources_unchanged(
                            self.project_root, self.context_pack_manifest, full=True,
                        )
                        self.context_verified_at = _utc_now()
                    self._verify_candidate_artifacts(full=True)
                    try:
                        final_review = self._blind_review(candidate, round_number)
                    except Exception as exc:
                        self.failed_turns += 1
                        self.states[agent.agent_id] = "working"
                        self._system_message(agent.agent_id, "blocker", f"Independent verification failed to run: {exc}", round_number, candidate, "final-release", "release")
                        continue
                    if final_review["verdict"] != "approved":
                        self.states[agent.agent_id] = "working"
                        blockers = "; ".join(final_review.get("blockers", [])) or "unspecified"
                        self._system_message(agent.agent_id, "blocker", f"{final_review['summary']} Blockers: {blockers}", round_number, candidate, "final-release", "release")
                        continue
                if self.evidence_manifest:
                    verify_evidence_snapshot(self.evidence_manifest, full=True)
                    verify_evidence_sources_unchanged(self.evidence_manifest, full=True)
                    self.evidence_verified_at = _utc_now()
                if self.context_pack_manifest:
                    verify_context_packs(self.context_pack_manifest, full=True)
                    verify_context_sources_unchanged(
                        self.project_root, self.context_pack_manifest, full=True,
                    )
                    self.context_verified_at = _utc_now()
                self._verify_candidate_artifacts(full=True)
                try:
                    artifact_manifest = self._export_staged_artifacts(candidate)
                    promotion_candidate, promotion_branch = self._create_promotion_candidate(
                        candidate,
                        [entry["source"] for entry in artifact_manifest["files"]],
                        artifact_manifest["manifest_sha256"],
                    )
                    if self.topology.human_promotion_required:
                        promotion_request = self._write_promotion_request(
                            candidate, promotion_candidate, promotion_branch,
                            artifact_manifest,
                        )
                except Exception as exc:
                    self.failed_turns += 1
                    self.states[agent.agent_id] = "working"
                    self._event(
                        "finalization.rejected", round_number,
                        agent_id=agent.agent_id, candidate=candidate,
                        reason=f"durable artifact export failed: {exc}",
                    )
                    self._system_message(
                        agent.agent_id, "blocker",
                        f"Durable artifact export failed: {exc}",
                        round_number, candidate, "final-release", "release",
                    )
                    continue
                status = "completed"
                verified_candidate = candidate
                finalized_by = agent.agent_id
                final_summary = reply["summary"]
                break
            if status == "completed":
                break

        self._reject_pending_control_requests(status, rounds)
        result = {
            "run_id": self.run_id, "status": status, "rounds": rounds,
            "working_rounds": min(rounds, self.max_rounds),
            "closeout_rounds": max(0, rounds - self.max_rounds),
            "max_closeout_rounds": self.max_closeout_rounds,
            "provider": self.provider, "topology": self.topology.topology_id,
            "host_provider": self.host_provider,
            "provider_assignments": self.provider_by_agent,
            "blind_verifier_provider": self.blind_verifier_provider,
            "finalized_by": finalized_by, "final_summary": final_summary,
            "verified_candidate": verified_candidate,
            "promotion_candidate": promotion_candidate,
            "promotion_branch": promotion_branch,
            "promotion_request": str(self.run_dir / "promotion-request.json") if promotion_request else None,
            "human_promotion_required": self.topology.human_promotion_required,
            "canonical_effects_applied": False,
            "artifact_manifest": artifact_manifest,
            "final_reviewer_id": self.governance.release_reviewer_id,
            "blind_review": final_review, "usage": self.usage,
            "usage_by_provider": self.usage_by_provider,
            "delivered_messages": self.delivered_messages,
            "dropped_messages": self.dropped_messages,
            "failed_turns": self.failed_turns,
            "attempted_turns": self.attempted_turns,
            "completed_turns": self.completed_turns,
            "governance": self.governance.snapshot(),
            "run_dir": str(self.run_dir),
            "integration_branch": self.workspaces[self.topology.finalizer_id].integration_branch,
            "integration_workspace": str(self.workspaces[self.topology.finalizer_id].integration_workspace,
            ),
            "artifact_staging_prefix": self.artifact_staging_prefix,
            "artifact_export_directory": str(self.run_dir / "deliverables"),
            "evidence_manifest": str(self.run_dir / "evidence-manifest.json") if self.evidence_manifest else None,
            "evidence_snapshot_root": self.evidence_manifest.get("snapshot_root") if self.evidence_manifest else None,
            "evidence_verified_at": self.evidence_verified_at,
            "context_manifest": self.context_manifest,
            "context_pack_manifest": str(self.run_dir / "context-pack-manifest.json") if self.context_pack_manifest else None,
            "context_verified_at": self.context_verified_at,
            "candidate_artifact_root": str(self.candidate_artifact_root),
            "candidate_artifact_manifests": [
                item["manifest_path"] for item in self.candidate_artifact_manifests
            ],
            "experiment_budget": {
                "maximum": self.max_experiments,
                "used": len(self.candidate_artifact_manifests),
                "remaining": max(0, self.max_experiments - len(self.candidate_artifact_manifests)),
            },
            "protected_paths": self.protected_paths,
            "control_protocol": self.control_protocol,
            "git_ownership": "reccli-host",
            "integrated_candidates": dict(self.integrated_candidates),
        }
        self._write_json("result.json", result)
        self._status(
            status,
            round_number=rounds,
            detail=final_summary or status,
            result=result,
            phase="closeout" if rounds > self.max_rounds else None,
        )
        return result

    def _staged_artifact_blobs(self, candidate: str) -> List[Dict[str, Any]]:
        """Read run-scoped artifacts from an exact Git candidate, not its worktree."""
        workspace = self.workspaces[self.topology.finalizer_id]
        proc = subprocess.run(
            [
                "git", "ls-tree", "-r", "-z", candidate, "--",
                self.artifact_staging_prefix,
            ],
            cwd=workspace.cwd, capture_output=True, check=False,
        )
        if proc.returncode != 0:
            raise RuntimeError(
                f"git ls-tree failed: {proc.stderr.decode('utf-8', errors='replace').strip()}"
            )
        prefix = PurePosixPath(self.artifact_staging_prefix)
        result: List[Dict[str, Any]] = []
        for raw_record in proc.stdout.split(b"\0"):
            if not raw_record:
                continue
            try:
                raw_header, raw_path = raw_record.split(b"\t", 1)
                mode, object_type, object_id = raw_header.decode("ascii").split()
                source = raw_path.decode("utf-8")
            except (ValueError, UnicodeDecodeError) as exc:
                raise RuntimeError("artifact staging contains an unsupported Git path") from exc
            if object_type != "blob" or mode not in {"100644", "100755"}:
                raise RuntimeError(
                    f"artifact staging supports regular files only: {source} ({mode} {object_type})"
                )
            relative = PurePosixPath(source).relative_to(prefix)
            if not relative.parts or any(part in {"", ".", ".."} for part in relative.parts):
                raise RuntimeError(f"unsafe staged artifact path: {source}")
            blob = subprocess.run(
                ["git", "show", f"{candidate}:{source}"],
                cwd=workspace.cwd, capture_output=True, check=False,
            )
            if blob.returncode != 0:
                raise RuntimeError(
                    f"git show failed for {source}: "
                    f"{blob.stderr.decode('utf-8', errors='replace').strip()}"
                )
            result.append({
                "source": source,
                "relative": relative.as_posix(),
                "mode": mode,
                "git_blob": object_id,
                "content": blob.stdout,
            })
        return result

    def _export_staged_artifacts(self, candidate: str) -> Dict[str, Any]:
        """Materialize verified artifacts into the durable ignored run directory."""
        blobs = self._staged_artifact_blobs(candidate)
        export_root = self.run_dir / "deliverables"
        export_root.mkdir(parents=True, exist_ok=True)
        files: List[Dict[str, Any]] = []
        for blob in blobs:
            destination = export_root.joinpath(*PurePosixPath(blob["relative"]).parts)
            destination.parent.mkdir(parents=True, exist_ok=True)
            temporary = destination.with_name(
                f".{destination.name}.{os.getpid()}.{uuid.uuid4().hex}.tmp"
            )
            temporary.write_bytes(blob["content"])
            temporary.replace(destination)
            if blob["mode"] == "100755":
                destination.chmod(destination.stat().st_mode | 0o111)
            files.append({
                "path": str(destination.relative_to(self.run_dir)),
                "source": blob["source"],
                "sha256": hashlib.sha256(blob["content"]).hexdigest(),
                "git_blob": blob["git_blob"],
                "bytes": len(blob["content"]),
                "mode": blob["mode"],
            })
        payload: Dict[str, Any] = {
            "version": 1,
            "run_id": self.run_id,
            "verified_candidate": candidate,
            "staging_prefix": self.artifact_staging_prefix,
            "export_directory": str(export_root),
            "files": files,
        }
        canonical = json.dumps(
            payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
        ).encode("utf-8")
        payload["manifest_sha256"] = hashlib.sha256(canonical).hexdigest()
        self._write_json("artifact-manifest.json", payload)
        return payload

    def _seal_reported_artifacts(
        self, agent: AgentSpec, reply: Dict[str, Any], round_number: int,
    ) -> Dict[str, Any]:
        """Seal explicitly reported ignored/generated outputs outside Git.

        The Git candidate remains the source-change identity. This bundle binds
        its generated evidence to that exact commit without retaining large
        experiment outputs in Git object storage.
        """
        if len(self.candidate_artifact_manifests) >= self.max_experiments:
            raise RuntimeError(
                f"hard experiment budget exhausted ({self.max_experiments} sealed bundles)"
            )
        handoffs = [
            message for message in reply["messages"]
            if message.get("tag") == "handoff" and message.get("candidate")
        ]
        identities = {
            (message["candidate"], message.get("workItem"), message.get("risk"))
            for message in handoffs
        }
        if len(identities) != 1:
            raise RuntimeError(
                "reported generated artifacts require exactly one candidate handoff"
            )
        candidate, work_item, risk = next(iter(identities))
        workspace = self.workspaces[agent.agent_id]
        head = _git(workspace.cwd, ["rev-parse", "HEAD"]).strip()
        if candidate != head:
            raise RuntimeError(
                f"generated artifacts must bind to current HEAD {head}, got {candidate}"
            )
        sources: List[Tuple[str, Path]] = []
        seen: Set[Path] = set()
        for raw in reply["artifacts"]:
            if not isinstance(raw, str) or not raw.strip():
                raise RuntimeError("artifact paths must be non-empty strings")
            supplied = Path(raw).expanduser()
            source = supplied if supplied.is_absolute() else workspace.cwd / supplied
            if source.is_symlink():
                raise RuntimeError(f"generated artifact may not be a symlink: {source}")
            source = source.resolve()
            if not _path_is_within(source, workspace.cwd.resolve()):
                raise RuntimeError(
                    f"generated artifact must be inside the agent worktree: {source}"
                )
            if not source.exists():
                raise RuntimeError(f"reported generated artifact does not exist: {source}")
            relative = source.relative_to(workspace.cwd.resolve()).as_posix()
            if self._artifact_path(relative):
                raise RuntimeError(
                    f"{relative} already uses Git-backed artifact staging; do not bundle it twice"
                )
            tracked = _git(workspace.cwd, ["ls-files", "--", relative]).strip()
            if tracked:
                raise RuntimeError(
                    f"tracked path {relative} belongs in the Git candidate, not a generated-output bundle"
                )
            _assert_snapshot_source_has_no_symlinks(source)
            if source not in seen:
                seen.add(source)
                sources.append((relative, source))
        if not sources:
            raise RuntimeError("no generated artifact paths remained after validation")

        bundle_id = (
            f"{_safe_name(agent.agent_id)}-r{round_number:02d}-{_safe_name(candidate[:12])}"
        )
        bundle_root = self.candidate_artifact_root / bundle_id
        manifest_path = self.candidate_artifact_root / f"{bundle_id}.manifest.json"
        self.candidate_artifact_root.chmod(0o755)
        try:
            bundle_root.mkdir(exist_ok=False)
            copied: List[Dict[str, Any]] = []
            for index, (relative, source) in enumerate(sources, 1):
                destination = bundle_root / f"{index:03d}_{_safe_name(source.name)}"
                _clone_or_copy(source, destination)
                copied.append({
                    "worktree_path": relative,
                    "bundle_path": destination.relative_to(bundle_root).as_posix(),
                    "kind": "directory" if destination.is_dir() else "file",
                })
            _make_tree_read_only(bundle_root)
            files, directories = _snapshot_inventory(bundle_root)
            manifest: Dict[str, Any] = {
                "version": 1, "created_at": _utc_now(), "run_id": self.run_id,
                "agent_id": agent.agent_id, "round": round_number,
                "candidate": candidate, "work_item": work_item, "risk": risk,
                "bundle_root": str(bundle_root), "manifest_path": str(manifest_path),
                "read_only": True, "reported_paths": copied,
                "file_count": len(files), "bytes": sum(item["bytes"] for item in files),
                "files": files, "directories": directories,
            }
            canonical = json.dumps(
                manifest, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
            ).encode("utf-8")
            manifest["manifest_sha256"] = hashlib.sha256(canonical).hexdigest()
            manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
            manifest_path.chmod(0o444)
            self._append_jsonl("candidate-artifacts.jsonl", manifest)
            self.candidate_artifact_manifests.append(manifest)
            return manifest
        except Exception:
            _remove_tree_even_if_read_only(bundle_root)
            if manifest_path.exists():
                try:
                    manifest_path.chmod(0o600)
                except OSError:
                    pass
                manifest_path.unlink(missing_ok=True)
            raise
        finally:
            self.candidate_artifact_root.chmod(0o555)

    def _verify_candidate_artifacts(self, full: bool) -> None:
        if not self.candidate_artifact_root.is_dir():
            raise RuntimeError(
                f"candidate artifact root is missing: {self.candidate_artifact_root}"
            )
        if self.candidate_artifact_root.stat().st_mode & 0o222:
            raise RuntimeError(
                f"candidate artifact root became writable: {self.candidate_artifact_root}"
            )
        expected_children = {
            Path(manifest["bundle_root"]).name
            for manifest in self.candidate_artifact_manifests
        } | {
            Path(manifest["manifest_path"]).name
            for manifest in self.candidate_artifact_manifests
        }
        actual_children = {path.name for path in self.candidate_artifact_root.iterdir()}
        if actual_children != expected_children:
            raise RuntimeError(
                "candidate artifact root inventory changed; expected "
                f"{sorted(expected_children)}, got {sorted(actual_children)}"
            )
        for manifest in self.candidate_artifact_manifests:
            verify_evidence_snapshot({
                "sources": [{
                    "snapshot": manifest["bundle_root"],
                    "files": manifest["files"],
                    "directories": manifest["directories"],
                }],
            }, full=full)
            manifest_path = Path(manifest["manifest_path"])
            if not manifest_path.is_file() or manifest_path.stat().st_mode & 0o222:
                raise RuntimeError(
                    f"candidate artifact manifest was mutated: {manifest_path}"
                )
            try:
                persisted = json.loads(manifest_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as exc:
                raise RuntimeError(
                    f"candidate artifact manifest is unreadable: {manifest_path}"
                ) from exc
            expected_hash = persisted.pop("manifest_sha256", None)
            canonical = json.dumps(
                persisted, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
            ).encode("utf-8")
            if hashlib.sha256(canonical).hexdigest() != expected_hash:
                raise RuntimeError(
                    f"candidate artifact manifest hash changed: {manifest_path}"
                )

    def _create_promotion_candidate(
        self,
        candidate: str,
        artifact_paths: List[str],
        manifest_sha256: str,
    ) -> Tuple[str, str]:
        """Create a clean promotion commit without changing the checked-out integration tree.

        The promotion commit is a child of the verified candidate, so its SHA
        still binds the reviewed artifact history. Its tree differs only by
        removing temporary RecCli staging paths. This includes staging inherited
        from a reviewed candidate produced by an earlier organization run.
        """
        integration_branch = self.workspaces[self.topology.finalizer_id].integration_branch
        workspace = self.workspaces[self.topology.finalizer_id]
        staged_paths = self._git_paths(
            workspace,
            [
                "ls-tree", "-r", "--name-only", "-z", candidate, "--",
                ARTIFACT_STAGING_ROOT,
            ],
        )
        paths_to_remove = sorted(staged_paths | set(artifact_paths))
        if not paths_to_remove:
            return candidate, integration_branch
        promotion_branch = f"{integration_branch}-promotion"
        index_path = self.run_dir / f".promotion-index-{uuid.uuid4().hex}"
        env = os.environ.copy()
        env["GIT_INDEX_FILE"] = str(index_path)
        env.setdefault("GIT_AUTHOR_NAME", "RecCli Organization")
        env.setdefault("GIT_AUTHOR_EMAIL", "organization@reccli.local")
        env.setdefault("GIT_COMMITTER_NAME", "RecCli Organization")
        env.setdefault("GIT_COMMITTER_EMAIL", "organization@reccli.local")

        def run_git(args: List[str], input_text: Optional[str] = None) -> str:
            proc = subprocess.run(
                ["git", *args], cwd=workspace.cwd, env=env,
                input=input_text, text=True, capture_output=True, check=False,
            )
            if proc.returncode != 0:
                raise RuntimeError(
                    f"git {' '.join(args)} failed: {(proc.stderr or proc.stdout).strip()}"
                )
            return proc.stdout.strip()

        try:
            run_git(["read-tree", candidate])
            for source in paths_to_remove:
                run_git(["update-index", "--force-remove", "--", source])
            tree = run_git(["write-tree"])
            promotion_candidate = run_git(
                ["commit-tree", tree, "-p", candidate],
                (
                    f"reccli: export organization artifacts for {self.run_id}\n\n"
                    f"Artifact-Manifest-SHA256: {manifest_sha256}\n"
                ),
            )
            _git(
                self.project_root,
                ["branch", "-f", promotion_branch, promotion_candidate],
            )
            return promotion_candidate, promotion_branch
        finally:
            index_path.unlink(missing_ok=True)

    def _write_promotion_request(
        self,
        verified_candidate: str,
        promotion_candidate: str,
        promotion_branch: str,
        artifact_manifest: Dict[str, Any],
    ) -> Dict[str, Any]:
        """Bind reversible run output into a proposal without applying it."""
        workspace = self.workspaces[self.topology.finalizer_id]
        base = workspace.base_commit or _git(workspace.cwd, ["rev-parse", "HEAD"]).strip()
        changed_paths = sorted(self._git_paths(
            workspace, ["diff", "--name-only", "-z", f"{base}..{promotion_candidate}"],
        ))
        request: Dict[str, Any] = {
            "version": 1,
            "created_at": _utc_now(),
            "run_id": self.run_id,
            "status": "awaiting_human_authorization",
            "canonical_effects_applied": False,
            "verified_candidate": verified_candidate,
            "proposed_promotion_candidate": promotion_candidate,
            "proposed_promotion_branch": promotion_branch,
            "changed_paths": changed_paths,
            "protected_paths": list(self.protected_paths),
            "experiment_budget": {
                "maximum": self.max_experiments,
                "used": len(self.candidate_artifact_manifests),
            },
            "evidence_manifest": (
                str(self.run_dir / "evidence-manifest.json")
                if self.evidence_manifest else None
            ),
            "evidence_manifest_sha256": (
                self.evidence_manifest.get("manifest_sha256")
                if self.evidence_manifest else None
            ),
            "context_pack_manifest": (
                str(self.run_dir / "context-pack-manifest.json")
                if self.context_pack_manifest else None
            ),
            "context_pack_manifest_sha256": (
                self.context_pack_manifest.get("manifest_sha256")
                if self.context_pack_manifest else None
            ),
            "candidate_artifact_bundles": [
                {
                    "candidate": manifest["candidate"],
                    "work_item": manifest["work_item"],
                    "manifest_path": manifest["manifest_path"],
                    "manifest_sha256": manifest["manifest_sha256"],
                }
                for manifest in self.candidate_artifact_manifests
            ],
            "run_artifact_manifest": str(self.run_dir / "artifact-manifest.json"),
            "run_artifact_manifest_sha256": artifact_manifest["manifest_sha256"],
            "review_record": self.governance.snapshot(),
            "authorization_required_for": [
                "merging or pushing the proposed candidate to the caller's canonical branch",
                "importing any generated-output bundle into the canonical experiment archive",
                "accepting proposed ledger, authority-document, visual, or scientific-standard changes",
                "modifying immutable evidence, introducing new authoritative inputs, or approving unsupported claims",
            ],
        }
        canonical = json.dumps(
            request, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
        ).encode("utf-8")
        request["request_sha256"] = hashlib.sha256(canonical).hexdigest()
        self._write_json("promotion-request.json", request)
        return request

    def _git_paths(self, workspace: Workspace, args: List[str]) -> Set[str]:
        proc = subprocess.run(
            ["git", *args], cwd=workspace.cwd, capture_output=True, check=False,
        )
        if proc.returncode != 0:
            raise RuntimeError(
                f"git {' '.join(args)} failed: "
                f"{proc.stderr.decode('utf-8', errors='replace').strip()}"
            )
        return {
            item.decode("utf-8", errors="surrogateescape")
            for item in proc.stdout.split(b"\0") if item
        }

    def _verify_caller_repository_unchanged(self) -> None:
        if not self.caller_head:
            return
        current_head = _git(self.project_root, ["rev-parse", "HEAD"]).strip()
        tracked_status = _git(
            self.project_root, ["status", "--porcelain", "--untracked-files=no"],
        ).strip()
        if current_head != self.caller_head or tracked_status:
            raise RuntimeError(
                "caller repository changed during organization run; canonical effects are forbidden"
            )

    def _changed_paths(self, workspace: Workspace) -> Set[str]:
        base = workspace.base_commit or _git(workspace.cwd, ["rev-parse", "HEAD"]).strip()
        paths = self._git_paths(
            workspace, ["diff", "--name-only", "-z", f"{base}..HEAD"],
        )
        paths.update(self._git_paths(
            workspace, ["diff", "--name-only", "-z", "HEAD"],
        ))
        paths.update(self._git_paths(
            workspace, ["ls-files", "--others", "--exclude-standard", "-z"],
        ))
        return {
            path for path in paths
            if not self._runtime_path(workspace, path)
        }

    @staticmethod
    def _runtime_path(workspace: Workspace, path: str) -> bool:
        return any(
            path == runtime or path.startswith(runtime + "/")
            for runtime in workspace.runtime_paths
        )

    def _uncommitted_paths(self, workspace: Workspace) -> Set[str]:
        paths = self._git_paths(
            workspace, ["diff", "--name-only", "-z", "HEAD"],
        )
        paths.update(self._git_paths(
            workspace, ["diff", "--cached", "--name-only", "-z"],
        ))
        paths.update(self._git_paths(
            workspace, ["ls-files", "--others", "--exclude-standard", "-z"],
        ))
        return {
            path for path in paths
            if not self._runtime_path(workspace, path)
        }

    def _artifact_path(self, path: str) -> bool:
        return path == self.artifact_staging_prefix or path.startswith(
            self.artifact_staging_prefix + "/"
        )

    def _commit_patch_id(self, workspace: Workspace, commit: str) -> Optional[str]:
        shown = subprocess.run(
            ["git", "show", "--pretty=format:", "--binary", commit],
            cwd=workspace.cwd, capture_output=True, check=False,
        )
        if shown.returncode != 0:
            raise RuntimeError(f"cannot inspect integration commit {commit}")
        patched = subprocess.run(
            ["git", "patch-id", "--stable"], cwd=workspace.cwd,
            input=shown.stdout, capture_output=True, check=False,
        )
        if patched.returncode != 0:
            raise RuntimeError(f"cannot compute patch identity for {commit}")
        output = patched.stdout.decode("ascii", errors="replace").strip()
        return output.split()[0] if output else None

    def _approved_patch_ids(self, workspace: Workspace) -> Set[str]:
        base = workspace.base_commit or _git(workspace.cwd, ["rev-parse", "HEAD"]).strip()
        result: Set[str] = set()
        for assignment in self.governance.assignments.values():
            if assignment.get("status") not in {"approved", "reviewed"}:
                continue
            candidate = assignment.get("candidate")
            try:
                commits = _git(
                    workspace.cwd, ["rev-list", "--reverse", f"{base}..{candidate}"],
                ).splitlines()
            except RuntimeError:
                continue
            for commit in commits:
                patch_id = self._commit_patch_id(workspace, commit)
                if patch_id:
                    result.add(patch_id)
        return result

    @staticmethod
    def _host_git_environment() -> Dict[str, str]:
        env = os.environ.copy()
        env.setdefault("GIT_AUTHOR_NAME", "RecCli Organization")
        env.setdefault("GIT_AUTHOR_EMAIL", "organization@reccli.local")
        env.setdefault("GIT_COMMITTER_NAME", "RecCli Organization")
        env.setdefault("GIT_COMMITTER_EMAIL", "organization@reccli.local")
        return env

    def _host_git(
        self,
        workspace: Workspace,
        args: List[str],
        *,
        check: bool = True,
    ) -> subprocess.CompletedProcess:
        proc = subprocess.run(
            ["git", *args],
            cwd=workspace.cwd,
            env=self._host_git_environment(),
            text=True,
            capture_output=True,
            check=False,
        )
        if check and proc.returncode != 0:
            raise RuntimeError(
                f"host git {' '.join(args)} failed: "
                f"{(proc.stderr or proc.stdout).strip()}"
            )
        return proc

    @staticmethod
    def _reply_uses_host_candidate(reply: Dict[str, Any]) -> bool:
        return (
            reply.get("candidate") == HOST_CANDIDATE
            or any(
                message.get("candidate") == HOST_CANDIDATE
                for message in reply.get("messages", [])
            )
        )

    @staticmethod
    def _resolve_reply_candidate(
        reply: Dict[str, Any],
        candidate: str,
        *,
        previous_heads: Optional[Set[str]] = None,
        rewrite_previous_candidates: bool = False,
    ) -> None:
        if reply.get("candidate") == HOST_CANDIDATE:
            reply["candidate"] = candidate
        elif (
            rewrite_previous_candidates
            and previous_heads
            and reply.get("candidate") in previous_heads
        ):
            reply["candidate"] = candidate
        for message in reply.get("messages", []):
            if message.get("candidate") == HOST_CANDIDATE:
                message["candidate"] = candidate
            elif (
                rewrite_previous_candidates
                and previous_heads
                and message.get("tag") in {"handoff", "review"}
                and message.get("candidate") in previous_heads
            ):
                message["candidate"] = candidate

    def _materialize_agent_candidate(
        self,
        agent: AgentSpec,
        reply: Dict[str, Any],
        previous_head: str,
        round_number: int,
    ) -> str:
        """Validate edits, commit them as the host, and resolve reply markers."""
        workspace = self.workspaces[agent.agent_id]
        self._validate_agent_write_scope(agent)
        uses_marker = self._reply_uses_host_candidate(reply)
        provider_head = _git(workspace.cwd, ["rev-parse", "HEAD"]).strip()
        if agent.write_scope == "workspace":
            pathspecs = ["."]
            for runtime in sorted(workspace.runtime_paths):
                pathspecs.extend([
                    f":(exclude){runtime}",
                    f":(exclude){runtime}/**",
                ])
            for raw in reply.get("artifacts", []):
                supplied = Path(raw).expanduser()
                source = supplied if supplied.is_absolute() else workspace.cwd / supplied
                try:
                    relative = source.resolve().relative_to(
                        workspace.cwd.resolve(),
                    ).as_posix()
                except ValueError:
                    continue
                pathspecs.extend([
                    f":(exclude){relative}",
                    f":(exclude){relative}/**",
                ])
            self._host_git(workspace, ["add", "-A", "--", *pathspecs])
        elif agent.write_scope in {"artifacts", "integration"}:
            artifact_root = workspace.cwd / self.artifact_staging_prefix
            tracked_artifacts = _git(
                workspace.cwd,
                ["ls-files", "--", self.artifact_staging_prefix],
            ).strip()
            if artifact_root.exists() or tracked_artifacts:
                self._host_git(
                    workspace,
                    ["add", "-A", "-f", "--", self.artifact_staging_prefix],
                )

        if agent.write_scope == "workspace":
            artifact_root = workspace.cwd / self.artifact_staging_prefix
            tracked_artifacts = _git(
                workspace.cwd,
                ["ls-files", "--", self.artifact_staging_prefix],
            ).strip()
            if artifact_root.exists() or tracked_artifacts:
                self._host_git(
                    workspace,
                    ["add", "-A", "-f", "--", self.artifact_staging_prefix],
                )

        staged = self._host_git(
            workspace, ["diff", "--cached", "--quiet"], check=False,
        ).returncode == 1
        create_empty_identity = (
            uses_marker
            and agent.agent_id in self.topology.worker_ids
            and not staged
        )
        if staged or create_empty_identity:
            commit_args = [
                "-c", "commit.gpgsign=false",
                "commit", "--no-verify", "--no-gpg-sign",
                "-m", (
                    f"reccli({agent.agent_id}): materialize round "
                    f"{round_number} candidate"
                ),
            ]
            if create_empty_identity:
                commit_args.insert(3, "--allow-empty")
            self._host_git(workspace, commit_args)
            head = _git(workspace.cwd, ["rev-parse", "HEAD"]).strip()
        else:
            head = _git(workspace.cwd, ["rev-parse", "HEAD"]).strip()

        self._resolve_reply_candidate(
            reply,
            head,
            previous_heads={previous_head, provider_head},
            rewrite_previous_candidates=(
                agent.agent_id in (
                    set(self.topology.worker_ids)
                    | {self.topology.finalizer_id}
                )
                and head != previous_head
            ),
        )
        self._validate_agent_write_scope(agent)
        if staged or create_empty_identity:
            self._event(
                "candidate.materialized",
                round_number,
                agent_id=agent.agent_id,
                candidate=head,
                empty=create_empty_identity,
            )
        return head

    def _sync_reviewed_candidates(
        self,
        round_number: int,
    ) -> None:
        """Apply already-reviewed handoffs in the integration worktree.

        Native provider sandboxes deliberately protect Git administrative
        files.  Git mutation therefore belongs to the trusted RecCli host, not
        to Claude or Codex.  Hierarchy and veto checks still happen before a
        handoff reaches this inbox.
        """
        finalizer_id = self.topology.finalizer_id
        workspace = self.workspaces[finalizer_id]
        messages = list(self.inboxes[finalizer_id])
        for message in messages:
            if message.get("tag") != "handoff" or not message.get("candidate"):
                continue
            candidate = str(message["candidate"])
            if candidate in self.integrated_candidates:
                continue
            assignment = self.governance.assignments.get(candidate)
            if not assignment or assignment.get("status") not in {
                "approved", "reviewed",
            }:
                continue
            if message.get("from") != assignment.get("primaryManagerId"):
                continue
            base = workspace.base_commit or _git(
                workspace.cwd, ["rev-parse", "HEAD"],
            ).strip()
            ancestry = self._host_git(
                workspace,
                ["merge-base", "--is-ancestor", base, candidate],
                check=False,
            )
            if ancestry.returncode != 0:
                self._system_message(
                    finalizer_id,
                    "blocker",
                    f"Candidate {candidate} is not descended from frozen base {base}; host integration refused it.",
                    round_number,
                    candidate,
                    assignment.get("workItem"),
                    assignment.get("risk"),
                )
                continue
            commits = _git(
                workspace.cwd,
                ["rev-list", "--reverse", f"{base}..{candidate}"],
            ).splitlines()
            integrated_patch_ids = {
                patch_id
                for commit in _git(
                    workspace.cwd,
                    ["rev-list", "--reverse", f"{base}..HEAD"],
                ).splitlines()
                if (patch_id := self._commit_patch_id(workspace, commit))
            }
            applied: List[str] = []
            try:
                for commit in commits:
                    patch_id = self._commit_patch_id(workspace, commit)
                    if patch_id and patch_id in integrated_patch_ids:
                        continue
                    self._host_git(
                        workspace,
                        ["cherry-pick", "--allow-empty", commit],
                    )
                    applied.append(commit)
                    if patch_id:
                        integrated_patch_ids.add(patch_id)
            except Exception as exc:
                self._host_git(
                    workspace, ["cherry-pick", "--abort"], check=False,
                )
                content = (
                    f"RecCli host could not integrate reviewed candidate "
                    f"{candidate}: {exc}. A worker-owned successor or explicit "
                    "conflict decision is required."
                )
                self._system_message(
                    finalizer_id, "blocker", content, round_number,
                    candidate, assignment.get("workItem"), assignment.get("risk"),
                )
                self._system_message(
                    assignment["primaryManagerId"], "blocker", content,
                    round_number, candidate, assignment.get("workItem"),
                    assignment.get("risk"),
                )
                continue
            head = _git(workspace.cwd, ["rev-parse", "HEAD"]).strip()
            self.integrated_candidates[candidate] = head
            self._validate_agent_write_scope(
                self.topology.agent(finalizer_id),
            )
            self._event(
                "integration.host_applied",
                round_number,
                candidate=candidate,
                integration_head=head,
                commits=applied,
            )
            self._system_message(
                finalizer_id,
                "status",
                f"RecCli host integrated reviewed candidate {candidate}; exact integration HEAD is {head}. Do not cherry-pick it again. Test the composed tree and use {HOST_CANDIDATE} for any new release-candidate message.",
                round_number,
                head,
                assignment.get("workItem"),
                assignment.get("risk"),
            )

    def _validate_agent_write_scope(self, agent: AgentSpec) -> None:
        workspace = self.workspaces[agent.agent_id]
        changed = self._changed_paths(workspace)
        protected_changes = {
            path for path in changed
            if any(
                path == protected or path.startswith(protected + "/")
                for protected in self.protected_paths
            )
        }
        if protected_changes:
            raise RuntimeError(
                f"{agent.agent_id} changed deny-write protected paths: "
                f"{', '.join(sorted(protected_changes))}"
            )
        if agent.write_scope == "workspace":
            return
        if agent.write_scope == "none":
            if changed:
                raise RuntimeError(
                    f"{agent.agent_id} is read-only but changed: {', '.join(sorted(changed))}"
                )
            return
        outside_artifacts = {path for path in changed if not self._artifact_path(path)}
        if agent.write_scope == "artifacts":
            if outside_artifacts:
                raise RuntimeError(
                    f"{agent.agent_id} may write only {self.artifact_staging_prefix}; "
                    f"rejected paths: {', '.join(sorted(outside_artifacts))}"
                )
            return
        if agent.write_scope != "integration":
            raise RuntimeError(f"unsupported write scope: {agent.write_scope}")

        uncommitted = self._uncommitted_paths(workspace)
        illegal_uncommitted = {
            path for path in uncommitted if not self._artifact_path(path)
        }
        if illegal_uncommitted:
            raise RuntimeError(
                f"{agent.agent_id} is integration-only and directly modified: "
                f"{', '.join(sorted(illegal_uncommitted))}"
            )
        base = workspace.base_commit or _git(workspace.cwd, ["rev-parse", "HEAD"]).strip()
        approved_patch_ids = self._approved_patch_ids(workspace)
        for commit in _git(
            workspace.cwd, ["rev-list", "--reverse", f"{base}..HEAD"],
        ).splitlines():
            commit_paths = self._git_paths(
                workspace,
                ["diff-tree", "--no-commit-id", "--name-only", "-r", "-z", commit],
            )
            if not commit_paths:
                continue
            if commit_paths and all(self._artifact_path(path) for path in commit_paths):
                continue
            patch_id = self._commit_patch_id(workspace, commit)
            if not patch_id or patch_id not in approved_patch_ids:
                raise RuntimeError(
                    f"{agent.agent_id} may integrate only independently reviewed, non-vetoed patches; "
                    f"commit {commit} was not eligible"
                )

    def _run_turn(self, agent: AgentSpec, round_number: int) -> Dict[str, Any]:
        started = time.monotonic()
        if agent.agent_id == self.topology.finalizer_id:
            self._sync_reviewed_candidates(round_number)
        # Keep the inbox durable until a provider turn completes. A timeout,
        # quota error, or malformed reply must not silently consume messages.
        inbox = list(self.inboxes[agent.agent_id])
        previous_head = _git(
            self.workspaces[agent.agent_id].cwd,
            ["rev-parse", "HEAD"],
        ).strip()
        prompt = self._build_prompt(agent, inbox, round_number, agent.agent_id not in self.turned)
        session = self.sessions.get(agent.agent_id)
        if session is None:
            provider = self.provider_by_agent[agent.agent_id]
            session = SubscriptionSession(
                provider, self.workspaces[agent.agent_id], agent.writable,
                agent.agent_id, self.run_dir, self.model, agent.reasoning,
            )
            self.sessions[agent.agent_id] = session
        result = session.run(prompt, AGENT_REPLY_SCHEMA, self.turn_timeout_seconds)
        reply = validate_agent_reply(result["value"])
        self._materialize_agent_candidate(
            agent, reply, previous_head, round_number,
        )
        self.inboxes[agent.agent_id] = []
        return {
            "agent": agent, "reply": reply, "usage": result.get("usage", {}),
            "provider": session.provider,
            "session_id": result.get("session_id"),
            "duration_ms": int((time.monotonic() - started) * 1000),
        }

    def _scientific_review_context(
        self, inbox: List[Dict[str, Any]], max_chars: int = 24_000,
    ) -> str:
        if self.topology.review_policy != "veto" or not any(
            message.get("tag") == "review" for message in inbox
        ):
            return "_No adversarial review assignment in this turn._"
        candidates = {
            message.get("candidate") for message in inbox
            if message.get("tag") == "review" and message.get("candidate")
        }
        work_items = {
            message.get("workItem") for message in inbox
            if message.get("tag") == "review" and message.get("workItem")
        }
        records: List[Dict[str, Any]] = []
        message_path = self.run_dir / "messages.jsonl"
        if message_path.exists():
            for raw in message_path.read_text(encoding="utf-8").splitlines():
                try:
                    record = json.loads(raw)
                except json.JSONDecodeError:
                    continue
                if (
                    record.get("candidate") in candidates
                    or record.get("workItem") in work_items
                    or "final-release" in work_items
                ):
                    records.append(record)
        bundles = [
            {
                "candidate": manifest["candidate"],
                "work_item": manifest["work_item"],
                "manifest_path": manifest["manifest_path"],
                "manifest_sha256": manifest["manifest_sha256"],
            }
            for manifest in self.candidate_artifact_manifests
            if (
                manifest["candidate"] in candidates
                or manifest["work_item"] in work_items
                or "final-release" in work_items
            )
        ]
        payload = json.dumps(
            {"relevant_decision_messages": records, "sealed_bundles": bundles},
            indent=2, ensure_ascii=False,
        )
        return payload[-max_chars:]

    def _build_prompt(
        self, agent: AgentSpec, inbox: List[Dict[str, Any]], round_number: int, first_turn: bool,
    ) -> str:
        team = "\n".join(
            f"- {member.agent_id} [{member.role}]: {member.instructions}"
            for member in self.topology.agents
        )
        routes = "\n".join(
            f"- {neighbor} [{self.topology.agent(neighbor).role}]"
            for neighbor in self.topology.neighbors(agent.agent_id)
        ) or "_No outbound neighbors._"
        inbox_text = "\n".join(
            f"{index}. From {message['from']} [{message['tag']}]: {message['content']}"
            + (f" candidate={message.get('candidate')} work={message.get('workItem')} risk={message.get('risk')}" if message.get("candidate") else "")
            for index, message in enumerate(inbox, 1)
        ) or "_No new messages._"
        workspace = self.workspaces[agent.agent_id]
        if agent.write_scope == "none":
            write_policy = "This is a read-only role. Do not modify files or create commits."
        elif agent.write_scope == "artifacts":
            write_policy = (
                f"You may write only under {self.artifact_staging_prefix}/. "
                "Do not stage or commit it yourself; RecCli owns Git materialization. "
                "Any project file change outside that prefix rejects the turn."
            )
        elif agent.write_scope == "integration":
            write_policy = (
                "You are integration-only: RecCli applies exact candidates after their adversarial review completes without a veto. "
                "Do not run cherry-pick, merge, add, commit, or author normal repository changes. "
                f"You may author run artifacts under {self.artifact_staging_prefix}/; RecCli commits them after validating your reply."
            )
        else:
            write_policy = (
                "Edit and test one cohesive candidate in this worktree. Do not "
                "run git add, commit, merge, or cherry-pick. RecCli validates "
                "the write scope and creates the immutable commit after your "
                f"turn. Use `{HOST_CANDIDATE}` anywhere your reply must refer "
                "to that resulting candidate."
            )
        evidence_policy = "_No explicit ignored or external evidence was selected for this run._"
        if self.evidence_manifest:
            mappings = "\n".join(
                f"- Immutable source `{entry['source']}` is available only as `{entry['snapshot']}` ({entry['file_count']} files; {entry['bytes']} bytes)."
                for entry in self.evidence_manifest["sources"]
            )
            evidence_policy = f"""Manifest: `{self.run_dir / 'evidence-manifest.json'}`
Snapshot root: `{self.evidence_manifest['snapshot_root']}`

{mappings}

Treat the snapshot as immutable primary evidence. Read snapshot paths, never the original source paths. Do not chmod, replace, delete, or add snapshot content. RecCli verifies its inventory after each round and its hashes before release."""
        context_policy = "_No organization context manifest was selected for this run._"
        if self.context_pack_manifest:
            pack = self.context_pack_manifest["agent_packs"][agent.agent_id]
            reading_paths = (
                "\n".join(
                    f"{index}. `{path}`"
                    for index, path in enumerate(pack["declared_reading_paths"], 1)
                ) or "_No declared reading paths._"
                if first_turn else
                f"_The full reading list remains in `{pack['index']}` and is not repeated on resumed turns._"
            )
            library_paths = (
                "\n".join(
                    f"- `{path}`"
                    for path in pack["declared_library_paths"]
                ) or "_No indexed library paths._"
                if first_turn else
                f"_The indexed library remains in `{pack['index']}` and is not repeated on resumed turns._"
            )
            if first_turn and pack["scope"] == "full-union":
                first_read = (
                    "Read the required common authority before substantive work. The worker lanes and declared library paths are a full-visibility indexed library, not an instruction to ingest every lane immediately; read the relevant entries before deciding on work that touches them. An adversarial review must read every lane and library record relevant to the exact candidate."
                )
            elif first_turn:
                first_read = (
                    "Before substantive work in your first turn, read the required common-plus-lane paths in the declared order. Do not ingest every indexed library record up front; consult the records relevant to your hypothesis, failure mode, or candidate before acting, and ground messages in the source paths."
                )
            else:
                first_read = (
                    "Revisit the assigned pack whenever this turn touches its contracts or evidence."
                )
            context_policy = f"""Scope: {pack['scope']}
Context box: `{pack['root']}`
Pack index: `{pack['index']}`

{pack['description'] or 'Shared project context plus the lane selected by the repository manifest.'}

Required reading order:

{reading_paths}

Indexed reference library (read relevant entries on demand):

{library_paths}

{first_read}

This is a hash-bound, read-only educational routing view. Files under `canonical/` preserve their project-relative paths. Canonical repository files remain authoritative and readable on demand; this assignment is not a deny-read boundary. Do not modify, replace, or add context-box content. Managers and designated auditors may receive the full union of worker lanes."""
        protected_policy = (
            "\n".join(f"- `{path}`" for path in self.protected_paths)
            if self.protected_paths else
            "_No tracked deny-write paths were declared. Immutable ignored/external evidence remains protected by the snapshot regardless._"
        )
        experiment_remaining = max(
            0, self.max_experiments - len(self.candidate_artifact_manifests),
        )
        review_context = self._scientific_review_context(inbox)
        first_context = ""
        if first_turn:
            first_context = f"""
## Mission

{self.mission}

## RecCli project memory

{self.project_context}

## Organization charter

{self.topology.name}: {self.topology.description}

Culture: {self.topology.culture}

{team}
"""
        final_instruction = (
            f"You may set final=true only for the exact integration HEAD after the reversible candidate and promotion dossier are complete. This does not declare canonical scientific acceptance or apply effects. Set candidate to `{HOST_CANDIDATE}` and risk=release; RecCli resolves it to the exact host-owned HEAD. Required reviewers: {', '.join(sorted(self.governance.required_final_approvers())) or 'none'}."
            if agent.agent_id == self.topology.finalizer_id
            else "You are not the finalizer. Set final=false and top-level candidate/risk to null."
        )
        closeout_instruction = ""
        if round_number > self.max_rounds:
            closeout_instruction = f"""
## Closeout-only pass

The {self.max_rounds}-round work budget is exhausted. This is closeout pass {round_number - self.max_rounds} of {self.max_closeout_rounds}. Do not initiate new implementation, experiments, hypotheses, or feature scope. Review and route an already-produced exact candidate, integrate an already-reviewed candidate, answer a release blocker, or finalize. If no such action remains, return state=done without inventing work.
"""
        runtime_note = (
            f"Worktree Python launcher: `{workspace.cwd / '.venv/bin/python'}`\n"
            "It executes the canonical project environment while placing this "
            "worktree's `src/` and root first on PYTHONPATH. Prefer "
            "`.venv/bin/python -m pytest ...`; do not repair, copy, or commit "
            "the runtime bridge."
            if ".venv" in workspace.runtime_paths else
            "No canonical `.venv/bin/python` was detected. Use the repository's documented runtime setup."
        )
        return f"""# RecCli organization turn: {agent.agent_id}

Run: {self.run_id}
Round: {min(round_number, self.max_rounds)} of {self.max_rounds}{f" (closeout {round_number - self.max_rounds} of {self.max_closeout_rounds})" if round_number > self.max_rounds else ""}
Role: {agent.role}
Native provider: {self.provider_by_agent[agent.agent_id]}
Standing instructions: {agent.instructions}
{first_context}
## Communication routes

{routes}

Messages outside these routes are dropped and recorded.

## Workspace

Working directory: {workspace.cwd}
Your branch: {workspace.branch}
Integration branch: {workspace.integration_branch}
Integration workspace: {workspace.integration_workspace}
Write scope: {agent.write_scope}
{write_policy}

## Candidate runtime

{runtime_note}

## Immutable evidence snapshot

{evidence_policy}

## Assigned documentation context

{context_policy}

## Deny-write protected tracked paths

{protected_policy}

Any change to a declared protected path rejects the turn even in a writable worktree. Draft proposed authority changes under the run artifact prefix instead of editing canonical-path files.

## Reversible experiment budget

Sealed generated-output bundles used: {len(self.candidate_artifact_manifests)} of {self.max_experiments}. Remaining: {experiment_remaining}.

This is a hard resource limit, not a judgment of novelty or scientific value. Use a temporary run-local identifier such as `{self.run_id}/{agent.agent_id}/r{round_number}`. Do not mint, reserve, or claim a canonical experiment/attempt ID; canonical IDs are assigned only at human-authorized archive import.

## Durable artifact protocol

Repository source, tests, and permanent product documentation belong at their normal tracked paths. Run-scoped reports, plans, generated deliverables, and design-decision artifacts do not. The project may ignore `devsession/`, so NEVER use the ignored run directory as a Git handoff surface and do not substitute the permanent `docs/` tree for temporary organization artifacts.

Write every run-scoped artifact under this tracked prefix instead:

`{self.artifact_staging_prefix}/<path-relative-to-the-run-directory>`

For example, a requested run output named `devsession/agent-organizations/{self.run_id}/00-roadmap.md` must be authored as `{self.artifact_staging_prefix}/00-roadmap.md` in your worktree. Do not stage or commit it. RecCli force-stages that exact prefix after enforcing your scope and binds it to the host-created candidate.

Reviewers inspect staged artifacts from immutable candidates with `git show <candidate>:{self.artifact_staging_prefix}/<path>`. The release manager composes reviewed artifacts under the same prefix. RecCli exports those files to `{self.run_dir / 'deliverables'}` and creates a local proposed-promotion branch whose final tree omits the temporary staging prefix. For a scientific run, this remains reversible and is not merged, pushed, imported, or canonically accepted by RecCli.

Large ignored/generated experiment outputs are a different channel: leave each new output at its expected path inside your isolated worktree and list that relative path in the reply `artifacts` array. The same reply must contain exactly one candidate handoff using `{HOST_CANDIDATE}`. After the turn, RecCli creates or resolves the immutable candidate, then clones/copies only those explicit untracked paths into `{self.candidate_artifact_root}`, makes the bundle read-only, hashes every file, and sends its manifest to the primary manager. Do not list tracked source, caches, environments, original evidence, or the Git-backed staging prefix. Unreported ignored output is not a durable handoff.

## Information policy

Read the original mission, acceptance criteria, source and tests, task-relevant repository documentation, applicable interfaces, and published design decisions. Do not rely on code alone. Routine unrelated traffic stays need-to-know. A scientific adversarial reviewer receives the full relevant durable decision record, candidate bundle references, and primary evidence; independence comes from veto-only authority and a different objective, not from information starvation.

## Fully-sighted adversarial review record

{review_context}

## Review governance

{self.governance.render(agent.agent_id)}

## Inbox

{inbox_text}
{closeout_instruction}

## This turn

Complete one cohesive unit of work and inspect real evidence before making claims. If blocked, name the owner and route a question or blocker. {final_instruction}

Return only the schema-constrained reply. Every message must include candidate, workItem, and risk, using null when not applicable. Worker handoffs require all three and must use `{HOST_CANDIDATE}` for the candidate produced by this turn. Under veto review, the assigned auditor must begin its decision with NO_VETO or BLOCKED and name the exact candidate; NO_VETO means only that no blocking falsification was established, never that the scientific claim is true. Other approval decisions begin with APPROVED or BLOCKED.
"""

    def _control_targets(self, target: str) -> List[str]:
        if target == "all":
            return [agent.agent_id for agent in self.topology.agents]
        if target == "lead":
            return [self.topology.leader_id]
        if target == "finalizer":
            return [self.topology.finalizer_id]
        if target == "managers":
            return list(self.topology.manager_ids)
        if target == "workers":
            return list(self.topology.worker_ids)
        if target == "integrators":
            return sorted(self.topology.integrator_ids)
        self.topology.agent(target)
        return [target]

    def _operator_message(
        self,
        recipient: str,
        tag: str,
        content: str,
        round_number: int,
        control_id: str,
        requested_by: str,
    ) -> None:
        message = {
            "runId": self.run_id,
            "round": round_number,
            "from": requested_by or "human-operator",
            "to": recipient,
            "tag": tag,
            "content": content,
            "candidate": None,
            "workItem": f"operator-control/{control_id}",
            "risk": "routine",
            "deliveredAt": _utc_now(),
            "status": "delivered",
            "control_id": control_id,
            "operator_message": True,
        }
        self.inboxes[recipient].append(message)
        self.states[recipient] = "working"
        self.delivered_messages += 1
        self._append_jsonl("messages.jsonl", message)

    def _apply_control_requests(self, boundary_round: int) -> Optional[str]:
        from .organization_control import (
            acknowledge_control_request,
            pending_control_requests,
        )

        for request in pending_control_requests(self.run_dir):
            action = str(request.get("action") or "")
            try:
                if action == "message":
                    targets = self._control_targets(str(request.get("target") or ""))
                    for recipient in targets:
                        self._operator_message(
                            recipient=recipient,
                            tag=str(request.get("tag") or "plan"),
                            content=str(request.get("content") or ""),
                            round_number=boundary_round,
                            control_id=str(request["id"]),
                            requested_by=str(
                                request.get("requested_by") or "human-operator",
                            ),
                        )
                    acknowledge_control_request(
                        self.run_dir,
                        request,
                        "applied",
                        (
                            "Operator message added to the target inboxes for "
                            "the next eligible turn."
                        ),
                        applied_round=boundary_round,
                        targets=targets,
                    )
                    self._event(
                        "control.message.applied",
                        boundary_round,
                        control_id=request["id"],
                        targets=targets,
                    )
                elif action == "pause":
                    self.paused = True
                    acknowledge_control_request(
                        self.run_dir,
                        request,
                        "applied",
                        "Organization paused at a safe round boundary.",
                        applied_round=boundary_round,
                    )
                    self._event(
                        "control.pause.applied",
                        boundary_round,
                        control_id=request["id"],
                    )
                elif action == "resume":
                    self.paused = False
                    acknowledge_control_request(
                        self.run_dir,
                        request,
                        "applied",
                        "Organization resumed from its safe boundary.",
                        applied_round=boundary_round,
                    )
                    self._event(
                        "control.resume.applied",
                        boundary_round,
                        control_id=request["id"],
                    )
                elif action == "cancel":
                    (self.run_dir / "cancel.requested").write_text(
                        _utc_now() + "\n",
                        encoding="utf-8",
                    )
                    acknowledge_control_request(
                        self.run_dir,
                        request,
                        "applied",
                        "Cancellation applied at a safe round boundary.",
                        applied_round=boundary_round,
                    )
                    self._event(
                        "control.cancel.applied",
                        boundary_round,
                        control_id=request["id"],
                    )
                    return "cancelled"
                else:
                    raise ValueError(f"unsupported control action: {action}")
            except Exception as exc:
                acknowledge_control_request(
                    self.run_dir,
                    request,
                    "rejected",
                    str(exc),
                    applied_round=boundary_round,
                )
                self._event(
                    "control.rejected",
                    boundary_round,
                    control_id=request.get("id"),
                    action=action,
                    reason=str(exc),
                )
        return None

    def _wait_while_paused(self, boundary_round: int) -> bool:
        if not self.paused:
            return False
        self._status(
            "paused",
            round_number=boundary_round,
            detail=(
                f"Paused after round {boundary_round}; waiting for a durable "
                "resume or cancellation request"
            ),
            scheduled_turns=0,
        )
        while self.paused:
            if (self.run_dir / "cancel.requested").exists():
                return True
            time.sleep(0.5)
            if self._apply_control_requests(boundary_round) == "cancelled":
                return True
        return False

    def _reject_pending_control_requests(
        self,
        terminal_status: str,
        round_number: int,
    ) -> None:
        from .organization_control import (
            acknowledge_control_request,
            pending_control_requests,
        )

        for request in pending_control_requests(self.run_dir):
            acknowledge_control_request(
                self.run_dir,
                request,
                "rejected",
                f"Run reached terminal status {terminal_status} before application.",
                applied_round=round_number,
            )

    def _deliver_message(self, sender: str, message: Dict[str, Any], round_number: int) -> None:
        recipient = message.get("to", "")
        tag = message.get("tag", "")
        allowed, reason = self.topology.can_route(sender, recipient, tag)
        if not allowed:
            self.dropped_messages += 1
            self._append_jsonl("messages.jsonl", {"round": round_number, "from": sender, **message, "status": "dropped", "reason": reason, "ts": _utc_now()})
            return
        if (
            recipient in self.topology.worker_ids
            and sender in self.topology.manager_ids
            and tag in {"plan", "handoff"}
        ):
            if not message.get("workItem") or message.get("risk") not in RISKS:
                self.dropped_messages += 1
                self._append_jsonl("messages.jsonl", {
                    "round": round_number,
                    "from": sender,
                    **message,
                    "status": "dropped",
                    "reason": (
                        "worker delegation requires a named workItem and risk"
                    ),
                    "ts": _utc_now(),
                })
                return
        if (
            self.topology.delegation_gate
            and recipient in self.topology.manager_ids
            and sender == self.topology.leader_id
            and tag in {"plan", "handoff"}
            and (not message.get("workItem") or message.get("risk") not in RISKS)
        ):
            self.dropped_messages += 1
            self._append_jsonl("messages.jsonl", {
                "round": round_number,
                "from": sender,
                **message,
                "status": "dropped",
                "reason": (
                    "manager delegation requires a named workItem and risk"
                ),
                "ts": _utc_now(),
            })
            return
        accepted, reason, system_message = self.governance.process_message(sender, message, round_number)
        if not accepted:
            self.dropped_messages += 1
            self._append_jsonl("messages.jsonl", {"round": round_number, "from": sender, **message, "status": "dropped", "reason": reason, "ts": _utc_now()})
            return
        delivered = {
            "runId": self.run_id, "round": round_number, "from": sender,
            **message, "deliveredAt": _utc_now(),
        }
        self.inboxes[recipient].append(delivered)
        self.delivered_messages += 1
        self._append_jsonl("messages.jsonl", {**delivered, "status": "delivered"})
        self.governance.record_decision(sender, message)
        if system_message:
            self.inboxes[system_message["to"]].append(system_message)
            self.delivered_messages += 1
            self._append_jsonl("messages.jsonl", {**system_message, "status": "delivered"})

    def _system_message(
        self, recipient: str, tag: str, content: str, round_number: int,
        candidate: Optional[str] = None, work_item: Optional[str] = None,
        risk: Optional[str] = None,
    ) -> None:
        message = {
            "runId": self.run_id, "round": round_number, "from": "orchestrator",
            "to": recipient, "tag": tag, "content": content,
            "candidate": candidate, "workItem": work_item, "risk": risk,
            "deliveredAt": _utc_now(),
        }
        self.inboxes[recipient].append(message)
        self.delivered_messages += 1
        self._append_jsonl("messages.jsonl", {**message, "status": "delivered"})

    def _blind_review(self, candidate: str, round_number: int) -> Dict[str, Any]:
        final_workspace = self.workspaces[self.topology.finalizer_id]
        session = SubscriptionSession(
            self.blind_verifier_provider, final_workspace, False,
            f"blind-verifier-{candidate}", self.run_dir, self.model, "high", fresh=True,
        )
        evidence_note = "No explicit ignored or external evidence snapshot was selected."
        if self.evidence_manifest:
            evidence_note = (
                f"Read immutable evidence only from {self.evidence_manifest['snapshot_root']} "
                f"using {self.run_dir / 'evidence-manifest.json'} as the source mapping. "
                "Do not read or modify the original evidence locations."
            )
        context_note = "No organization documentation context manifest was selected."
        if self.context_pack_manifest:
            pack = self.context_pack_manifest["agent_packs"][
                self.topology.finalizer_id
            ]
            context_note = (
                f"Use the read-only full verification context at {pack['root']} "
                f"and its index {pack['index']}. Canonical repository documentation "
                "remains authoritative."
            )
        prompt = f"""# Fresh independent final verification

Candidate: {candidate}
Working directory: {final_workspace.cwd}

You have not seen the team's messages, rationale, debugging history, or confidence claims. First verify git rev-parse HEAD exactly equals the candidate. Read the mission and task-relevant repository documentation. Exercise the integrated artifact as a skeptical user and run deterministic checks. Do not add files or fixes.

Evidence policy: {evidence_note}
Documentation context: {context_note}

Run-scoped deliverables are tracked in the candidate under `{self.artifact_staging_prefix}/`; inspect them there. RecCli exports that exact verified content to `{self.run_dir / 'deliverables'}` only after approval.

Generated/ignored output bundles, when present, are sealed under `{self.candidate_artifact_root}`. Verify each `*.manifest.json` and its exact candidate binding; these bundles are evidence, not automatically promoted product files.

Approve only when the exact candidate meets observable acceptance criteria. A plausible implementation or another agent's test claim is not evidence.

## Mission

{self.mission}

## RecCli project memory

{self.project_context}
"""
        result = session.run(prompt, BLIND_REVIEW_SCHEMA, self.turn_timeout_seconds)
        review = result["value"]
        if not isinstance(review, dict) or review.get("candidate") != candidate:
            raise ValueError("blind verifier did not review the exact candidate")
        if review.get("verdict") not in {"approved", "blocked"}:
            raise ValueError("blind verifier returned an invalid verdict")
        self._add_usage(result.get("usage", {}), self.blind_verifier_provider)
        self._append_jsonl("turns/blind-verifier.jsonl", {
            "round": round_number, "candidate": candidate,
            "provider": self.blind_verifier_provider,
            "session_id": result.get("session_id"), "usage": result.get("usage", {}),
            "review": review,
        })
        return review

    def _has_initial_worker_assignment(self, worker_id: str) -> bool:
        primary = self.topology.primary_manager_by_worker.get(worker_id)
        if not primary:
            return True
        return self._has_delegation(
            self.inboxes[worker_id],
            sender=primary,
            recipient=worker_id,
        )

    @staticmethod
    def _has_delegation(
        messages: List[Dict[str, Any]],
        *,
        sender: str,
        recipient: str,
    ) -> bool:
        return any(
            message.get("from") == sender
            and message.get("to", recipient) == recipient
            and message.get("tag") in {"plan", "handoff"}
            and bool(message.get("workItem"))
            and message.get("risk") in RISKS
            for message in messages
        )

    def _assert_delegation_barrier(self, round_number: int) -> None:
        """Fail closed when the hierarchy did not issue explicit assignments.

        The barrier does not serialize implementation. It ensures every lane
        has a bounded instruction before round three fans workers out in
        parallel. A deliberate no-code lane is represented by an explicit
        research, review, or standby work item rather than silence.
        """
        if not self.topology.delegation_gate:
            return
        missing: List[str] = []
        if round_number == 1:
            missing = [
                manager_id
                for manager_id in self.topology.manager_ids
                if not self._has_delegation(
                    self.inboxes[manager_id],
                    sender=self.topology.leader_id,
                    recipient=manager_id,
                )
            ]
            level = "lead-to-manager"
        elif round_number == 2:
            missing = [
                worker_id
                for worker_id in self.topology.worker_ids
                if not self._has_initial_worker_assignment(worker_id)
            ]
            level = "manager-to-worker"
        else:
            return
        if missing:
            raise RuntimeError(
                f"{level} delegation barrier incomplete after round "
                f"{round_number}; missing explicit assignments for: "
                f"{', '.join(missing)}"
            )

    def _select_agents(self, round_number: int) -> List[AgentSpec]:
        if self.topology.scheduler == "all":
            return list(self.topology.agents)
        if round_number == 1:
            return [self.topology.agent(self.topology.leader_id)]
        if self.topology.delegation_gate and round_number == 2:
            # The lead owns initial reconnaissance and decomposition. Managers
            # receive that map in round two, refine it against their specialist
            # context, and only then wake explicitly delegated workers in round
            # three. This preserves leadership without serializing the actual
            # implementation lanes.
            return [
                self.topology.agent(manager_id)
                for manager_id in self.topology.manager_ids
                if self.inboxes[manager_id]
            ]
        selected = [
            agent for agent in self.topology.agents
            if (
                self.inboxes[agent.agent_id]
                or (
                    self.states[agent.agent_id] == "working"
                    and agent.agent_id not in self.topology.inbox_only_ids
                )
                or agent.agent_id in self.topology.always_wake
            )
        ]
        selected = [
            agent for agent in selected
            if (
                agent.agent_id not in self.topology.worker_ids
                or agent.agent_id in self.turned
                or self._has_initial_worker_assignment(agent.agent_id)
            )
        ]
        # A worker turn is not synonymous with a sealed experiment. Workers
        # may inspect evidence, reproduce tracked tests, review interfaces, or
        # report that a route is indeterminate without consuming an artifact
        # slot. The hard experiment limit is enforced when generated outputs
        # are sealed, so it must not silently starve an explicitly assigned
        # worker before the team has even evaluated the lane.
        return selected

    def _select_closeout_agents(self) -> List[AgentSpec]:
        """Schedule only review, integration, and release traffic after cap.

        The configured round count remains the exploration/work budget.
        Closeout cannot wake implementation workers or initiate another
        experiment; it only gives already-produced candidates enough message
        boundaries to complete adversarial review and integration.
        """
        return [
            agent for agent in self.topology.agents
            if (
                agent.agent_id not in self.topology.worker_ids
                and bool(self.inboxes[agent.agent_id])
            )
        ]

    def _add_usage(self, usage: Dict[str, Any], provider: Optional[str] = None) -> None:
        for key in self.usage:
            value = int(usage.get(key, 0) or 0)
            self.usage[key] += value
            if provider and provider in self.usage_by_provider:
                self.usage_by_provider[provider][key] += value

    def _event(self, event_type: str, round_number: int, **details: Any) -> None:
        self._append_jsonl("events.jsonl", {"type": event_type, "round": round_number, "ts": _utc_now(), **details})

    def _status(
        self,
        status: str,
        round_number: int,
        detail: str,
        result: Optional[Dict[str, Any]] = None,
        scheduled_turns: Optional[int] = None,
        phase: Optional[str] = None,
    ) -> None:
        resolved_phase = phase or "parallel_execution"
        if phase is None and self.topology.delegation_gate:
            if round_number <= 1:
                resolved_phase = "lead_recon"
            elif round_number == 2:
                resolved_phase = "manager_delegation"
        payload = {
            "run_id": self.run_id, "status": status, "round": round_number,
            "max_rounds": self.max_rounds,
            "max_closeout_rounds": self.max_closeout_rounds,
            "rounds_remaining": max(0, self.max_rounds - round_number),
            "closeout_round": max(0, round_number - self.max_rounds),
            "closeout_rounds_remaining": max(
                0,
                self.max_rounds + self.max_closeout_rounds - round_number,
            ) if round_number > self.max_rounds else 0,
            "scheduled_turns": scheduled_turns,
            "completed_turns": self.completed_turns,
            "attempted_turns": self.attempted_turns,
            "failed_turns": self.failed_turns,
            "detail": detail, "provider": self.provider,
            "host_provider": self.host_provider,
            "provider_assignments": self.provider_by_agent,
            "blind_verifier_provider": self.blind_verifier_provider,
            "topology": self.topology.topology_id, "updated_at": _utc_now(),
            "pid": os.getpid(), "run_dir": str(self.run_dir),
            "agent_states": self.states,
            "usage": self.usage,
            "usage_by_provider": self.usage_by_provider,
            "delivered_messages": self.delivered_messages,
            "dropped_messages": self.dropped_messages,
            "paused": self.paused,
            "phase": resolved_phase,
            "control_protocol": self.control_protocol,
            "human_promotion_required": self.topology.human_promotion_required,
            "evidence_snapshot_root": self.evidence_manifest.get("snapshot_root") if self.evidence_manifest else None,
            "evidence_verified_at": self.evidence_verified_at,
            "context_pack_manifest": str(self.run_dir / "context-pack-manifest.json") if self.context_pack_manifest else None,
            "context_verified_at": self.context_verified_at,
            "candidate_artifact_root": str(self.candidate_artifact_root),
            "candidate_artifact_bundles": len(self.candidate_artifact_manifests),
            "max_experiments": self.max_experiments,
            "experiments_remaining": max(
                0, self.max_experiments - len(self.candidate_artifact_manifests),
            ),
        }
        if result is not None:
            payload["result"] = result
        self._write_json("status.json", payload)

    def _write_json(self, relative: str, value: Any) -> None:
        path = self.run_dir / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        temp_path = path.with_suffix(path.suffix + ".tmp")
        temp_path.write_text(json.dumps(value, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        temp_path.replace(path)

    def _append_jsonl(self, relative: str, value: Any) -> None:
        path = self.run_dir / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        with self._trace_lock:
            with path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(value, ensure_ascii=False) + "\n")


def build_project_context(project_root: Path, max_chars: int = 30_000) -> str:
    manager = DevProjectManager(project_root)
    document = manager.load_or_create()
    project = document.get("project", {})
    lines = [
        f"Project: {project.get('name', project_root.name)}",
        str(project.get("description", "")),
        "Features:",
    ]
    for feature in document.get("features", []):
        files = ", ".join(feature.get("files_touched", [])[:12])
        docs = ", ".join(
            doc.get("path", "") for doc in feature.get("docs", [])[:5]
            if isinstance(doc, dict)
        )
        lines.append(
            f"- {feature.get('feature_id')}: {feature.get('title')} [{feature.get('status', 'unknown')}]\n"
            f"  {feature.get('description', '')}\n  files: {files or 'not mapped'}\n  docs: {docs or 'not mapped'}"
        )
    context = "\n".join(lines)
    return context[:max_chars]


def _provider_authentication_status(provider: str) -> str:
    """Check native CLI subscription auth without retaining account output."""
    executable = shutil.which(provider)
    if executable is None:
        return "missing"
    commands = {
        "claude": [executable, "auth", "status"],
        "codex": [executable, "login", "status"],
    }
    try:
        proc = subprocess.run(
            commands[provider], capture_output=True, text=True,
            timeout=10, check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        # Older native CLIs may not expose a status command. The real agent
        # invocation remains the authority in that case.
        return "unknown"
    return "authenticated" if proc.returncode == 0 else "not_authenticated"


def _detect_host_provider(available: List[str]) -> str:
    override = (os.environ.get("RECCLI_HOST") or "").strip().lower()
    if override in available:
        return override
    if (
        os.environ.get("CLAUDECODE") or os.environ.get("CLAUDE_CODE_SESSION_ID")
    ) and "claude" in available:
        return "claude"
    if (
        os.environ.get("CODEX_SESSION_ID") or os.environ.get("CODEX_HOME")
    ) and "codex" in available:
        return "codex"
    try:
        pid = os.getpid()
        for _ in range(10):
            parent = subprocess.run(
                ["ps", "-o", "ppid=,command=", "-p", str(pid)],
                capture_output=True, text=True, timeout=2, check=False,
            )
            text = (parent.stdout or "").strip().lower()
            if not text:
                break
            pieces = text.split(None, 1)
            if len(pieces) != 2:
                break
            pid = int(pieces[0])
            command = pieces[1]
            if "codex" in command and "codex" in available:
                return "codex"
            if "claude" in command and "claude" in available:
                return "claude"
            if pid <= 1:
                break
    except (OSError, subprocess.TimeoutExpired, ValueError):
        pass
    return "claude" if "claude" in available else available[0]


def build_provider_assignments(
    topology: Topology, host_provider: str, secondary_provider: Optional[str] = None,
) -> Dict[str, str]:
    """Assign providers by lane so routine context and cross-review both work."""
    if secondary_provider is None or secondary_provider == host_provider:
        return {agent.agent_id: host_provider for agent in topology.agents}
    assignments: Dict[str, str] = {}
    for agent in topology.agents:
        agent_id = agent.agent_id
        if agent_id in {
            topology.leader_id, topology.finalizer_id, topology.release_manager_id,
        }:
            assignments[agent_id] = host_provider
            continue
        lane = re.fullmatch(r"(?:manager|worker)-([a-z])", agent_id)
        if lane:
            # Paired worker/primary-manager lanes share a provider. Alternating
            # lanes ensure every default lane has an opposite-provider manager
            # available for independent review.
            lane_index = ord(lane.group(1)) - ord("a")
            assignments[agent_id] = (
                secondary_provider if lane_index % 2 == 0 else host_provider
            )
            continue
        assignments[agent_id] = (
            secondary_provider
            if _stable_index(agent_id, 2) == 0
            else host_provider
        )
    for worker, primary in topology.primary_manager_by_worker.items():
        if primary in assignments and worker in assignments:
            assignments[worker] = assignments[primary]
    return assignments


def resolve_provider_plan(provider: str, topology: Topology) -> ProviderPlan:
    normalized = (provider or "auto").strip().lower()
    if normalized not in {"auto", "mixed", "claude", "codex"}:
        raise ValueError("provider must be auto, mixed, claude, or codex")

    installed = [name for name in ("claude", "codex") if shutil.which(name)]
    if not installed:
        raise RuntimeError("Neither claude nor codex CLI was found on PATH")
    auth_targets = (
        installed if normalized in {"auto", "mixed"}
        else [normalized] if normalized in installed
        else []
    )
    authentication = {name: "not_checked" for name in installed}
    authentication.update({
        name: _provider_authentication_status(name) for name in auth_targets
    })
    usable = [
        name for name in installed
        if authentication[name] != "not_authenticated"
    ]

    if normalized in {"claude", "codex"}:
        if normalized not in installed:
            raise RuntimeError(f"{normalized} CLI not found on PATH")
        if authentication[normalized] == "not_authenticated":
            raise RuntimeError(f"{normalized} CLI is installed but not authenticated")
        mode = normalized
        active = [normalized]
    elif normalized == "mixed":
        if set(usable) != {"claude", "codex"}:
            raise RuntimeError(
                "mixed provider runs require authenticated claude and codex CLIs"
            )
        mode = "mixed"
        active = ["claude", "codex"]
    else:
        if not usable:
            raise RuntimeError("No installed native provider has usable authentication")
        mode = "mixed" if set(usable) == {"claude", "codex"} else usable[0]
        active = ["claude", "codex"] if mode == "mixed" else [mode]

    host = _detect_host_provider(active)
    secondary = next((name for name in active if name != host), None)
    assignments = build_provider_assignments(topology, host, secondary)
    return ProviderPlan(
        mode=mode,
        requested=normalized,
        host_provider=host,
        available_providers=active,
        provider_assignments=assignments,
        blind_verifier_provider=secondary or host,
        authentication=authentication,
    )


def resolve_provider(provider: str) -> str:
    """Backward-compatible mode-only provider resolution."""
    return resolve_provider_plan(provider, get_topology()).mode


def organization_root(project_root: Path) -> Path:
    return project_root / "devsession" / "agent-organizations"


def create_run_request(
    working_directory: str,
    mission: str,
    provider: str = "auto",
    topology: str = "google-rotating",
    max_rounds: int = 8,
    max_concurrency: int = 5,
    turn_timeout_seconds: int = 1200,
    model: str = "auto",
    evidence_paths: Optional[List[str]] = None,
    protected_paths: Optional[List[str]] = None,
    context_manifest: Optional[str] = None,
    max_experiments: int = 3,
) -> Dict[str, Any]:
    project_root = discover_project_root(Path(working_directory).expanduser().resolve())
    if project_root is None:
        raise FileNotFoundError(f"No RecCli/Git project found from {working_directory}")
    if not mission or not mission.strip():
        raise ValueError("mission must not be empty")
    _validate_clean_repository(project_root)
    resolved_evidence = resolve_evidence_paths(project_root, evidence_paths)
    resolved_protected = resolve_protected_paths(project_root, protected_paths)
    resolved_context = resolve_context_manifest(project_root, context_manifest)
    topology_config = get_topology(topology)
    provider_plan = resolve_provider_plan(provider, topology_config)
    run_id = f"{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}_org_{_safe_name(topology)}_{uuid.uuid4().hex[:6]}"
    run_dir = organization_root(project_root) / run_id
    normalized_model = None if (model or "auto").strip().lower() in {"", "auto", "none", "default"} else model.strip()
    if provider_plan.mode == "mixed" and normalized_model:
        raise ValueError(
            "mixed provider runs require model='auto' so each native CLI uses its own configured model"
        )
    run_dir.mkdir(parents=True, exist_ok=False)
    request = {
        "run_id": run_id, "run_dir": str(run_dir),
        "project_root": str(project_root), "mission": mission.strip(),
        "provider": provider_plan.mode, "provider_requested": provider,
        "host_provider": provider_plan.host_provider,
        "available_providers": provider_plan.available_providers,
        "provider_assignments": provider_plan.provider_assignments,
        "blind_verifier_provider": provider_plan.blind_verifier_provider,
        "provider_authentication": provider_plan.authentication,
        "topology": topology, "max_rounds": max(1, int(max_rounds)),
        "scheduler": topology_config.scheduler,
        "delegation_gate": topology_config.delegation_gate,
        "human_promotion_required": topology_config.human_promotion_required,
        "max_concurrency": max(1, int(max_concurrency)),
        "turn_timeout_seconds": max(30, int(turn_timeout_seconds)),
        "model": normalized_model, "created_at": _utc_now(),
        "evidence_paths": [str(path) for path in resolved_evidence],
        "protected_paths": resolved_protected,
        "context_manifest": (
            resolved_context.relative_to(project_root).as_posix()
            if resolved_context else None
        ),
        "max_experiments": max(0, int(max_experiments)),
        "control_protocol": "reccli.organization-control.v1",
    }
    (run_dir / "request.json").write_text(json.dumps(request, indent=2) + "\n", encoding="utf-8")
    (run_dir / "status.json").write_text(json.dumps({
        "run_id": run_id, "status": "starting", "round": 0,
        "max_rounds": max(1, int(max_rounds)),
        "rounds_remaining": max(1, int(max_rounds)),
        "scheduled_turns": 0, "completed_turns": 0, "attempted_turns": 0,
        "detail": "Background organization worker is starting",
        "provider": provider_plan.mode,
        "host_provider": provider_plan.host_provider,
        "provider_assignments": provider_plan.provider_assignments,
        "blind_verifier_provider": provider_plan.blind_verifier_provider,
        "topology": topology,
        "human_promotion_required": topology_config.human_promotion_required,
        "evidence_paths": [str(path) for path in resolved_evidence],
        "protected_paths": resolved_protected,
        "context_manifest": (
            resolved_context.relative_to(project_root).as_posix()
            if resolved_context else None
        ),
        "max_experiments": max(0, int(max_experiments)),
        "control_protocol": "reccli.organization-control.v1",
        "phase": "lead_recon" if topology_config.delegation_gate else "parallel_execution",
        "agent_states": {
            agent.agent_id: "idle" for agent in topology_config.agents
        },
        "updated_at": _utc_now(), "run_dir": str(run_dir),
    }, indent=2) + "\n", encoding="utf-8")
    return request


def run_request(request: Dict[str, Any]) -> Dict[str, Any]:
    runner = OrganizationRunner(
        project_root=Path(request["project_root"]), mission=request["mission"],
        provider=request["provider"], topology_name=request["topology"],
        run_id=request["run_id"], run_dir=Path(request["run_dir"]),
        max_rounds=request.get("max_rounds", 8),
        max_concurrency=request.get("max_concurrency", 5),
        turn_timeout_seconds=request.get("turn_timeout_seconds", 1200),
        model=request.get("model"),
        provider_assignments=request.get("provider_assignments"),
        host_provider=request.get("host_provider"),
        blind_verifier_provider=request.get("blind_verifier_provider"),
        evidence_paths=request.get("evidence_paths"),
        protected_paths=request.get("protected_paths"),
        context_manifest=request.get("context_manifest"),
        max_experiments=request.get("max_experiments", 3),
    )
    return runner.run()


def find_run(working_directory: str, run_id: str) -> Optional[Path]:
    candidate = Path(run_id).expanduser()
    if candidate.is_absolute() and candidate.is_dir():
        return candidate.resolve()
    project_root = discover_project_root(Path(working_directory).expanduser().resolve())
    if project_root is None:
        return None
    direct = organization_root(project_root) / _safe_name(run_id)
    if direct.is_dir():
        return direct
    matches = sorted(organization_root(project_root).glob(f"*{_safe_name(run_id)}*"))
    return matches[-1] if matches else None


def _stable_index(value: str, size: int) -> int:
    if size <= 0:
        return 0
    return int.from_bytes(hashlib.sha256(value.encode()).digest()[:4], "big") % size


def _safe_name(value: str) -> str:
    result = re.sub(r"[^A-Za-z0-9._-]+", "-", str(value).strip()).strip("-")
    if not result:
        raise ValueError(f"cannot create safe name from {value!r}")
    return result


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _git(cwd: Path, args: List[str]) -> str:
    proc = subprocess.run(
        ["git", *args], cwd=cwd, capture_output=True, text=True, check=False,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)} failed: {(proc.stderr or proc.stdout).strip()}")
    return proc.stdout


def _validate_clean_repository(project_root: Path) -> None:
    """Fail before launch unless candidates can share one unambiguous Git base."""
    top_level = Path(_git(project_root, ["rev-parse", "--show-toplevel"]).strip()).resolve()
    if top_level != project_root.resolve():
        raise RuntimeError(
            f"organization project root must be the Git root ({top_level}), got {project_root.resolve()}"
        )
    tracked_status = _git(
        project_root, ["status", "--porcelain", "--untracked-files=no"],
    )
    if tracked_status.strip():
        raise RuntimeError(
            "organization runs require a clean tracked Git worktree; commit or stash current changes"
        )
