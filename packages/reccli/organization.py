"""RecCli-native multi-agent organization runner.

The runner intentionally dispatches the installed Claude Code and Codex CLIs.
It does not use model API keys.  Each organization member owns a resumable
provider session and an isolated Git worktree.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import platform
import queue
import re
import shlex
import shutil
import stat
import subprocess
import sys
import tempfile
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any, Dict, List, Optional, Set, Tuple

from .organization_admission import render_admission_prompt, validate_admission
from .organization_outcomes import record_outcome_event
from .project.devproject import discover_project_root


MESSAGE_TAGS = {
    "plan", "question", "answer", "handoff", "review",
    "decision", "status", "blocker", "flag",
}
DELEGATION_TAGS = {"plan", "handoff", "review"}
RISKS = {"routine", "high", "release"}
STATES = {"working", "idle", "blocked", "done"}
DISPOSITIONS = {"continue", "promote", "no_promotion", "pending_human", "no_op"}
WORKER_GOAL_TERMINAL_STATES = {
    "candidate_ready", "completed", "superseded", "cancelled", "unevaluable",
}
GOAL_CLASSES = {"production_pipeline", "evaluator_infrastructure"}
PREDICATE_SOURCES = {"commands_pass", "hard_gate", "metric"}
COMPARISON_RULES = {"false_to_true", "maximize", "minimize"}
ARTIFACT_STAGING_ROOT = ".reccli-org-artifacts"
CONTEXT_PACK_SCHEMA = "reccli.organization-context-packs.v1"
HOST_CANDIDATE = "RECCLI_HOST_CANDIDATE"
DEFAULT_CLOSEOUT_ROUNDS = 4
ACTIVITY_SCHEMA = "reccli.organization-activity.v1"
GATE_PROPOSAL_SCHEMA = "reccli.organization-gate-proposal.v1"
HOST_STATE_SCHEMA = "reccli.organization-host-state.v1"
GOAL_STATE_SCHEMA = "reccli.organization-goals.v1"
EXPERIMENT_RECORD_SCHEMA = "reccli.organization-experiment-record.v1"
EXPERIMENT_POLICY_SCHEMA = "reccli.organization-experiment-policy.v1"
EXPERIMENT_CONTRACT_SCHEMA = "reccli.organization-experiment-contract.v1"
EXPERIMENT_TRIAL_SCHEMA = "reccli.organization-experiment-trial.v1"
PROJECT_EXPERIMENT_RESULT_SCHEMA = "reccli.project-experiment-result.v1"
REPORT_ONLY_SUFFIXES = frozenset({".md", ".txt", ".rst", ".adoc"})
EXPERIMENT_VERDICTS = frozenset({
    "baseline",
    "keep",
    "discard",
    "inconclusive",
    "crash",
})
EXPERIMENT_PATH_COMPONENTS = frozenset({
    "benchmark", "benchmarks", "data", "fixture", "fixtures",
    "measurement", "measurements", "output", "outputs", "probe", "probes",
    "result", "results",
})
_ACTIVITY_WRITE_LOCK = threading.Lock()
_SECRET_ASSIGNMENT_RE = re.compile(
    r"(?i)\b([A-Z0-9_]*(?:TOKEN|SECRET|PASSWORD|API_KEY|ACCESS_KEY)[A-Z0-9_]*)"
    r"=([^\s;&|]+)",
)


def verify_experiment_trial_records(
    records: List[Dict[str, Any]],
) -> Tuple[bool, Optional[str], Optional[str]]:
    """Verify the append-only SHA-256 chain for compact trial records."""
    previous: Optional[str] = None
    for index, record in enumerate(records):
        if not isinstance(record, dict):
            return False, previous, f"trial record {index} is not an object"
        claimed = record.get("record_sha256")
        if not isinstance(claimed, str) or not re.fullmatch(
            r"[0-9a-f]{64}", claimed,
        ):
            return (
                False,
                previous,
                f"trial record {index} has no valid record_sha256",
            )
        if record.get("previous_record_sha256") != previous:
            return (
                False,
                previous,
                f"trial record {index} does not extend the prior record",
            )
        payload = dict(record)
        payload.pop("record_sha256", None)
        canonical = json.dumps(
            payload,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        ).encode("utf-8")
        actual = hashlib.sha256(canonical).hexdigest()
        if actual != claimed:
            return (
                False,
                previous,
                f"trial record {index} SHA-256 mismatch",
            )
        previous = claimed
    return True, previous, None


_URL_TOKEN_RE = re.compile(r"(?i)([?&](?:token|key|secret|password)=)[^&\s]+")

# One bounded label an auditor may politely prefix a disposition with. The
# July forensics' first finding was governance parsed by string prefix; the
# thirteenth-hour blocker was a NO_VETO rejected for starting with "FORMAL
# DISPOSITION: ". One shared parser, one strip, startswith semantics kept so
# "NOT NO_VETO" can never pass.
_DISPOSITION_LABEL_RE = re.compile(
    r"^(?:FORMAL\s+)?(?:DISPOSITION|DECISION|REVIEW|VERDICT)\s*:\s*",
    re.IGNORECASE,
)
_DISPOSITION_MARKERS = ("NO_VETO", "REVIEWED", "APPROVED", "BLOCKED", "VETO")


def disposition_marker(content: Any) -> Optional[str]:
    """Parse one disposition marker from message content, or None.

    Strips at most one bounded label prefix, then requires the marker at the
    start. Every consumer of disposition semantics (live normalization, the
    governance ledger, record derivation) must use this parser so the record
    and its readers can never disagree about what an approval looks like.
    """
    text = str(content or "").lstrip()
    text = _DISPOSITION_LABEL_RE.sub("", text, count=1).lstrip()
    upper = text.upper()
    for marker in _DISPOSITION_MARKERS:
        if upper.startswith(marker):
            return marker
    return None

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
                    "goalClass": {
                        "anyOf": [
                            {"type": "string", "enum": sorted(GOAL_CLASSES)},
                            {"type": "null"},
                        ],
                    },
                    "predicateId": {
                        "anyOf": [
                            {"type": "string", "minLength": 1},
                            {"type": "null"},
                        ],
                    },
                    "evaluatorId": {
                        "anyOf": [
                            {"type": "string", "minLength": 1},
                            {"type": "null"},
                        ],
                    },
                },
                # OpenAI strict schema mode requires EVERY declared property
                # in `required`; optionality is expressed only by the
                # anyOf-with-null on the field itself. Omitting the three goal
                # fields made Codex reject the response_format before the
                # model ran, which killed every codex lane in the first live
                # flat run. Claude tolerated the omission, hiding it.
                "required": [
                    "to", "tag", "content", "candidate", "workItem", "risk",
                    "goalClass", "predicateId", "evaluatorId",
                ],
                "additionalProperties": False,
            },
        },
        "summary": {"type": "string", "minLength": 1},
        "state": {"type": "string", "enum": sorted(STATES)},
        "artifacts": {"type": "array", "items": {"type": "string"}},
        "candidate": {"anyOf": [{"type": "string", "minLength": 1}, {"type": "null"}]},
        "risk": {"anyOf": [{"type": "string", "enum": sorted(RISKS)}, {"type": "null"}]},
        "disposition": {"type": "string", "enum": sorted(DISPOSITIONS)},
        "final": {"type": "boolean"},
    },
    "required": [
        "messages", "summary", "state", "artifacts", "candidate", "risk",
        "disposition", "final",
    ],
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

RUN_CONCLUSION_SCHEMA: Dict[str, Any] = {
    "type": "object",
    "properties": {
        "summary": {"type": "string", "minLength": 1},
        "accomplishments": {
            "type": "array", "items": {"type": "string", "minLength": 1},
        },
        "conclusive_findings": {
            "type": "array", "items": {"type": "string", "minLength": 1},
        },
        "evidence_and_tests": {
            "type": "array", "items": {"type": "string", "minLength": 1},
        },
        "scientific_or_product_blockers": {
            "type": "array", "items": {"type": "string", "minLength": 1},
        },
        "infrastructure_failures": {
            "type": "array", "items": {"type": "string", "minLength": 1},
        },
        "unresolved": {
            "type": "array", "items": {"type": "string", "minLength": 1},
        },
        "promotion_readiness": {
            "type": "string",
            "enum": [
                "ready_for_human_review",
                "awaiting_human_approval",
                "verified",
                "not_ready",
                "no_candidate",
                "cancelled",
            ],
        },
        "next_action": {"type": "string", "minLength": 1},
        "limitations": {
            "type": "array", "items": {"type": "string", "minLength": 1},
        },
        # The compounding seam: a terminal lead may propose the successor
        # run's admission contract (consumer, work_class, done_condition,
        # stop_conditions). The host validates it like any admission and the
        # continuation uses it in place of carrying the parent's verbatim,
        # so an autonomous chain can re-scope each link. Null when the parent
        # contract should simply carry.
        "proposed_successor_admission": {
            "anyOf": [{"type": "object"}, {"type": "null"}],
        },
    },
    "required": [
        "summary",
        "accomplishments",
        "conclusive_findings",
        "evidence_and_tests",
        "scientific_or_product_blockers",
        "infrastructure_failures",
        "unresolved",
        "promotion_readiness",
        "next_action",
        "limitations",
        "proposed_successor_admission",
    ],
    "additionalProperties": False,
}

DEFAULT_CLAUDE_ALLOWED_TOOLS = [
    "Read", "Glob", "Grep",
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
    "Bash(python3 -m pytest*)", "Bash(.venv/bin/python -m pytest*)",
    "Bash(.venv/bin/python scripts/* --check*)",
    "Bash(python scripts/* --check*)", "Bash(python3 scripts/* --check*)",
    "Bash(make test*)",
]

CLAUDE_WEB_RESEARCH_TOOLS = ["WebSearch", "WebFetch"]


@dataclass(frozen=True)
class AgentSpec:
    agent_id: str
    role: str
    instructions: str
    writable: bool = True
    reasoning: str = "medium"
    write_scope: str = "workspace"
    web_research: bool = False
    fresh_session: bool = False

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
    required_approvers: Set[str] = field(default_factory=set)
    manager_ids: List[str] = field(default_factory=list)
    worker_ids: List[str] = field(default_factory=list)
    primary_manager_by_worker: Dict[str, str] = field(default_factory=dict)
    release_manager_id: Optional[str] = None
    alternate_reviewer_pool: List[str] = field(default_factory=list)
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
    environment: Dict[str, str] = field(default_factory=dict)


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


def _flat_topology() -> Topology:
    """One coordinator, many workers, independent audit lanes, no managers.

    Built as its own structure rather than as a hierarchical topology with empty
    manager lists, because the manager layer is not merely unused here: there is
    no delegation barrier to satisfy, no primary-manager ownership to route
    through, and review is an independent lane rather than a rung.

    The case for it is measured. Across a recorded twelve-round hierarchical run,
    management took 50 of 64 turns (78%) while the four workers took 14 between
    them, one of them a single turn for the entire run. Managers exist in human
    organizations to allocate scarce attention and to surface privately-held
    context; neither applies to agents that cannot share memory, and every layer
    boundary becomes another serialized document instead of work.

    Auditors can veto but cannot promote, matching the existing human-authority
    boundary: agents never grant themselves the final word.
    """
    worker_ids = [f"worker-{letter}" for letter in "abcdef"]
    auditor_ids = ["auditor-a", "auditor-b"]

    routes: Dict[Tuple[str, str], Optional[Set[str]]] = {}
    for worker in worker_ids:
        _route(routes, "lead", worker)
    for auditor in auditor_ids:
        _route(routes, "lead", auditor)
        # Auditors read candidates directly from the worker that produced them,
        # so a review does not have to be relayed through the coordinator.
        for worker in worker_ids:
            _route(routes, auditor, worker)

    agents = [
        AgentSpec(
            "lead", "coordinator",
            "Give every worker a falsifiable question it can answer by executing "
            "something: a test, the project CLI, a script. Assign directly; there "
            "is no management layer to route through. Consume each result as it "
            "lands and re-task that worker immediately. Do not write "
            "implementation yourself, do not relay work between workers, and do "
            "not authorize canonical promotion. RecCli integrates reviewed "
            "candidates into your worktree; never run Git yourself.",
            # Integration scope, not read-only: the host cherry-picks reviewed
            # candidates into the lead's integration worktree, and every commit
            # there must carry an approved patch-id. A read-only lead made its
            # own workspace fail write-scope validation the moment the host
            # integrated anything.
            True, "high", "integration", True,
        ),
        *[
            AgentSpec(
                worker, "worker",
                "Answer the one falsifiable question you were given by running "
                "something that can disprove it: the test suite, the project CLI, "
                "a focused script. Work directly on source, tests, evaluator, or "
                "product paths in your disposable worktree. Report the observed "
                "result, including a negative one, and hand any exact candidate "
                "back to the coordinator. A summary of what you read is not a "
                "result. Flag contradictions without expanding scope.",
                True, "high", "workspace",
            ) for worker in worker_ids
        ],
        *[
            AgentSpec(
                auditor, "independent auditor",
                "Attempt to refute an exact candidate against the repository and "
                "its tests. Report what you executed and what it showed. Veto with "
                "a reproducible reason or annotate; never integrate or promote, "
                "and never treat the absence of a veto as evidence of "
                "correctness.",
                False, "high", "none", True,
            ) for auditor in auditor_ids
        ],
    ]

    return Topology(
        "flat", "Flat Fleet",
        "One coordinator assigning falsifiable questions directly to workers, "
        "with independent audit lanes and no management layer.",
        "Every agent executes and reports observable results. Review is an "
        "independent lane, not a rung. Nothing is relayed that could be run.",
        agents, routes, "lead", "lead", {"lead"},
        scheduler="event", always_wake=set(),
        inbox_only_ids={"lead"},
        # The lead is also the finalizer here, and an agent cannot send a
        # decision to itself, so naming it a required approver deadlocks every
        # finalization. Authority still exists: human_promotion_required below
        # keeps the final word with a person, which is the point of the boundary.
        required_approvers=set(),
        manager_ids=[], worker_ids=worker_ids,
        primary_manager_by_worker={},
        release_manager_id=None,
        final_reviewer_pool=auditor_ids,
        blind_final_review=True,
        # Auditors refute; they never approve. "No veto" is not a finding of
        # correctness, which is why review_policy is veto rather than approval.
        review_policy="veto",
        human_promotion_required=True,
    )


def _supervisor_ids(topology: "Topology") -> Set[str]:
    """Every agent that supervises at least one worker.

    Hierarchical topologies answer with their primary managers. A flat topology
    has none, and an empty set silently disabled the things gated on it: no agent
    could author an experiment contract, and the host-state and experiment
    prompts were withheld from the only agent that coordinates.
    """
    supervisors = set(topology.primary_manager_by_worker.values())
    return supervisors or {topology.leader_id}


def _supervisor_of(topology: "Topology", worker_id: str) -> str:
    """Who a worker hands its candidate to, and who may author its experiments.

    Hierarchical topologies route through the worker's primary manager. A flat
    topology has no manager layer, so the coordinator plays that part directly.
    Returning the leader rather than None matters: the handoff validator compares
    the message recipient against this value, and with None it rejected every
    worker handoff in a flat run, while the experiment-contract check made it
    impossible for any agent to author a contract at all.
    """
    return topology.primary_manager_by_worker.get(worker_id) or topology.leader_id


LEGACY_TOPOLOGY_ALIASES = frozenset({"google-rotating", "google", "scientific"})


def get_topology(name: str = "flat") -> Topology:
    """Resolve the organization structure. Flat is the only structure.

    The hierarchical topologies (google, google-rotating, scientific) were
    deleted after the recorded runs measured what they bought: management took
    50 of 64 turns (78%) in the surviving hierarchical run, six of nine runs
    re-adjudicated one candidate, and 29 of 32 materialized candidates were
    prose reports. The manager layer existed to allocate scarce attention and
    surface privately-held context; neither applies to agents, and every layer
    boundary became another serialized document instead of work.

    Legacy names alias to flat so existing project contracts, continuation
    records, and replay paths keep launching instead of bricking. Callers
    record the requested name next to the resolved one, so a downgraded launch
    is visible in the durable record rather than silent.
    """
    normalized = (name or "flat").strip().lower()
    if normalized != "flat" and normalized not in LEGACY_TOPOLOGY_ALIASES:
        raise ValueError(
            "topology must be flat (legacy names google, google-rotating, "
            "and scientific alias to flat)"
        )
    return _flat_topology()


class Governance:
    def _supervisor_for(self, worker_id: str) -> str:
        """See _supervisor_of; shared so the two classes cannot drift."""
        return _supervisor_of(self.topology, worker_id)

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
        if message.get("tag") != "handoff":
            return True, "", None
        # A flat topology has no managers but does have independent auditors.
        # Gating this on manager_ids alone skipped candidate/workItem/risk
        # validation entirely there, and left the auditors with nothing routed
        # to them, which is the one job they exist to do.
        if not (self.topology.manager_ids or self.topology.final_reviewer_pool):
            return True, "", None

        if sender in self.topology.worker_ids:
            primary = self._supervisor_for(sender)
            if message.get("to") != primary:
                return False, f"worker handoff must go to {primary}", None
            if not message.get("candidate") or not message.get("workItem") or not message.get("risk"):
                return False, "worker handoff requires candidate, workItem, and risk metadata", None
            candidate = message["candidate"]
            if candidate in self.assignments:
                return True, "", None
            if self.topology.review_policy == "veto":
                preferred = [
                    manager for manager in (
                        self.topology.alternate_reviewer_pool
                        or self.topology.final_reviewer_pool
                    )
                    if manager not in {
                        primary,
                        self.topology.release_manager_id,
                        self.release_reviewer_id,
                    }
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
            if not eligible:
                # Nobody independent is available to review. Accept the handoff
                # rather than raising, and say so, instead of dividing by zero.
                return True, "no independent reviewer is available", None
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
        marker = disposition_marker(message.get("content"))
        candidate = message["candidate"]
        assignment = self.assignments.get(candidate)
        if assignment and sender == assignment["reviewerId"]:
            if self.topology.review_policy == "veto" and marker in {"NO_VETO", "REVIEWED", "APPROVED"}:
                assignment["status"] = "reviewed"
                assignment["decision"] = message.get("content")
            elif self.topology.review_policy != "veto" and marker == "APPROVED":
                assignment["status"] = "approved"
                assignment["decision"] = message.get("content")
            elif marker in {"BLOCKED", "VETO"}:
                assignment["status"] = "vetoed" if self.topology.review_policy == "veto" else "blocked"
                assignment["decision"] = message.get("content")
        # The final ledger counts the whole reviewer pool, not one hash-picked
        # member. Counting only the release reviewer while assignment routing
        # deliberately targets the OTHER auditor aimed the two mechanisms at
        # different agents: run thirteen held three durable exact-candidate
        # NO_VETOs and an empty approvals ledger, and the finalizer demanded
        # an approval the system already possessed until the round limit.
        # The same narrowness cut the safety direction: a non-release
        # auditor's veto did not register as blocking.
        eligible_final_voice = (
            sender in self.required_final_approvers()
            or (
                self.topology.review_policy == "veto"
                and sender in self.topology.final_reviewer_pool
            )
        )
        if message.get("to") == self.topology.finalizer_id and eligible_final_voice:
            if marker in {"BLOCKED", "VETO"}:
                self.candidate_vetoes[sender] = candidate
                self.candidate_approvals.pop(sender, None)
            elif (
                self.topology.review_policy == "veto"
                and marker in {"NO_VETO", "REVIEWED", "APPROVED"}
            ) or marker == "APPROVED":
                self.candidate_approvals[sender] = candidate
                self.candidate_vetoes.pop(sender, None)

    def missing_final_approvers(self, candidate: str) -> List[str]:
        if self.topology.review_policy == "veto" and self.topology.final_reviewer_pool:
            # Veto policy: any pool member's standing veto on this exact
            # candidate blocks; explicit required approvers must each clear
            # it; beyond those, one pool member's exact-candidate NO_VETO
            # satisfies (both auditors are independently veto-empowered, so
            # privileging the hash-picked one adds no safety and starves the
            # finalizer when routing chose the other).
            if any(
                vetoed == candidate for vetoed in self.candidate_vetoes.values()
            ):
                return sorted(self.required_final_approvers())
            missing_required = sorted(
                approver for approver in self.topology.required_approvers
                if self.candidate_approvals.get(approver) != candidate
            )
            if missing_required:
                return missing_required
            pool_cleared = any(
                self.candidate_approvals.get(member) == candidate
                for member in self.topology.final_reviewer_pool
            )
            if pool_cleared:
                return []
            return sorted(self.required_final_approvers())
        return sorted(
            approver for approver in self.required_final_approvers()
            if self.candidate_approvals.get(approver) != candidate
        )

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
        web_research: bool = False,
    ):
        self.provider = provider
        self.workspace = workspace
        self.writable = writable
        self.session_key = session_key
        self.run_dir = run_dir
        self.model = model
        self.reasoning = reasoning
        self.fresh = fresh
        self.web_research = web_research
        self.session_id: Optional[str] = None
        self.turn = 0

    def run(self, prompt: str, schema: Dict[str, Any], timeout_seconds: int) -> Dict[str, Any]:
        self.turn += 1
        self._record_activity(
            "turn",
            f"Starting {self.provider.title()} turn {self.turn}",
            status="started",
        )
        try:
            if self.provider == "claude":
                result = self._run_claude(prompt, schema, timeout_seconds)
            elif self.provider == "codex":
                result = self._run_codex(prompt, schema, timeout_seconds)
            else:
                raise ValueError(
                    f"Unsupported subscription provider: {self.provider}",
                )
        except Exception as exc:
            self._record_activity(
                "turn",
                f"{self.provider.title()} turn {self.turn} failed: "
                f"{type(exc).__name__}",
                status="failed",
            )
            raise
        self._record_activity(
            "turn",
            f"Completed {self.provider.title()} turn {self.turn}",
            status="completed",
        )
        return result

    def record_reply_disposition(self, reply: Dict[str, Any]) -> None:
        """Record a safe operator-facing disposition from the validated reply."""
        state = str(reply.get("state") or "")
        messages = reply.get("messages") or []
        waiting_on = sorted({
            str(message.get("to"))
            for message in messages
            if isinstance(message, dict)
            and message.get("to")
            and message.get("tag") in {"question", "review", "handoff", "blocker"}
        })
        if state == "blocked":
            suffix = f" on {', '.join(waiting_on)}" if waiting_on else ""
            self._record_activity(
                "waiting",
                f"Blocked; waiting{suffix}",
                status="waiting",
            )
        elif state == "idle" and waiting_on:
            self._record_activity(
                "waiting",
                f"Waiting on {', '.join(waiting_on)}",
                status="waiting",
            )
        elif state == "done":
            self._record_activity(
                "waiting",
                "Work item complete; standing by",
                status="completed",
            )

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
        env.update(self.workspace.environment)
        return env

    def _run_claude(self, prompt: str, schema: Dict[str, Any], timeout_seconds: int) -> Dict[str, Any]:
        if shutil.which("claude") is None:
            raise RuntimeError("claude CLI not found on PATH")
        requested_id = self.session_id or str(uuid.uuid4())
        args = [
            "claude", "-p", "--output-format", "stream-json", "--verbose",
            "--json-schema", json.dumps(schema),
            "--permission-mode", "acceptEdits" if self.writable else "dontAsk",
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
        allowed_tools = list(DEFAULT_CLAUDE_ALLOWED_TOOLS)
        if self.web_research:
            allowed_tools += CLAUDE_WEB_RESEARCH_TOOLS
        args += ["--allowedTools", *allowed_tools]
        if not self.writable:
            args += ["--disallowedTools", "Edit", "Write", "NotebookEdit"]
        if self.fresh:
            args += ["--setting-sources", "project", "--no-session-persistence", "--disable-slash-commands"]
        proc, events = self._run_streaming_process(
            args,
            prompt,
            timeout_seconds,
        )
        if proc.returncode != 0:
            raise RuntimeError(f"Claude Code exited {proc.returncode}: {(proc.stderr or '').strip()}")
        envelope = next(
            (
                event for event in reversed(events)
                if (
                    event.get("type") == "result"
                    or (
                        "structured_output" in event
                        and "is_error" in event
                    )
                )
            ),
            None,
        )
        if envelope is None:
            raise RuntimeError("Claude Code returned no result event")
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
        codex_prefix = ["codex", *(["--search"] if self.web_research else [])]
        if self.session_id:
            args = [
                *codex_prefix, "exec", "resume", "--json",
                "--output-schema", str(schema_path),
                "--output-last-message", str(output_path),
            ]
            if self.model:
                args += ["--model", self.model]
            effort = "low" if self.reasoning == "minimal" else self.reasoning
            args += ["-c", f'model_reasoning_effort="{effort}"', self.session_id, "-"]
        else:
            args = [
                *codex_prefix, "exec", "--cd", str(self.workspace.cwd),
                "--sandbox", "workspace-write" if self.writable else "read-only",
                "--json", "--output-schema", str(schema_path),
                "--output-last-message", str(output_path),
            ]
            if self.fresh:
                args += ["--ephemeral"]
            if self.model:
                args += ["--model", self.model]
            effort = "low" if self.reasoning == "minimal" else self.reasoning
            args += ["-c", f'model_reasoning_effort="{effort}"']
            for directory in self.workspace.additional_directories:
                args += ["--add-dir", str(directory)]
            args += ["-"]
        proc, events = self._run_streaming_process(
            args,
            prompt,
            timeout_seconds,
        )
        for event in events:
            if event.get("type") == "thread.started":
                self.session_id = event.get("thread_id") or event.get("threadId") or self.session_id
        if proc.returncode != 0:
            error_event = next(
                (
                    event for event in reversed(events)
                    if event.get("type") in {"error", "turn.failed"}
                ),
                {},
            )
            error_value = error_event.get("error") or error_event.get("message")
            if isinstance(error_value, dict):
                error_value = error_value.get("message") or json.dumps(error_value)
            detail = self._sanitize_text(
                error_value or proc.stderr or "no diagnostic",
            )
            raise RuntimeError(f"Codex exited {proc.returncode}: {detail}")
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

    def _run_streaming_process(
        self,
        args: List[str],
        prompt: str,
        timeout_seconds: int,
    ) -> Tuple[subprocess.CompletedProcess, List[Dict[str, Any]]]:
        """Run a native CLI while consuming JSONL events as they arrive."""
        process = subprocess.Popen(
            args,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            cwd=self.workspace.cwd,
            env=self._process_environment(),
            bufsize=1,
        )
        stdout_queue: queue.Queue[Optional[str]] = queue.Queue()
        stderr_chunks: List[str] = []

        def read_stdout() -> None:
            assert process.stdout is not None
            for line in process.stdout:
                stdout_queue.put(line)
            stdout_queue.put(None)

        def read_stderr() -> None:
            assert process.stderr is not None
            stderr_chunks.extend(process.stderr.readlines())

        stdout_thread = threading.Thread(target=read_stdout, daemon=True)
        stderr_thread = threading.Thread(target=read_stderr, daemon=True)
        stdout_thread.start()
        stderr_thread.start()
        assert process.stdin is not None
        try:
            process.stdin.write(prompt)
            process.stdin.close()
        except BrokenPipeError:
            pass

        stdout_chunks: List[str] = []
        events: List[Dict[str, Any]] = []
        deadline = time.monotonic() + timeout_seconds
        stdout_done = False
        timed_out = False
        while not stdout_done or process.poll() is None:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                timed_out = True
                process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                break
            try:
                line = stdout_queue.get(timeout=min(0.2, remaining))
            except queue.Empty:
                continue
            if line is None:
                stdout_done = True
                continue
            stdout_chunks.append(line)
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(event, dict):
                events.append(event)
                self._observe_native_event(event)

        stdout_thread.join(timeout=2)
        stderr_thread.join(timeout=2)
        returncode = process.poll()
        if returncode is None:
            process.kill()
            returncode = process.wait(timeout=5)
        completed = subprocess.CompletedProcess(
            args=args,
            returncode=returncode,
            stdout="".join(stdout_chunks),
            stderr="".join(stderr_chunks),
        )
        self._persist_process_output(completed)
        if timed_out:
            raise subprocess.TimeoutExpired(
                args,
                timeout_seconds,
                output=completed.stdout,
                stderr=completed.stderr,
            )
        return completed, events

    def _observe_native_event(self, event: Dict[str, Any]) -> None:
        """Translate provider events into safe, provider-neutral telemetry."""
        if self.provider == "codex":
            self._observe_codex_event(event)
        elif self.provider == "claude":
            self._observe_claude_event(event)

    def _observe_codex_event(self, event: Dict[str, Any]) -> None:
        event_type = str(event.get("type") or "")
        if event_type == "turn.failed":
            error_value = event.get("error") or {}
            if isinstance(error_value, dict):
                error_value = error_value.get("message") or "provider error"
            self._record_activity(
                "provider",
                "Codex provider error: "
                f"{self._sanitize_text(error_value or 'unknown error')}",
                status="failed",
            )
            return
        item = event.get("item")
        if not isinstance(item, dict):
            return
        item_type = str(item.get("type") or "")
        status = (
            "started" if event_type == "item.started"
            else "failed" if item.get("status") == "failed"
            else "completed"
        )
        native_id = str(item.get("id") or "") or None
        if item_type == "command_execution":
            self._record_command_activity(
                str(item.get("command") or ""),
                status=status,
                native_id=native_id,
                exit_code=item.get("exit_code"),
            )
        elif item_type == "file_change":
            changes = item.get("changes") or []
            paths = [
                self._display_path(change.get("path"))
                for change in changes
                if isinstance(change, dict) and change.get("path")
            ]
            paths = [path for path in paths if path]
            self._record_activity(
                "edit",
                "Editing " + (", ".join(paths[:4]) or "workspace files"),
                status=status,
                paths=paths,
                native_id=native_id,
            )
        elif item_type in {"web_search", "web_search_call"}:
            query = item.get("query") or item.get("queries") or "external sources"
            if isinstance(query, list):
                query = "; ".join(str(value) for value in query[:4])
            self._record_activity(
                "web",
                f"Searching the web: {self._sanitize_text(query)}",
                status=status,
                native_id=native_id,
            )
        elif item_type in {"mcp_tool_call", "tool_call"}:
            tool_name = str(item.get("name") or item.get("tool") or "tool")
            self._record_activity(
                "tool",
                f"Using {self._sanitize_text(tool_name)}",
                status=status,
                native_id=native_id,
            )

    def _observe_claude_event(self, event: Dict[str, Any]) -> None:
        if event.get("type") == "system" and event.get("subtype") == "init":
            self.session_id = event.get("session_id") or self.session_id
            return
        if event.get("type") != "assistant":
            return
        message = event.get("message")
        if not isinstance(message, dict):
            return
        content = message.get("content")
        if not isinstance(content, list):
            return
        for block in content:
            if not isinstance(block, dict) or block.get("type") != "tool_use":
                continue
            name = str(block.get("name") or "")
            if not name or name == "StructuredOutput":
                continue
            inputs = block.get("input") if isinstance(block.get("input"), dict) else {}
            native_id = str(block.get("id") or "") or None
            if name == "Bash":
                self._record_command_activity(
                    str(inputs.get("command") or ""),
                    status="started",
                    native_id=native_id,
                )
            elif name == "Read":
                path = self._display_path(inputs.get("file_path"))
                self._record_activity(
                    "read",
                    f"Reading {path or 'a project file'}",
                    status="started",
                    paths=[path] if path else [],
                    native_id=native_id,
                )
            elif name in {"Grep", "Glob"}:
                query = (
                    inputs.get("pattern")
                    or inputs.get("query")
                    or inputs.get("path")
                    or ""
                )
                self._record_activity(
                    "search",
                    f"Searching files/symbols: {self._sanitize_text(query)}",
                    status="started",
                    native_id=native_id,
                )
            elif name == "WebSearch":
                self._record_activity(
                    "web",
                    "Searching the web: "
                    f"{self._sanitize_text(inputs.get('query') or '')}",
                    status="started",
                    native_id=native_id,
                )
            elif name == "WebFetch":
                self._record_activity(
                    "web",
                    "Reading external source: "
                    f"{self._sanitize_text(inputs.get('url') or '')}",
                    status="started",
                    native_id=native_id,
                )
            elif name in {"Edit", "Write", "NotebookEdit"}:
                path = self._display_path(
                    inputs.get("file_path") or inputs.get("notebook_path"),
                )
                self._record_activity(
                    "edit",
                    f"Editing {path or 'a workspace file'}",
                    status="started",
                    paths=[path] if path else [],
                    native_id=native_id,
                )
            else:
                self._record_activity(
                    "tool",
                    f"Using {self._sanitize_text(name)}",
                    status="started",
                    native_id=native_id,
                )

    def _record_command_activity(
        self,
        command: str,
        *,
        status: str,
        native_id: Optional[str],
        exit_code: Any = None,
    ) -> None:
        sanitized = self._sanitize_command(command)
        lowered = sanitized.lower()
        critical_paths = sorted(set(re.findall(
            r"docs/Core/Critical/[A-Za-z0-9._+/-]+",
            sanitized,
        )))
        if re.search(r"(?:^|\s)(?:pytest|py\.test)(?:\s|$)", lowered):
            kind = "test"
            prefix = "Running tests" if status == "started" else "Tests finished"
        elif re.search(r"(?:^|\s)git(?:\s|$)", lowered):
            kind = "git"
            prefix = "Inspecting Git history/state"
        elif re.search(r"(?:^|\s)(?:rg|grep|find|fd)(?:\s|$)", lowered):
            kind = "search"
            prefix = "Searching files/symbols"
        elif critical_paths or re.search(
            r"(?:^|\s)(?:cat|sed|head|tail|less|wc)(?:\s|$)",
            lowered,
        ):
            kind = "read"
            prefix = (
                f"Reading {', '.join(critical_paths[:3])}"
                if critical_paths else "Reading project files"
            )
        else:
            kind = "command"
            prefix = "Running command" if status == "started" else "Command finished"
        if exit_code not in (None, 0):
            status = "failed"
        detail = sanitized if sanitized else "project command"
        content = prefix if critical_paths and kind == "read" else f"{prefix}: {detail}"
        self._record_activity(
            kind,
            content,
            status=status,
            paths=critical_paths,
            native_id=native_id,
            extra={"exit_code": exit_code} if exit_code is not None else None,
        )

    def _display_path(self, value: Any) -> str:
        if not value:
            return ""
        raw = str(value)
        path = Path(raw).expanduser()
        roots = [self.workspace.cwd, *self.workspace.additional_directories]
        if path.is_absolute():
            for root in roots:
                try:
                    relative = path.resolve().relative_to(root.resolve())
                except (OSError, ValueError):
                    continue
                parts = list(relative.parts)
                if parts and parts[0] == "canonical":
                    parts = parts[1:]
                return PurePosixPath(*parts).as_posix()
            try:
                relative = path.resolve().relative_to(self.run_dir.resolve())
                return f"<run>/{relative.as_posix()}"
            except (OSError, ValueError):
                return path.name
        normalized = PurePosixPath(raw).as_posix()
        return normalized.removeprefix("./")

    def _sanitize_command(self, command: str) -> str:
        text = str(command or "").replace("\n", " ").strip()
        replacements = [(self.workspace.cwd, ".")]
        replacements.extend(
            (directory / "canonical", "")
            for directory in self.workspace.additional_directories
        )
        replacements.extend(
            (directory, "<context>")
            for directory in self.workspace.additional_directories
        )
        replacements.append((self.run_dir, "<run>"))
        for root, replacement in replacements:
            text = text.replace(str(root), replacement)
        text = re.sub(r"^/bin/(?:zsh|bash)\s+-lc\s+", "", text)
        if len(text) >= 2 and text[0] == text[-1] and text[0] in {"'", '"'}:
            text = text[1:-1]
        return self._sanitize_text(text)

    @staticmethod
    def _sanitize_text(value: Any, max_chars: int = 360) -> str:
        text = " ".join(str(value or "").split())
        text = _SECRET_ASSIGNMENT_RE.sub(r"\1=<redacted>", text)
        text = _URL_TOKEN_RE.sub(r"\1<redacted>", text)
        if len(text) > max_chars:
            text = text[: max_chars - 1].rstrip() + "…"
        return text

    def _record_activity(
        self,
        kind: str,
        content: str,
        *,
        status: str,
        paths: Optional[List[str]] = None,
        native_id: Optional[str] = None,
        extra: Optional[Dict[str, Any]] = None,
    ) -> None:
        record = {
            "schema": ACTIVITY_SCHEMA,
            "ts": _utc_now(),
            "runId": self.run_dir.name,
            "agent_id": self.session_key,
            "provider": self.provider,
            "turn": self.turn,
            "type": kind,
            "status": status,
            "content": self._sanitize_text(content, max_chars=520),
            "paths": [path for path in (paths or []) if path][:8],
            "native_id": native_id,
        }
        if extra:
            record.update(extra)
        line = json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n"
        with _ACTIVITY_WRITE_LOCK:
            with (self.run_dir / "activity.jsonl").open(
                "a",
                encoding="utf-8",
            ) as handle:
                handle.write(line)

    def _persist_process_output(self, proc: subprocess.CompletedProcess) -> None:
        stem = self.run_dir / f"{_safe_name(self.session_key)}_turn_{self.turn:03d}"
        stdout = proc.stdout or ""
        if self.provider == "claude":
            # Switching Claude to stream-json must not create a new durable
            # chain-of-thought/tool-result archive. The sanitized activity log
            # owns operational telemetry; retain only the final result envelope
            # needed for diagnostics and usage reconciliation.
            safe_lines: List[str] = []
            for line in stdout.splitlines():
                try:
                    event = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if event.get("type") == "result":
                    safe_lines.append(json.dumps(event, ensure_ascii=False))
            stdout = "\n".join(safe_lines) + ("\n" if safe_lines else "")
        stem.with_name(stem.name + "_stdout.txt").write_text(
            stdout,
            encoding="utf-8",
        )
        stem.with_name(stem.name + "_stderr.txt").write_text(proc.stderr or "", encoding="utf-8")


def validate_agent_reply(value: Any) -> Dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError("agent reply must be an object")
    required = {
        "messages", "summary", "state", "artifacts", "candidate", "risk",
        "disposition", "final",
    }
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
    if value["disposition"] not in DISPOSITIONS:
        raise ValueError("invalid terminal disposition")
    # Dispositional contradictions are normalized, never fatal. A failed turn
    # gives the model no feedback about why, so a raise here consumed whole
    # turns across two live runs while the model repeated the same shape:
    # workers say "no_op" to mean "nothing this turn", and the safe reading of
    # every contradiction is an ordinary continue. A lead that truly intends
    # the terminal no_op sends final=true with no candidate, which passes
    # through untouched.
    if value["disposition"] == "no_op" and (
        not value["final"] or value["candidate"] is not None
    ):
        value["disposition"] = "continue"
        value["final"] = False
    if value["final"] and value["disposition"] == "continue":
        value["final"] = False
    for message in value["messages"]:
        if not isinstance(message, dict):
            raise ValueError("message must be an object")
        required_fields = {
            "to", "tag", "content", "candidate", "workItem", "risk",
        }
        optional_fields = {"goalClass", "predicateId", "evaluatorId"}
        if (
            not required_fields.issubset(message)
            or set(message) - required_fields - optional_fields
            or message.get("tag") not in MESSAGE_TAGS
        ):
            raise ValueError("invalid message fields or tag")
        for field_name in optional_fields:
            message.setdefault(field_name, None)
        if message.get("risk") is not None and message["risk"] not in RISKS:
            raise ValueError("invalid message risk")
    return value


def validate_run_conclusion(value: Any) -> Dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError("run conclusion must be an object")
    required = set(RUN_CONCLUSION_SCHEMA["required"])
    if set(value) != required:
        raise ValueError(
            f"run conclusion fields must be exactly {sorted(required)}"
        )
    for field in (
        "accomplishments",
        "conclusive_findings",
        "evidence_and_tests",
        "scientific_or_product_blockers",
        "infrastructure_failures",
        "unresolved",
        "limitations",
    ):
        items = value[field]
        if (
            not isinstance(items, list)
            or any(not isinstance(item, str) or not item.strip() for item in items)
        ):
            raise ValueError(f"run conclusion {field} must be a string array")
    for field in ("summary", "next_action"):
        if not isinstance(value[field], str) or not value[field].strip():
            raise ValueError(f"run conclusion {field} is required")
    readiness = set(
        RUN_CONCLUSION_SCHEMA["properties"]["promotion_readiness"]["enum"]
    )
    if value["promotion_readiness"] not in readiness:
        raise ValueError("invalid run conclusion promotion_readiness")
    proposal = value.get("proposed_successor_admission")
    if proposal is not None:
        try:
            value["proposed_successor_admission"] = validate_admission(proposal)
        except ValueError:
            # An invalid proposal must not invalidate the terminal record;
            # the continuation simply falls back to carrying the parent
            # contract, and the raw text survives in the conclusion prose.
            value["proposed_successor_admission"] = None
    return value


def _normalize_round_language(
    value: Dict[str, Any],
    digest: Dict[str, Any],
) -> Dict[str, Any]:
    """Correct the common model error that conflates rounds with turns."""
    result = dict(value)
    summary = str(result.get("summary") or "")
    total_rounds = int(digest.get("rounds", 0) or 0)
    working_rounds = int(digest.get("working_rounds", 0) or 0)
    closeout_rounds = int(digest.get("closeout_rounds", 0) or 0)
    replacements = {
        f"{total_rounds}-turn limit": f"{total_rounds}-round limit",
        f"{total_rounds} turn limit": f"{total_rounds} round limit",
        (
            f"{working_rounds} working turns plus "
            f"{closeout_rounds} closeout turns"
        ): (
            f"{working_rounds} working rounds plus "
            f"{closeout_rounds} closeout rounds"
        ),
    }
    for old, new in replacements.items():
        summary = summary.replace(old, new)
    result["summary"] = summary
    return result


def _render_run_conclusion_markdown(conclusion: Dict[str, Any]) -> str:
    def section(title: str, values: List[str]) -> List[str]:
        lines = [f"## {title}", ""]
        lines.extend(
            [f"- {value}" for value in values]
            if values else
            ["_None recorded._"]
        )
        lines.append("")
        return lines

    lines = [
        "# Organization Run Conclusion",
        "",
        f"- Run: `{conclusion.get('run_id', 'unknown')}`",
        f"- Status: `{conclusion.get('terminal_status', 'unknown')}`",
        f"- Lead: `{conclusion.get('lead_agent_id', 'unknown')}`",
        f"- Generated by: `{conclusion.get('generated_by', 'unknown')}`",
        f"- Promotion readiness: `{conclusion.get('promotion_readiness', 'unknown')}`",
        "",
        "## Outcome",
        "",
        str(conclusion.get("summary") or "No summary was available."),
        "",
    ]
    lines.extend(section(
        "What was accomplished", list(conclusion.get("accomplishments") or []),
    ))
    lines.extend(section(
        "Conclusive findings",
        list(conclusion.get("conclusive_findings") or []),
    ))
    lines.extend(section(
        "Evidence and tests",
        list(conclusion.get("evidence_and_tests") or []),
    ))
    lines.extend(section(
        "Scientific or product blockers",
        list(conclusion.get("scientific_or_product_blockers") or []),
    ))
    lines.extend(section(
        "Infrastructure failures",
        list(conclusion.get("infrastructure_failures") or []),
    ))
    lines.extend(section(
        "Unresolved",
        list(conclusion.get("unresolved") or []),
    ))
    lines.extend(section(
        "Exact candidates",
        [
            (
                f"`{record.get('candidate')}` — "
                f"{record.get('kind', 'unknown')}"
            )
            for record in conclusion.get("candidates", [])
        ],
    ))
    lines.extend(section(
        "Artifacts", list(conclusion.get("artifacts") or []),
    ))
    lines.extend([
        "## Recommended next action",
        "",
        str(conclusion.get("next_action") or "Inspect the durable run record."),
        "",
    ])
    lines.extend(section(
        "Limitations", list(conclusion.get("limitations") or []),
    ))
    return "\n".join(lines).rstrip() + "\n"


def _write_run_conclusion_files(
    run_dir: Path,
    conclusion: Dict[str, Any],
) -> None:
    temporary = run_dir / f".run-conclusion.{os.getpid()}.tmp"
    temporary.write_text(
        json.dumps(conclusion, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    temporary.replace(run_dir / "run-conclusion.json")
    markdown = run_dir / f".run-conclusion.{os.getpid()}.md.tmp"
    markdown.write_text(
        _render_run_conclusion_markdown(conclusion),
        encoding="utf-8",
    )
    markdown.replace(run_dir / "run-conclusion.md")


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


def resolve_experiment_policy(
    project_root: Path, experiment_policy: Optional[str],
) -> Optional[Path]:
    """Resolve one tracked, project-local autonomous experiment policy."""
    if experiment_policy is None:
        return None
    if not isinstance(experiment_policy, str) or not experiment_policy.strip():
        raise ValueError(
            "experiment_policy must be a non-empty project-relative path"
        )
    supplied = Path(experiment_policy).expanduser()
    if supplied.is_absolute():
        raise ValueError("experiment_policy must be relative to the project root")
    project_root = project_root.resolve()
    candidate = (project_root / supplied).resolve()
    if candidate == project_root or not _path_is_within(candidate, project_root):
        raise ValueError("experiment_policy escapes or selects the project root")
    if candidate.is_symlink() or not candidate.is_file():
        raise FileNotFoundError(
            f"experiment_policy must name a regular tracked file: {candidate}"
        )
    relative = candidate.relative_to(project_root).as_posix()
    tracked = _git(
        project_root,
        ["ls-files", "--error-unmatch", "--", relative],
    ).strip()
    if tracked != relative:
        raise ValueError(f"experiment_policy is not tracked: {relative}")
    return candidate


def _safe_project_relative(raw: Any, *, label: str) -> str:
    if not isinstance(raw, str) or not raw.strip():
        raise ValueError(f"{label} must be a non-empty project-relative path")
    supplied = PurePosixPath(raw.strip())
    if supplied.is_absolute() or ".." in supplied.parts or not supplied.parts:
        raise ValueError(f"{label} is unsafe: {raw}")
    return supplied.as_posix().rstrip("/")


def _load_experiment_policy_definition(
    project_root: Path,
    policy_path: Path,
) -> Dict[str, Any]:
    """Validate the generic host-run evaluator policy without executing it."""
    try:
        definition = json.loads(policy_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(
            f"experiment policy is not valid JSON: {policy_path}"
        ) from exc
    if not isinstance(definition, dict):
        raise ValueError("experiment policy must be a JSON object")
    if definition.get("schema") != EXPERIMENT_POLICY_SCHEMA:
        raise ValueError(
            f"experiment policy schema must be {EXPERIMENT_POLICY_SCHEMA!r}"
        )
    if definition.get("enabled") is not True:
        raise ValueError("experiment policy must explicitly set enabled=true")

    def bounded_integer(name: str, minimum: int, maximum: int) -> int:
        value = definition.get(name)
        if isinstance(value, bool) or not isinstance(value, int):
            raise ValueError(f"experiment policy {name} must be an integer")
        if value < minimum or value > maximum:
            raise ValueError(
                f"experiment policy {name} must be between "
                f"{minimum} and {maximum}"
            )
        return value

    max_trials = bounded_integer("max_trials_per_contract", 1, 100)
    max_non_improving = bounded_integer(
        "max_consecutive_non_improving", 1, max_trials,
    )
    max_wall = bounded_integer(
        "max_contract_wall_seconds", 1, 86_400,
    )
    promotion_requires_goal_progress = definition.get(
        "promotion_requires_goal_progress",
        False,
    )
    if not isinstance(promotion_requires_goal_progress, bool):
        raise ValueError(
            "experiment policy promotion_requires_goal_progress must be Boolean"
        )
    raw_evaluators = definition.get("evaluators")
    if not isinstance(raw_evaluators, list) or not raw_evaluators:
        raise ValueError("experiment policy evaluators must be a non-empty array")
    evaluators: Dict[str, Dict[str, Any]] = {}
    for index, raw_evaluator in enumerate(raw_evaluators):
        if not isinstance(raw_evaluator, dict):
            raise ValueError(f"experiment evaluator {index} must be an object")
        evaluator_id = str(raw_evaluator.get("id") or "").strip()
        if not evaluator_id or not re.fullmatch(r"[A-Za-z0-9._-]+", evaluator_id):
            raise ValueError(f"experiment evaluator {index} has an invalid id")
        if evaluator_id in evaluators:
            raise ValueError(f"duplicate experiment evaluator id: {evaluator_id}")
        raw_commands = raw_evaluator.get("commands")
        if not isinstance(raw_commands, list) or not raw_commands:
            raise ValueError(
                f"experiment evaluator {evaluator_id} requires commands"
            )
        commands: List[Dict[str, Any]] = []
        for command_index, raw_command in enumerate(raw_commands):
            if not isinstance(raw_command, dict):
                raise ValueError(
                    f"experiment evaluator {evaluator_id} command "
                    f"{command_index} must be an object"
                )
            argv = raw_command.get("argv")
            if (
                not isinstance(argv, list)
                or not argv
                or any(
                    not isinstance(value, str)
                    or not value
                    or "\0" in value
                    for value in argv
                )
            ):
                raise ValueError(
                    f"experiment evaluator {evaluator_id} command "
                    f"{command_index} has invalid argv"
                )
            timeout = raw_command.get("timeout_seconds")
            if (
                isinstance(timeout, bool)
                or not isinstance(timeout, int)
                or timeout < 1
                or timeout > 1800
            ):
                raise ValueError(
                    f"experiment evaluator {evaluator_id} command "
                    f"{command_index} timeout must be 1..1800 seconds"
                )
            commands.append({
                "argv": list(argv),
                "timeout_seconds": timeout,
            })
        raw_immutable_paths = raw_evaluator.get("immutable_paths")
        if not isinstance(raw_immutable_paths, list):
            raise ValueError(
                f"experiment evaluator {evaluator_id} immutable_paths must "
                "be an array"
            )
        immutable_paths = [
            _safe_project_relative(
                raw,
                label=f"experiment evaluator {evaluator_id} immutable path",
            )
            for raw in raw_immutable_paths
        ]
        if not immutable_paths:
            raise ValueError(
                f"experiment evaluator {evaluator_id} requires immutable_paths"
            )
        for relative in immutable_paths:
            source = project_root / relative
            if source.is_symlink() or not source.exists():
                raise ValueError(
                    f"experiment evaluator {evaluator_id} immutable path "
                    f"is missing or unsafe: {relative}"
                )
            if not _git(project_root, ["ls-files", "--", relative]).strip():
                raise ValueError(
                    f"experiment evaluator {evaluator_id} immutable path "
                    f"is not tracked: {relative}"
                )
        raw_mutable_roots = raw_evaluator.get("mutable_roots")
        if not isinstance(raw_mutable_roots, list):
            raise ValueError(
                f"experiment evaluator {evaluator_id} mutable_roots must "
                "be an array"
            )
        mutable_roots = [
            _safe_project_relative(
                raw,
                label=f"experiment evaluator {evaluator_id} mutable root",
            )
            for raw in raw_mutable_roots
        ]
        if not mutable_roots:
            raise ValueError(
                f"experiment evaluator {evaluator_id} requires mutable_roots"
            )
        result_mode = str(raw_evaluator.get("result_mode") or "").strip()
        if result_mode not in {"command_exit", "json_file"}:
            raise ValueError(
                f"experiment evaluator {evaluator_id} result_mode must be "
                "command_exit or json_file"
            )
        hard_gates = raw_evaluator.get("hard_gates", [])
        if (
            not isinstance(hard_gates, list)
            or any(
                not isinstance(value, str) or not value.strip()
                for value in hard_gates
            )
        ):
            raise ValueError(
                f"experiment evaluator {evaluator_id} hard_gates must "
                "be a string array"
            )
        raw_metrics = raw_evaluator.get("metrics", [])
        if not isinstance(raw_metrics, list):
            raise ValueError(
                f"experiment evaluator {evaluator_id} metrics must be an array"
            )
        metrics: List[Dict[str, Any]] = []
        for metric_index, raw_metric in enumerate(raw_metrics):
            if not isinstance(raw_metric, dict):
                raise ValueError(
                    f"experiment evaluator {evaluator_id} metric "
                    f"{metric_index} must be an object"
                )
            metric_id = str(raw_metric.get("id") or "").strip()
            direction = raw_metric.get("direction")
            unit = str(raw_metric.get("unit") or "").strip()
            tolerance = raw_metric.get("tolerance", 0.0)
            if (
                not metric_id
                or not re.fullmatch(r"[A-Za-z0-9._-]+", metric_id)
                or direction not in {"minimize", "maximize"}
                or not unit
                or isinstance(tolerance, bool)
                or not isinstance(tolerance, (int, float))
                or float(tolerance) < 0
            ):
                raise ValueError(
                    f"experiment evaluator {evaluator_id} metric "
                    f"{metric_index} is invalid"
                )
            metrics.append({
                "id": metric_id,
                "direction": direction,
                "unit": unit,
                "tolerance": float(tolerance),
            })
        goal_success_rule = str(
            raw_evaluator.get("goal_success_rule") or "",
        ).strip()
        if promotion_requires_goal_progress and not goal_success_rule:
            raise ValueError(
                f"experiment evaluator {evaluator_id} requires a "
                "goal_success_rule when promotion_requires_goal_progress=true"
            )
        if result_mode == "command_exit" and (hard_gates or metrics):
            raise ValueError(
                f"command_exit evaluator {evaluator_id} cannot declare "
                "JSON hard gates or metrics"
            )
        raw_predicates = raw_evaluator.get("predicates", [])
        if not isinstance(raw_predicates, list):
            raise ValueError(
                f"experiment evaluator {evaluator_id} predicates must be an "
                "array"
            )
        predicates: Dict[str, Dict[str, Any]] = {}
        metric_ids = {metric["id"] for metric in metrics}
        hard_gate_ids = {value.strip() for value in hard_gates}
        for predicate_index, raw_predicate in enumerate(raw_predicates):
            if not isinstance(raw_predicate, dict):
                raise ValueError(
                    f"experiment evaluator {evaluator_id} predicate "
                    f"{predicate_index} must be an object"
                )
            predicate_id = str(raw_predicate.get("id") or "").strip()
            goal_class = str(
                raw_predicate.get("goal_class") or "",
            ).strip()
            source = str(raw_predicate.get("source") or "").strip()
            result_id = raw_predicate.get("result_id")
            comparison_rule_id = str(
                raw_predicate.get("comparison_rule_id") or "",
            ).strip()
            if (
                not predicate_id
                or not re.fullmatch(r"[A-Za-z0-9._-]+", predicate_id)
                or predicate_id in predicates
                or goal_class not in GOAL_CLASSES
                or source not in PREDICATE_SOURCES
                or comparison_rule_id not in COMPARISON_RULES
            ):
                raise ValueError(
                    f"experiment evaluator {evaluator_id} predicate "
                    f"{predicate_index} is invalid"
                )
            if source == "commands_pass":
                if result_id is not None or comparison_rule_id != "false_to_true":
                    raise ValueError(
                        f"predicate {predicate_id} commands_pass requires "
                        "result_id=null and comparison_rule_id=false_to_true"
                    )
            elif (
                not isinstance(result_id, str)
                or not result_id
                or (
                    source == "hard_gate"
                    and (
                        result_id not in hard_gate_ids
                        or comparison_rule_id != "false_to_true"
                    )
                )
                or (
                    source == "metric"
                    and (
                        result_id not in metric_ids
                        or comparison_rule_id not in {"maximize", "minimize"}
                    )
                )
            ):
                raise ValueError(
                    f"predicate {predicate_id} does not match evaluator "
                    f"{evaluator_id} result declarations"
                )
            predicates[predicate_id] = {
                "id": predicate_id,
                "goal_class": goal_class,
                "source": source,
                "result_id": result_id,
                "comparison_rule_id": comparison_rule_id,
            }
        raw_resource_limits = raw_evaluator.get("resource_limits", {})
        if not isinstance(raw_resource_limits, dict):
            raise ValueError(
                f"experiment evaluator {evaluator_id} resource_limits must "
                "be an object"
            )
        if set(raw_resource_limits) - {
            "max_threads", "same_host_required",
        }:
            raise ValueError(
                f"experiment evaluator {evaluator_id} resource_limits has "
                "unsupported fields"
            )
        max_threads = raw_resource_limits.get("max_threads", 1)
        same_host_required = raw_resource_limits.get(
            "same_host_required", True,
        )
        if (
            isinstance(max_threads, bool)
            or not isinstance(max_threads, int)
            or max_threads < 1
            or max_threads > 256
        ):
            raise ValueError(
                f"experiment evaluator {evaluator_id} max_threads must be "
                "between 1 and 256"
            )
        if not isinstance(same_host_required, bool):
            raise ValueError(
                f"experiment evaluator {evaluator_id} "
                "same_host_required must be Boolean"
            )
        raw_change_limits = raw_evaluator.get("change_limits", {})
        if not isinstance(raw_change_limits, dict):
            raise ValueError(
                f"experiment evaluator {evaluator_id} change_limits must "
                "be an object"
            )
        if set(raw_change_limits) - {
            "max_changed_lines", "max_diff_hunks",
        }:
            raise ValueError(
                f"experiment evaluator {evaluator_id} change_limits has "
                "unsupported fields"
            )
        max_changed_lines = raw_change_limits.get("max_changed_lines", 1000)
        max_diff_hunks = raw_change_limits.get("max_diff_hunks", 100)
        for field_name, value, maximum in (
            ("max_changed_lines", max_changed_lines, 100_000),
            ("max_diff_hunks", max_diff_hunks, 10_000),
        ):
            if (
                isinstance(value, bool)
                or not isinstance(value, int)
                or value < 1
                or value > maximum
            ):
                raise ValueError(
                    f"experiment evaluator {evaluator_id} {field_name} "
                    f"must be between 1 and {maximum}"
                )
        immutable_inventory = [
            {
                "path": relative,
                "object": _git(
                    project_root,
                    ["rev-parse", f"HEAD:{relative}"],
                ).strip(),
            }
            for relative in sorted(set(immutable_paths))
        ]
        immutable_ground_truth_sha256 = hashlib.sha256(
            json.dumps(
                immutable_inventory,
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=False,
            ).encode("utf-8")
        ).hexdigest()
        evaluator_profile = {
            "id": evaluator_id,
            "commands": commands,
            "immutable_paths": list(dict.fromkeys(immutable_paths)),
            "mutable_roots": list(dict.fromkeys(mutable_roots)),
            "result_mode": result_mode,
            "hard_gates": list(dict.fromkeys(
                value.strip() for value in hard_gates
            )),
            "metrics": metrics,
            "predicates": predicates,
            "goal_success_rule": goal_success_rule,
            "resource_limits": {
                "max_threads": max_threads,
                "same_host_required": same_host_required,
            },
            "change_limits": {
                "max_changed_lines": max_changed_lines,
                "max_diff_hunks": max_diff_hunks,
            },
            "immutable_ground_truth_sha256": (
                immutable_ground_truth_sha256
            ),
        }
        evaluator_profile["profile_sha256"] = hashlib.sha256(
            json.dumps(
                evaluator_profile,
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=False,
            ).encode("utf-8")
        ).hexdigest()
        evaluators[evaluator_id] = evaluator_profile
    return {
        "schema": EXPERIMENT_POLICY_SCHEMA,
        "source_path": str(policy_path),
        "source_relative": policy_path.relative_to(project_root).as_posix(),
        "source_sha256": _sha256_file(policy_path),
        "max_trials_per_contract": max_trials,
        "max_consecutive_non_improving": max_non_improving,
        "max_contract_wall_seconds": max_wall,
        "promotion_requires_goal_progress": (
            promotion_requires_goal_progress
        ),
        "evaluators": evaluators,
    }


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
    lane_paths_mode = definition.get("lane_paths_mode", "required")
    if not isinstance(common, dict) or not isinstance(common.get("paths"), list):
        raise ValueError("context manifest common.paths must be an array")
    if not isinstance(agents, dict):
        raise ValueError("context manifest agents must be an object")
    if not isinstance(full_context_agents, list) or any(
        not isinstance(agent_id, str) for agent_id in full_context_agents
    ):
        raise ValueError("context manifest full_context_agents must be an array of IDs")
    if lane_paths_mode not in {"required", "on_demand"}:
        raise ValueError(
            "context manifest lane_paths_mode must be 'required' or 'on_demand'"
        )
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
        "lane_paths_mode": lane_paths_mode,
    }


def _materialize_pack_file(
    source: Path,
    destination: Path,
    materialized: Dict[str, Path],
) -> None:
    """Place a pack file, sharing bytes with any identical earlier placement.

    Roles genuinely receive different file SETS, so the per-agent directory
    layout has to stay. What does not have to stay is a private byte copy of
    every shared file: across eleven packs in one recorded run, 2,080 pack files
    held 24,010,389 bytes of which only 3,027,496 were unique. 87.4% redundant,
    and evidence snapshots duplicated a pre-existing corpus again on top.

    A hard link keeps the layout, the bytes, the mode and the mtime identical to
    a copy, so `verify_context_packs` and every other reader are unaffected, but
    the second and later placements of the same source cost an inode instead of
    a file. Falls back to copying when linking is unavailable (a different
    filesystem, or a platform that refuses), so correctness never depends on it.

    One property does change, and it is a deliberate trade. Linked packs share an
    inode, so an agent that chmods a read-only pack file and edits it corrupts
    that file for every role holding it, where previously it would have corrupted
    only its own copy. Hash-binding is unaffected: `verify_context_packs` detects
    the mutation either way, and now detects it in every affected pack rather
    than one. Given packs are materialized 0o444, agents work in separate
    worktrees, and the alternative is hundreds of megabytes of duplication per
    run, wider detection of a deliberate violation is the better side to be on.
    """
    first = materialized.get(str(source))
    if first is not None:
        try:
            os.link(first, destination)
            return
        except OSError:
            pass  # cross-device, or links unsupported: fall through to a copy
    shutil.copy2(source, destination)
    materialized.setdefault(str(source), destination)


def prepare_context_packs(
    project_root: Path,
    run_dir: Path,
    context_manifest: Optional[str],
    topology: Topology,
) -> Optional[Dict[str, Any]]:
    """Materialize hash-bound, read-only educational context per agent."""
    project_root = project_root.resolve()
    # source path -> first materialized destination, so later packs can
    # hard-link to it instead of copying the bytes again.
    materialized: Dict[str, Path] = {}
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
            if definition["lane_paths_mode"] == "on_demand":
                declared_library_paths.extend([
                    *lane["declared_paths"],
                    *lane["declared_library_paths"],
                ])
            else:
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
            _materialize_pack_file(source, destination, materialized)
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
            "lane_paths_mode": definition["lane_paths_mode"],
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
            "lane_paths_mode": definition["lane_paths_mode"],
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
        "lane_paths_mode": definition["lane_paths_mode"],
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
        # Keep the virtual-environment entrypoint path intact. Resolving the
        # symlink to the base interpreter discards pyvenv.cfg discovery and
        # therefore the canonical environment's installed dependencies.
        f"exec {shlex.quote(str(canonical_python))} \"$@\"\n"
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
    def _supervisor_for(self, worker_id: str) -> str:
        """See _supervisor_of; shared so the two classes cannot drift."""
        return _supervisor_of(self.topology, worker_id)

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
        experiment_policy: Optional[str] = None,
        max_experiments: int = 3,
        max_closeout_rounds: int = DEFAULT_CLOSEOUT_ROUNDS,
        continuation_from_run_id: Optional[str] = None,
        continuation_conclusion_sha256: Optional[str] = None,
        mission_origin: str = "direct",
        admission: Optional[Dict[str, Any]] = None,
    ):
        self.project_root = project_root.resolve()
        self.mission = mission.strip()
        # The launch surface enforces admission; a runner constructed directly
        # (tests, replay) may carry none, and prompts/records then omit it.
        self.admission = validate_admission(admission) if admission else None
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
        self.experiment_policy_path = experiment_policy
        self.continuation_from_run_id = (
            str(continuation_from_run_id).strip()
            if continuation_from_run_id else None
        )
        self.continuation_conclusion_sha256 = (
            str(continuation_conclusion_sha256).strip()
            if continuation_conclusion_sha256 else None
        )
        self.mission_origin = str(mission_origin or "direct").strip()
        self.evidence_manifest: Optional[Dict[str, Any]] = None
        self.evidence_verified_at: Optional[str] = None
        self.context_pack_manifest: Optional[Dict[str, Any]] = None
        self.context_verified_at: Optional[str] = None
        self.run_id = run_id
        self.run_dir = run_dir
        self.candidate_artifact_root = self.run_dir / "candidate-artifacts"
        self.experiment_loop_root = self.run_dir / "experiment-loop"
        self.candidate_artifact_manifests: List[Dict[str, Any]] = []
        self.experiment_policy: Optional[Dict[str, Any]] = None
        self.experiment_contracts: Dict[str, Dict[str, Any]] = {}
        self.experiment_contract_by_work_item: Dict[str, str] = {}
        self.active_experiment_by_worker: Dict[str, str] = {}
        self.experiment_trials: List[Dict[str, Any]] = []
        self.experiment_baselines: Dict[str, Dict[str, Any]] = {}
        self.experiment_champions: Dict[str, Dict[str, Any]] = {}
        self.experiment_non_improving: Dict[str, int] = {}
        self.experiment_halted_workers: Set[str] = set()
        self.experiment_resource_fingerprints: Dict[str, str] = {}
        self.experiment_ledger_head_sha256: Optional[str] = None
        self.candidate_progress: Optional[Dict[str, Any]] = None
        self.goal_baselines: Dict[str, Dict[str, Any]] = {}
        self.goal_candidate_evaluations: List[Dict[str, Any]] = []
        self._experiment_contract_started: Dict[str, float] = {}
        self._experiment_loop_lock = threading.Lock()
        self.experiment_records: List[Dict[str, Any]] = []
        self._experiment_records_by_turn: Dict[
            Tuple[str, int], Dict[str, Any]
        ] = {}
        self._experiment_lock = threading.Lock()
        self.artifact_staging_prefix = (
            f"{ARTIFACT_STAGING_ROOT}/{_safe_name(run_id)}"
        )
        self.max_rounds = max(1, int(max_rounds))
        self.max_closeout_rounds = max(0, int(max_closeout_rounds))
        self.max_experiments = max(0, int(max_experiments))
        # Round by which an experiment-driven run must have authored at least one
        # contract. Both recorded runs used 0 of 3 experiment bundles, never
        # created a contracts/ directory, and still ran to the round limit: one
        # produced 198 files of records and twelve rounds of prose. A run that
        # cannot author a contract has already reported it has nothing to
        # execute. Half the working rounds, never less than three, so a slow
        # start is not punished.
        # Workers act from round 2 (round 1 is lead reconnaissance). Give them
        # at least two rounds of their own before concluding nothing will be
        # authored, and never exceed max_rounds or the check could never fire.
        _first_worker_round = 2
        self._experiment_contract_deadline = min(
            self.max_rounds,
            max(4, _first_worker_round + 2, (self.max_rounds + 1) // 2),
        )
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
        self.worker_goals: Dict[str, Dict[str, Any]] = {}
        # Fallback assignments issued when the hierarchy did not delegate.
        # Surfaced in the terminal record so a run that only progressed via
        # fallbacks is not read as one where delegation worked.
        self.worker_goal_history: List[Dict[str, Any]] = []
        self.off_goal_flags: Dict[str, Dict[str, Any]] = {}
        self.sessions: Dict[str, SubscriptionSession] = {}
        self.turned: Set[str] = set()
        self._consecutive_turn_failures: Dict[str, int] = {}
        self._finalization_attempted = False
        self._nudged_assignments: Set[str] = set()
        self.prompt_bootstrapped: Set[str] = set()
        self.model_prompt_state_by_agent: Dict[str, Dict[str, Any]] = {}
        self.usage = {"input_tokens": 0, "cached_input_tokens": 0, "output_tokens": 0}
        self.usage_by_provider = {
            provider_name: {
                "input_tokens": 0, "cached_input_tokens": 0, "output_tokens": 0,
            }
            for provider_name in sorted(set(self.provider_by_agent.values()) | {self.blind_verifier_provider})
        }
        self._provider_session_usage: Dict[
            Tuple[str, str], Dict[str, int]
        ] = {}
        self.delivered_messages = 0
        self.dropped_messages = 0
        self.failed_turns = 0
        self.completed_turns = 0
        self.attempted_turns = 0
        self._trace_lock = threading.Lock()
        self.workspaces: Dict[str, Workspace] = {}
        self.caller_head: Optional[str] = None
        self.control_protocol = "reccli.organization-control.v1"
        self.paused = False
        self.integrated_candidates: Dict[str, str] = {}
        self.candidate_kinds: Dict[str, Dict[str, Any]] = {}
        self.host_state_brief: Dict[str, Any] = {}
        self._mission_ref_state: Optional[Dict[str, Any]] = None
        self._closeout_signatures: Set[str] = set()

    def run(self) -> Dict[str, Any]:
        if not self.mission:
            raise ValueError("mission must not be empty")
        self.run_dir.mkdir(parents=True, exist_ok=True)
        _validate_clean_repository(self.project_root)
        self.caller_head = _git(self.project_root, ["rev-parse", "HEAD"]).strip()
        self.candidate_artifact_root.mkdir(parents=True, exist_ok=False)
        self.candidate_artifact_root.chmod(0o555)
        self.experiment_loop_root.mkdir(parents=True, exist_ok=False)
        resolved_experiment_policy = resolve_experiment_policy(
            self.project_root,
            self.experiment_policy_path,
        )
        if resolved_experiment_policy is not None:
            self.experiment_policy = _load_experiment_policy_definition(
                self.project_root,
                resolved_experiment_policy,
            )
            immutable_loop_paths = {
                resolved_experiment_policy.relative_to(
                    self.project_root,
                ).as_posix(),
                *(
                    path
                    for evaluator in self.experiment_policy[
                        "evaluators"
                    ].values()
                    for path in evaluator["immutable_paths"]
                ),
            }
            for relative in sorted(immutable_loop_paths):
                if relative not in self.protected_paths:
                    self.protected_paths.append(relative)
            policy_copy = self.experiment_loop_root / "policy.json"
            policy_copy.write_bytes(resolved_experiment_policy.read_bytes())
            policy_copy.chmod(0o444)
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
        for agent_id in {
            self.topology.leader_id,
            *self.topology.manager_ids,
            *self.topology.final_reviewer_pool,
            self.topology.finalizer_id,
            *self.topology.worker_ids,
        }:
            if agent_id and agent_id in self.workspaces:
                self.workspaces[agent_id].additional_directories.append(
                    self.experiment_loop_root
                )
        if self.evidence_manifest:
            evidence_environment = {
                "RECCLI_EVIDENCE_MANIFEST": str(
                    self.run_dir / "evidence-manifest.json"
                ),
                "RECCLI_EVIDENCE_SNAPSHOT_ROOT": str(
                    self.evidence_manifest["snapshot_root"]
                ),
            }
            for workspace in self.workspaces.values():
                workspace.environment.update(evidence_environment)
        self._write_host_state_brief(round_number=0)
        self._write_json("run.json", {
            "run_id": self.run_id, "created_at": _utc_now(),
            "project_root": str(self.project_root), "provider": self.provider,
            "host_provider": self.host_provider,
            "provider_assignments": self.provider_by_agent,
            "blind_verifier_provider": self.blind_verifier_provider,
            "topology": self.topology.topology_id, "mission": self.mission,
            "admission": self.admission,
            "mission_origin": self.mission_origin,
            "continuation_from_run_id": self.continuation_from_run_id,
            "continuation_conclusion_sha256": (
                self.continuation_conclusion_sha256
            ),
            "scheduler": self.topology.scheduler,
            "coordination_cadence": (
                "round-1-lead-recon; round-2+-event-driven-parallel-work"
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
            "experiment_loop": {
                "enabled": self.experiment_policy is not None,
                "root": str(self.experiment_loop_root),
                "source_policy": self.experiment_policy_path,
                "source_policy_sha256": (
                    self.experiment_policy.get("source_sha256")
                    if self.experiment_policy else None
                ),
                "contracts": str(
                    self.experiment_loop_root / "contracts.jsonl"
                ),
                "trials": str(self.experiment_loop_root / "trials.jsonl"),
                "one_mutable_file": True,
                "baseline_required": True,
                "manager_cadence": "event-driven",
            },
            "experiment_records": str(self.run_dir / "experiments.jsonl"),
            "human_promotion_required": self.topology.human_promotion_required,
            "canonical_effects_applied": False,
            "control_protocol": self.control_protocol,
            "git_ownership": "reccli-host",
            "host_state_brief": str(self.run_dir / "host-state.json"),
            "host_state_sha256": self.host_state_brief.get("content_sha256"),
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
        no_promotion_report: Optional[str] = None
        pending_human_report: Optional[str] = None
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
            if closeout:
                closeout_signature = self._closeout_progress_signature()
                if closeout_signature in self._closeout_signatures:
                    self._event(
                        "closeout.no_progress",
                        round_number,
                        signature=closeout_signature,
                        detail=(
                            "No release-relevant candidate, governance, "
                            "integration, artifact, or inbox state changed"
                        ),
                    )
                    break
                self._closeout_signatures.add(closeout_signature)
            scheduled = (
                self._select_closeout_agents()
                if closeout else self._select_agents(round_number)
            )
            if not scheduled and self._nudge_pending_reviews(round_number):
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
            scheduled_ids = {agent.agent_id for agent in scheduled}
            phase = (
                "closeout"
                if closeout
                else "experiment_loop"
                if scheduled_ids & set(self.active_experiment_by_worker)
                else None
            )
            self._status(
                "running", round_number=round_number,
                detail=(
                    f"{phase_detail}: running "
                    f"{len(scheduled)} agent turns; "
                    f"{self.completed_turns} completed previously"
                ),
                scheduled_turns=len(scheduled),
                phase=phase,
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
                    self._record_turn_failure(
                        agent, str(item["error"]), round_number,
                    )
                    continue
                self._consecutive_turn_failures[agent.agent_id] = 0
                reply = item["reply"]
                self.states[agent.agent_id] = reply["state"]
                accounted_usage = self._add_usage(
                    item.get("usage", {}),
                    item.get("provider"),
                    item.get("session_id"),
                )
                self._append_jsonl(f"turns/{_safe_name(agent.agent_id)}.jsonl", {
                    "round": round_number, "agent_id": agent.agent_id,
                    "provider": item.get("provider"),
                    "status": "completed", "duration_ms": item["duration_ms"],
                    "session_id": item.get("session_id"), "usage": item.get("usage", {}),
                    "accounted_usage": accounted_usage,
                    "prompt_chars": item.get("prompt_chars"),
                    "prompt_mode": item.get("prompt_mode"),
                    "inbox_count": item.get("inbox_count"),
                    "host_state_sha256": item.get("host_state_sha256"),
                    "reply": reply,
                })
                for message in reply["messages"]:
                    self._deliver_message(agent.agent_id, message, round_number)
                if agent.agent_id in self.topology.worker_ids:
                    self._update_worker_goal_after_reply(
                        agent.agent_id,
                        reply,
                        round_number,
                    )
                bundle = item.get("candidate_artifact_bundle")
                if bundle:
                    recipient = _supervisor_of(self.topology, agent.agent_id)
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
                    self._finalization_attempted = True

            # A lead no_op is adjudicated before the delegation barrier: a run
            # whose lead just declared the admission's done condition satisfied
            # (or a stop condition triggered) must not have fallback work
            # forged for it by the very machinery that exists to keep work
            # flowing. Stopping is a successful outcome and costs one turn
            # instead of rounds of manufactured work.
            lead_no_op = next(
                (
                    item for item in final_attempts
                    if item["reply"].get("disposition") == "no_op"
                    and item["agent"].agent_id == self.topology.leader_id
                ),
                None,
            )
            if lead_no_op is not None:
                status = "completed_no_op"
                finalized_by = lead_no_op["agent"].agent_id
                final_summary = lead_no_op["reply"]["summary"]
                self._event(
                    "finalization.no_op", round_number,
                    agent_id=lead_no_op["agent"].agent_id,
                    done_condition=(
                        (self.admission or {}).get("done_condition")
                    ),
                    reason=lead_no_op["reply"]["summary"],
                )
                self._write_host_state_brief(round_number)
                break

            for item in final_attempts:
                agent = item["agent"]
                reply = item["reply"]
                candidate = reply.get("candidate")
                if reply.get("disposition") == "no_op":
                    self.states[agent.agent_id] = "working"
                    self._event(
                        "finalization.rejected", round_number,
                        agent_id=agent.agent_id,
                        reason="only the lead may declare no_op",
                    )
                    continue
                if agent.agent_id != self.topology.finalizer_id:
                    self._event("finalization.rejected", round_number, agent_id=agent.agent_id, reason="agent is not the finalizer")
                    continue
                disposition = reply.get("disposition")
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
                    # An event is not feedback: the inbox-only finalizer is
                    # never rescheduled by a silent rejection, so the run
                    # winds down over the exact approval it is waiting for.
                    self._system_message(
                        agent.agent_id,
                        "blocker",
                        (
                            f"Finalization of {candidate} is blocked on "
                            f"missing release approvals from "
                            f"{', '.join(missing)}. Request NO_VETO on this "
                            "exact candidate from each, then re-submit your "
                            "final reply."
                        ),
                        round_number,
                        candidate,
                        "final-release",
                        "release",
                    )
                    continue
                if disposition in {"no_promotion", "pending_human"}:
                    report_record = self._candidate_record(
                        self.workspaces[agent.agent_id], candidate,
                    )
                    if not any(
                        self._artifact_path(path)
                        for path in report_record.get("paths", [])
                    ):
                        self.states[agent.agent_id] = "working"
                        self._event(
                            "finalization.rejected", round_number,
                            agent_id=agent.agent_id,
                            candidate=candidate,
                            reason=(
                                f"{disposition} disposition requires a durable "
                                "run-artifact dossier"
                            ),
                        )
                        continue
                    if disposition == "pending_human":
                        status = "completed_pending_human"
                        pending_human_report = candidate
                        event_type = "finalization.pending_human"
                        event_field = "approval_report_candidate"
                    else:
                        status = "completed_no_promotion"
                        no_promotion_report = candidate
                        event_type = "finalization.no_promotion"
                        event_field = "report_candidate"
                    finalized_by = agent.agent_id
                    final_summary = reply["summary"]
                    self._event(
                        event_type,
                        round_number,
                        agent_id=agent.agent_id,
                        **{event_field: candidate},
                    )
                    break
                if disposition != "promote":
                    self.states[agent.agent_id] = "working"
                    self._event(
                        "finalization.rejected", round_number,
                        agent_id=agent.agent_id,
                        reason=(
                            "final disposition must be promote, no_promotion, "
                            "or pending_human"
                        ),
                    )
                    continue
                progress_verdict = self._candidate_goal_progress_verdict(
                    candidate,
                    round_number=round_number,
                )
                if not progress_verdict["qualifies"]:
                    status = "completed_no_promotion"
                    finalized_by = "reccli-host"
                    final_summary = (
                        "RecCli discarded the proposed candidate because the "
                        "project-owned evaluator recorded no improvement over "
                        "baseline bound to the exact stated current worker goal."
                    )
                    self.states[agent.agent_id] = "done"
                    self._event(
                        "finalization.no_goal_progress",
                        round_number,
                        agent_id=agent.agent_id,
                        candidate=candidate,
                        verdict_sha256=progress_verdict["verdict_sha256"],
                        reason=progress_verdict["reason"],
                    )
                    break
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
            self._write_host_state_brief(round_number)
            if status in {
                "completed",
                "completed_no_promotion",
                "completed_pending_human",
                "completed_no_op",
            }:
                break
            # Evaluated AFTER the round's work, not before it. Checked at the top
            # of the loop this ended the run one round early, so the deadline
            # round itself never ran and workers in a delegation-gated topology
            # (which first act in round 3) got a single round to produce anything.
            if not closeout and self._experiment_contract_deadline_passed(round_number):
                status = "no_experiment_contract"
                self._event(
                    "run.no_experiment_contract",
                    round_number,
                    deadline_round=self._experiment_contract_deadline,
                    max_experiments=self.max_experiments,
                )
                break

        self._reject_pending_control_requests(status, rounds)
        if status != "cancelled":
            self._status(
                "running",
                round_number=rounds,
                detail=(
                    "Working rounds are complete; the lead is writing the "
                    "terminal organization conclusion"
                ),
                scheduled_turns=1,
                phase="conclusion",
            )
        conclusion = self._write_terminal_lead_conclusion(
            status,
            rounds,
            verified_candidate=verified_candidate,
            promotion_candidate=promotion_candidate,
            promotion_request=promotion_request,
            no_promotion_report=no_promotion_report,
            pending_human_report=pending_human_report,
        )
        approval_request: Optional[Dict[str, Any]] = None
        if status == "completed_pending_human" and pending_human_report:
            approval_request = self._write_pending_human_approval_request(
                pending_human_report,
                conclusion,
            )
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
            "no_promotion_report": no_promotion_report,
            "pending_human_report": pending_human_report,
            "approval_request": (
                str(self.run_dir / "approval-request.json")
                if approval_request else None
            ),
            "human_promotion_required": self.topology.human_promotion_required,
            "canonical_effects_applied": False,
            "artifact_manifest": artifact_manifest,
            "final_reviewer_id": self.governance.release_reviewer_id,
            "blind_review": final_review, "usage": self.usage,
            "usage_by_provider": self.usage_by_provider,
            "delivered_messages": self.delivered_messages,
            "dropped_messages": self.dropped_messages,
            "candidate_counts": self._candidate_counts(),
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
                "used": self._experiment_used(),
                "remaining": self._experiment_remaining(),
                "records": list(self.experiment_records),
            },
            "protected_paths": self.protected_paths,
            "control_protocol": self.control_protocol,
            "git_ownership": "reccli-host",
            "host_state_brief": str(self.run_dir / "host-state.json"),
            "host_state_sha256": self.host_state_brief.get("content_sha256"),
            "integrated_candidates": dict(self.integrated_candidates),
            "candidate_progress": self.candidate_progress,
            "conclusion": conclusion,
            "conclusion_json": str(self.run_dir / "run-conclusion.json"),
            "conclusion_markdown": str(self.run_dir / "run-conclusion.md"),
        }
        self._write_json("result.json", result)
        try:
            record_outcome_event(
                self.project_root, "run_terminal", self.run_id,
                terminal_status=status,
                work_class=(self.admission or {}).get("work_class"),
                consumer=(
                    (self.admission or {}).get("consumer") or {}
                ).get("name"),
                usage=dict(self.usage),
                candidate_counts=self._candidate_counts(),
                completed_turns=self.completed_turns,
                promotion_readiness=conclusion.get("promotion_readiness"),
                verified_candidate=verified_candidate,
                promotion_candidate=promotion_candidate,
            )
        except Exception as exc:
            # The ledger measures the run; it must never fail it.
            self._event("outcome.ledger_failed", rounds, error=str(exc))
        self._status(
            status,
            round_number=rounds,
            detail=conclusion["summary"],
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
                ARTIFACT_STAGING_ROOT,
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

    def _experiment_used(self) -> int:
        return len(self.experiment_records)

    def _experiment_remaining(self) -> int:
        return max(0, self.max_experiments - self._experiment_used())

    def _experiment_contract_deadline_passed(self, round_number: int) -> bool:
        """True when an experiment-driven run has authored no contract in time.

        Only applies when the run was actually configured to run experiments: an
        experiment policy is attached and the budget is non-zero. A mission with
        no experiment loop is not failing by not using one, so it must never trip
        this.
        """
        if self.experiment_policy is None or self.max_experiments <= 0:
            return False
        # A gate-authoring or governance run never authors an experiment
        # contract: there is nothing to experiment on, its measurability is
        # the discrimination validator, and its exit is human ratification.
        # This deadline was built to kill contract-less IMPLEMENTATION runs
        # that drift into prose; category-blind, it executed three ceremony
        # runs at round 4 with budget remaining. The admission declares the
        # run's shape; believe it.
        if (self.admission or {}).get("work_class") in {
            "uncertainty_reduction", "hypothesis_test",
        }:
            return False
        # A run too short for workers to have acted at all cannot be said to have
        # failed to author a contract. Reporting no_experiment_contract there
        # would blame the run for a budget it was never given.
        first_worker_round = 2
        if self._experiment_contract_deadline < first_worker_round + 1:
            return False
        if round_number < self._experiment_contract_deadline:
            return False
        # A run attempting terminal closure is not a run with nothing to
        # execute. Run five reached a scorer-limited finding in one recon turn
        # and was mid-way through staging its no_promotion dossier when this
        # deadline killed it: the dossier survived only as loose artifacts,
        # the release review never ran, and the terminal read as generic
        # no-contract instead of the reviewed negative result it was.
        if self._finalization_attempted:
            return False
        return not self.experiment_contracts


    def _claim_experiment_slot(
        self,
        agent: AgentSpec,
        round_number: int,
        *,
        kind: str,
        candidate: Optional[str],
        paths: List[str],
    ) -> Dict[str, Any]:
        """Atomically charge one scientific work bundle per agent turn.

        Git-backed probes/data and ignored generated outputs are two storage
        channels for the same bounded resource. A turn using both consumes one
        slot, while parallel turns cannot collectively exceed the configured
        cap.
        """
        key = (agent.agent_id, int(round_number))
        with self._experiment_lock:
            existing = self._experiment_records_by_turn.get(key)
            if existing is not None:
                if kind not in existing["kinds"]:
                    existing["kinds"].append(kind)
                    existing["kinds"].sort()
                existing["paths"] = sorted(set([
                    *existing.get("paths", []),
                    *paths,
                ]))
                if candidate:
                    existing["candidate"] = candidate
                snapshot = dict(existing)
                action = "updated"
            else:
                if self._experiment_used() >= self.max_experiments:
                    self._append_jsonl("experiments.jsonl", {
                        "schema": EXPERIMENT_RECORD_SCHEMA,
                        "run_id": self.run_id,
                        "agent_id": agent.agent_id,
                        "round": round_number,
                        "candidate": candidate,
                        "kinds": [kind],
                        "paths": sorted(set(paths)),
                        "status": "rejected_budget_exhausted",
                        "maximum": self.max_experiments,
                        "used": self._experiment_used(),
                        "ts": _utc_now(),
                    })
                    raise RuntimeError(
                        "hard experiment budget exhausted "
                        f"({self.max_experiments} scientific work bundles)"
                    )
                record: Dict[str, Any] = {
                    "schema": EXPERIMENT_RECORD_SCHEMA,
                    "run_id": self.run_id,
                    "agent_id": agent.agent_id,
                    "round": round_number,
                    "candidate": candidate,
                    "kinds": [kind],
                    "paths": sorted(set(paths)),
                    "slot": self._experiment_used() + 1,
                    "status": "claimed",
                    "created_at": _utc_now(),
                }
                self.experiment_records.append(record)
                self._experiment_records_by_turn[key] = record
                snapshot = dict(record)
                action = "claimed"
            self._append_jsonl("experiments.jsonl", {
                **snapshot,
                "action": action,
                "ts": _utc_now(),
            })
        self._event(
            f"experiment.{action}",
            round_number,
            agent_id=agent.agent_id,
            candidate=candidate,
            kinds=snapshot["kinds"],
            slot=snapshot.get("slot"),
            maximum=self.max_experiments,
        )
        return snapshot

    def _metered_experiment_paths(
        self,
        agent: AgentSpec,
        paths: Set[str],
    ) -> List[str]:
        """Classify Git-backed worker probes/data that must consume a slot.

        Metering follows the experiment budget, not a topology name: any run
        with a non-zero budget charges worker probe bundles against it.
        """
        if (
            self.max_experiments <= 0
            or agent.agent_id not in self.topology.worker_ids
        ):
            return []
        prefix = PurePosixPath(self.artifact_staging_prefix)
        result: List[str] = []
        for path in sorted(paths):
            supplied = PurePosixPath(path)
            try:
                relative = supplied.relative_to(prefix)
            except ValueError:
                continue
            lowered_parts = {part.lower() for part in relative.parts[:-1]}
            suffix = relative.suffix.lower()
            name = relative.name.lower()
            if (
                lowered_parts & EXPERIMENT_PATH_COMPONENTS
                or "__pycache__" in lowered_parts
                or name.startswith("test_")
                or suffix not in REPORT_ONLY_SUFFIXES
            ):
                result.append(path)
        return result

    def _seal_reported_artifacts(
        self, agent: AgentSpec, reply: Dict[str, Any], round_number: int,
    ) -> Optional[Dict[str, Any]]:
        """Seal explicitly reported ignored/generated outputs outside Git.

        The Git candidate remains the source-change identity. This bundle binds
        its generated evidence to that exact commit without retaining large
        experiment outputs in Git object storage.
        """
        workspace = self.workspaces[agent.agent_id]
        sources: List[Tuple[str, Path]] = []
        stale: List[str] = []
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
                # After a host discard-and-reset, the model's memory of its
                # artifact paths is stale through no fault of its own, and
                # raising here killed the corrective turn of the run that
                # produced the first real candidate. A stale path is skipped
                # and recorded; if it was load-bearing evidence, the sealed
                # bundle will visibly lack it at review.
                stale.append(str(supplied))
                continue
            relative = source.relative_to(workspace.cwd.resolve()).as_posix()
            if self._artifact_path(relative):
                # Native agents commonly cite their durable tracked report in
                # the reply's artifact list. It is already bound to the Git
                # candidate and must not be treated as generated experiment
                # output or invalidate otherwise useful delegation messages.
                continue
            tracked = _git(workspace.cwd, ["ls-files", "--", relative]).strip()
            if tracked:
                continue
            _assert_snapshot_source_has_no_symlinks(source)
            if source not in seen:
                seen.add(source)
                sources.append((relative, source))
        if stale:
            self._event(
                "artifacts.stale_reported_paths",
                round_number,
                agent_id=agent.agent_id,
                paths=stale,
            )
        if not sources:
            return None
        handoffs = [
            message for message in reply["messages"]
            if message.get("tag") == "handoff" and message.get("candidate")
        ]
        identities = {
            (message["candidate"], message.get("workItem"), message.get("risk"))
            for message in handoffs
        }
        if not handoffs:
            # A worker legitimately generates outputs before its gates pass:
            # a baseline probe, a failed experiment, a diagnostic run.
            # Reporting them on a status-only reply is honesty, not a
            # violation; sealing waits for the reply that hands off a
            # candidate and re-reports them. The old rule failed the turn,
            # which taught the mission author to instruct models to misreport
            # their outputs, and both providers refused to. Two live runs
            # died on it.
            self._event(
                "artifacts.unsealed_probe_outputs",
                round_number,
                agent_id=agent.agent_id,
                paths=[relative for relative, _ in sources],
            )
            return None
        if len(identities) != 1:
            raise RuntimeError(
                "reported generated artifacts require exactly one candidate handoff"
            )
        candidate, work_item, risk = next(iter(identities))
        head = _git(workspace.cwd, ["rev-parse", "HEAD"]).strip()
        if candidate != head:
            raise RuntimeError(
                f"generated artifacts must bind to current HEAD {head}, got {candidate}"
            )
        self._claim_experiment_slot(
            agent,
            round_number,
            kind="sealed-generated-output",
            candidate=str(candidate),
            paths=[relative for relative, _ in sources],
        )

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
            "schema": "reccli.organization-approval-request.v1",
            "version": 1,
            "created_at": _utc_now(),
            "run_id": self.run_id,
            "request_kind": "candidate_promotion",
            "title": "Verified candidate ready for human promotion",
            "question": (
                "Approve the exact verified candidate and fast-forward the "
                "current clean local branch?"
            ),
            "status": "awaiting_human_authorization",
            "canonical_effects_applied": False,
            "base_commit": base,
            "verified_candidate": verified_candidate,
            "proposed_promotion_candidate": promotion_candidate,
            "proposed_promotion_branch": promotion_branch,
            "action": {
                "type": "fast_forward_local",
                "remote_push": False,
                "effect": (
                    "Revalidate the request and repository, then fast-forward "
                    "the clean local branch to the proposed promotion candidate. "
                    "No remote push is performed."
                ),
            },
            "changed_paths": changed_paths,
            "protected_paths": list(self.protected_paths),
            "experiment_budget": {
                "maximum": self.max_experiments,
                "used": self._experiment_used(),
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

    def _extract_gate_proposal(
        self,
        candidate: str,
        workspace: Optional[Workspace] = None,
    ) -> Optional[Dict[str, Any]]:
        """Read a staged gate proposal from the exact report candidate tree.

        Returns None when the candidate stages no proposal, a dict with an
        "error" key when it stages a malformed one (surfaced to the human
        reviewer instead of silently dropped), and the normalized manifest
        otherwise. Validation of targets happens again on the control side at
        apply time; this pass exists so the approval packet is honest.
        """
        if workspace is None:
            workspace = self.workspaces[self.topology.finalizer_id]
        # A chain-adopted packet lives under the AUTHORING run's prefix (its
        # identity is pinned to that tree), so discovery scans the whole
        # staging root rather than assuming this run authored the proposal.
        listing = subprocess.run(
            [
                "git", "ls-tree", "-r", "--name-only", candidate, "--",
                ARTIFACT_STAGING_ROOT,
            ],
            cwd=workspace.cwd, capture_output=True, check=False,
        )
        if listing.returncode != 0:
            return None
        manifest_candidates = [
            path for path in listing.stdout.decode(
                "utf-8", errors="replace",
            ).splitlines()
            if path.endswith("/gate-proposal/gate-proposal.json")
        ]
        own_manifest = (
            f"{self.artifact_staging_prefix}/gate-proposal/gate-proposal.json"
        )
        if own_manifest in manifest_candidates:
            manifest_path = own_manifest
        elif len(manifest_candidates) == 1:
            manifest_path = manifest_candidates[0]
        elif not manifest_candidates:
            return None
        else:
            return {
                "error": (
                    "the candidate stages multiple gate proposals; exactly "
                    f"one is ratifiable: {sorted(manifest_candidates)}"
                ),
                "manifest_path": None,
            }
        proc = subprocess.run(
            ["git", "show", f"{candidate}:{manifest_path}"],
            cwd=workspace.cwd, capture_output=True, check=False,
        )
        if proc.returncode != 0:
            return None
        try:
            manifest = json.loads(proc.stdout.decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError):
            return {
                "error": "gate proposal manifest is not valid JSON",
                "manifest_path": manifest_path,
            }
        problems: List[str] = []
        if manifest.get("schema") != GATE_PROPOSAL_SCHEMA:
            problems.append(f"schema must be {GATE_PROPOSAL_SCHEMA}")
        for field_name in (
            "predicate_id", "evaluator_id", "rationale", "baseline_command",
        ):
            value = manifest.get(field_name)
            if not isinstance(value, str) or not value.strip():
                problems.append(f"{field_name} must be a non-empty string")
        if not isinstance(
            manifest.get("proposed_tolerance"), (int, float),
        ):
            problems.append("proposed_tolerance must be a number")
        if "measured_baseline" not in manifest:
            problems.append("measured_baseline is required")
        # Executable ratification: the owner must be able to judge "this gate
        # discriminates" with one look and zero domain expertise. Every
        # proposal ships a truth-exact case and a corrupted case with
        # measured scores, and the host checks the arithmetic: truth-exact
        # must score within the proposed tolerance, corrupted must not. The
        # cylinder scorer — a ratified-wrong gate that measured tessellation
        # fidelity while claiming to measure segmentation — would have failed
        # this exact check: truth-exact input scored 61x its tolerance.
        discrimination = manifest.get("discrimination")
        tolerance = manifest.get("proposed_tolerance")
        if not isinstance(discrimination, dict):
            problems.append(
                "discrimination proof is required: {truth_exact_command, "
                "truth_exact_score, corrupted_command, corrupted_score}"
            )
        else:
            for field_name in ("truth_exact_command", "corrupted_command"):
                value = discrimination.get(field_name)
                if not isinstance(value, str) or not value.strip():
                    problems.append(
                        f"discrimination.{field_name} must be a non-empty "
                        "string"
                    )
            truth_score = discrimination.get("truth_exact_score")
            corrupted_score = discrimination.get("corrupted_score")
            if not isinstance(truth_score, (int, float)):
                problems.append(
                    "discrimination.truth_exact_score must be a number"
                )
            if not isinstance(corrupted_score, (int, float)):
                problems.append(
                    "discrimination.corrupted_score must be a number"
                )
            if (
                isinstance(truth_score, (int, float))
                and isinstance(tolerance, (int, float))
                and truth_score > tolerance
            ):
                problems.append(
                    "gate fails its own discrimination proof: truth-exact "
                    f"input scores {truth_score}, above the proposed "
                    f"tolerance {tolerance}"
                )
            if (
                isinstance(corrupted_score, (int, float))
                and isinstance(tolerance, (int, float))
                and corrupted_score <= tolerance
            ):
                problems.append(
                    "gate fails its own discrimination proof: the corrupted "
                    f"case scores {corrupted_score}, within the proposed "
                    f"tolerance {tolerance}, so the gate does not separate"
                )
        fools = manifest.get("what_fools_this_gate")
        if not isinstance(fools, str) or len(fools.strip()) < 40:
            problems.append(
                "what_fools_this_gate must be a blind-spot analysis legible "
                "to the owner (at least 40 characters)"
            )
        # The law travels with its first enforcement mission: a gate proposal
        # may carry the successor admission for the run that closes it.
        # Without this, ratification packets inherit whatever the terminal
        # conclusion proposed, which for a governance run is honestly another
        # ceremony: the fifteenth gap launched ceremony three on the first
        # real click.
        gate_successor = manifest.get("successor_admission")
        normalized_gate_successor = None
        if gate_successor is not None:
            try:
                normalized_gate_successor = validate_admission(gate_successor)
            except ValueError as exc:
                problems.append(
                    f"successor_admission is invalid: {exc}"
                )
        files = manifest.get("files")
        # Proposal files live beside their manifest, whichever run authored it.
        prefix = manifest_path[: -len("gate-proposal.json")] + "files/"
        normalized_files: List[Dict[str, str]] = []
        if not isinstance(files, list) or not files:
            problems.append("files must be a non-empty list of {path, target}")
        else:
            for index, entry in enumerate(files):
                path = entry.get("path") if isinstance(entry, dict) else None
                target = (
                    entry.get("target") if isinstance(entry, dict) else None
                )
                if not isinstance(path, str) or not path.startswith(prefix):
                    problems.append(
                        f"files[{index}].path must live under {prefix}"
                    )
                    continue
                if (
                    not isinstance(target, str)
                    or not target.strip()
                    or PurePosixPath(target).is_absolute()
                    or ".." in PurePosixPath(target).parts
                ):
                    problems.append(
                        f"files[{index}].target must be a repo-relative path "
                        "without traversal"
                    )
                    continue
                exists = subprocess.run(
                    ["git", "cat-file", "-e", f"{candidate}:{path}"],
                    cwd=workspace.cwd, capture_output=True, check=False,
                )
                if exists.returncode != 0:
                    problems.append(
                        f"files[{index}].path is not in the candidate tree"
                    )
                    continue
                normalized_files.append({"path": path, "target": target})
        if problems:
            return {
                "error": "; ".join(problems),
                "manifest_path": manifest_path,
            }
        return {
            "schema": GATE_PROPOSAL_SCHEMA,
            "predicate_id": manifest["predicate_id"].strip(),
            "evaluator_id": manifest["evaluator_id"].strip(),
            "rationale": manifest["rationale"].strip(),
            "baseline_command": manifest["baseline_command"].strip(),
            "measured_baseline": manifest["measured_baseline"],
            "proposed_tolerance": manifest["proposed_tolerance"],
            "discrimination": {
                "truth_exact_command": (
                    discrimination["truth_exact_command"].strip()
                ),
                "truth_exact_score": discrimination["truth_exact_score"],
                "corrupted_command": (
                    discrimination["corrupted_command"].strip()
                ),
                "corrupted_score": discrimination["corrupted_score"],
            },
            "what_fools_this_gate": fools.strip(),
            "successor_admission": normalized_gate_successor,
            "files": normalized_files,
            "manifest_path": manifest_path,
        }

    def _warn_invalid_gate_proposal(
        self,
        agent: AgentSpec,
        workspace: Workspace,
        candidate: str,
        round_number: int,
    ) -> None:
        """Tell the author about a broken gate proposal while it can still fix it.

        Run six staged its gate substance in prose and left the manifest's
        machine fields null; nothing said so until the terminal packet, when
        no agent could act. Validation feedback belongs at materialization,
        rounds earlier.
        """
        proposal = self._extract_gate_proposal(candidate, workspace=workspace)
        if not proposal or not proposal.get("error"):
            return
        self._event(
            "gate_proposal.invalid",
            round_number,
            agent_id=agent.agent_id,
            candidate=candidate,
            error=proposal["error"],
        )
        self._system_message(
            agent.agent_id,
            "blocker",
            (
                f"The gate proposal staged in candidate {candidate} is "
                f"invalid and cannot be ratified as-is: {proposal['error']}. "
                "Complete the manifest's machine-readable fields before "
                "handoff; the dossier prose does not substitute for them."
            ),
            round_number,
            None,
            None,
            "routine",
        )

    def _write_pending_human_approval_request(
        self,
        report_candidate: str,
        conclusion: Dict[str, Any],
    ) -> Dict[str, Any]:
        """Stage an exact human-decision packet for a fresh continuation."""
        workspace = self.workspaces[self.topology.finalizer_id]
        report_record = self._candidate_record(workspace, report_candidate)
        report_files: List[Dict[str, Any]] = []
        remaining = 240_000
        for path in report_record.get("paths", []):
            if not self._artifact_path(path):
                continue
            blob_sha = _git(
                workspace.cwd,
                ["rev-parse", f"{report_candidate}:{path}"],
            ).strip()
            content = ""
            truncated = False
            if remaining > 0:
                proc = subprocess.run(
                    ["git", "show", f"{report_candidate}:{path}"],
                    cwd=workspace.cwd,
                    capture_output=True,
                    check=False,
                )
                if proc.returncode == 0:
                    decoded = proc.stdout.decode("utf-8", errors="replace")
                    content = decoded[:remaining]
                    truncated = len(decoded) > len(content)
                    remaining -= len(content)
            report_files.append({
                "path": path,
                "git_blob": blob_sha,
                "content": content,
                "truncated": truncated,
            })

        source_request = json.loads(
            (self.run_dir / "request.json").read_text(encoding="utf-8"),
        )
        gate_proposal_block = self._extract_gate_proposal(report_candidate)
        continuation = {
            "provider": source_request.get("provider_requested", self.provider),
            "topology": source_request.get(
                "topology", self.topology.topology_id,
            ),
            "max_rounds": int(source_request.get("max_rounds", self.max_rounds)),
            "max_concurrency": int(
                source_request.get("max_concurrency", self.max_concurrency),
            ),
            "turn_timeout_seconds": int(source_request.get(
                "turn_timeout_seconds", self.turn_timeout_seconds,
            )),
            "model": source_request.get("model") or "auto",
            "evidence_paths": list(source_request.get("evidence_paths") or []),
            "protected_paths": list(
                source_request.get("protected_paths") or [],
            ),
            "context_manifest": source_request.get("context_manifest"),
            "experiment_policy": source_request.get("experiment_policy"),
            "max_experiments": self._experiment_remaining(),
        }
        request: Dict[str, Any] = {
            "schema": "reccli.organization-approval-request.v1",
            "version": 1,
            "created_at": _utc_now(),
            "run_id": self.run_id,
            "request_kind": "checkpoint_continuation",
            "title": "Organization checkpoint awaiting your decision",
            "question": (
                "Approve the exact authority request documented by the "
                "reviewed dossier and continue the mission in a fresh run?"
            ),
            "status": "awaiting_human_authorization",
            "canonical_effects_applied": False,
            "base_commit": self.caller_head,
            "report_candidate": report_candidate,
            "report_kind": report_record.get("kind"),
            "report_paths": list(report_record.get("paths") or []),
            "report_files": report_files,
            "conclusion": {
                key: conclusion.get(key)
                for key in (
                    "summary",
                    "accomplishments",
                    "conclusive_findings",
                    "evidence_and_tests",
                    "scientific_or_product_blockers",
                    "infrastructure_failures",
                    "unresolved",
                    "next_action",
                    "limitations",
                )
            },
            "review_record": self.governance.snapshot(),
            # The admission the click's auto-launched successor should run
            # under. A ratified gate's own successor admission (the law's
            # first enforcement mission) outranks the conclusion's proposal,
            # which for a governance run is honestly another ceremony.
            "successor_admission": (
                (gate_proposal_block or {}).get("successor_admission")
                or conclusion.get("proposed_successor_admission")
            ),
            # The frontier seam: a run may stage a proposed capability gate
            # (predicate, evaluator wiring, fixture files) under its artifact
            # staging. The org can never apply it — the files target protected
            # paths — but the human approval click can. None when the
            # candidate stages no proposal; an "error" field when it staged a
            # malformed one, so the reviewer sees why nothing will apply.
            "gate_proposal": gate_proposal_block,
            "action": {
                "type": "start_successor",
                "remote_push": False,
                "effect": (
                    "Record an immutable human approval decision and launch a "
                    "fresh successor organization from the same clean Git HEAD. "
                    "The signed decision is added to its read-only evidence. "
                    "The terminal supervisor is never resumed."
                ),
            },
            "continuation": continuation,
            "original_mission": self.mission,
            "authorization_limits": [
                (
                    "Approval applies only to the exact report candidate and "
                    "question in this request."
                ),
                (
                    "Approval does not authorize remote push, unsupported "
                    "scientific claims, or mutation of protected evidence."
                ),
            ],
        }
        canonical = json.dumps(
            request,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        ).encode("utf-8")
        request["request_sha256"] = hashlib.sha256(canonical).hexdigest()
        self._write_json("approval-request.json", request)
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
        """True for run-artifact paths, this run's or any predecessor's.

        Matching only this run's prefix made chain adoption structurally
        impossible: a successor adopting a packet pinned to the authoring
        run's tree had its untracked files silently filtered at candidate
        capture, and the handoff materialized identity-only. Worse, had
        capture worked, the same run-scoped predicate would have classified
        the adopted packet as an implementation and failed the worker's
        artifacts write scope. Any path under the staging root is a run
        artifact; the authoring run's prefix is the packet's identity, not a
        trespass.
        """
        return path == ARTIFACT_STAGING_ROOT or path.startswith(
            ARTIFACT_STAGING_ROOT + "/"
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

    def _stage_literal_paths(
        self,
        workspace: Workspace,
        paths: Set[str],
        *,
        force: bool = False,
    ) -> None:
        """Stage only paths already observed by the host.

        Worktrees may contain ignored runtime bridges such as `.venv`.  A broad
        `git add -A .` asks Git to reconsider those paths even when exclusion
        pathspecs are present.  Literal, host-enumerated pathspecs keep runtime
        plumbing outside candidate identity and also avoid provider-controlled
        pathspec magic.
        """
        ordered = sorted(paths)
        for start in range(0, len(ordered), 128):
            args = ["add", "-A"]
            if force:
                args.append("-f")
            args.extend([
                "--",
                *(f":(literal){path}" for path in ordered[start:start + 128]),
            ])
            self._host_git(workspace, args)

    def _mission_commit_inventory(self) -> Dict[str, Any]:
        """Resolve exact commit identities mentioned by the mission once.

        This is deliberately mechanical. It prevents every agent from spending
        a turn rediscovering object existence, ancestry, and path inventories;
        it does not adjudicate whether a commit or its claims are scientifically
        correct.
        """
        if self._mission_ref_state is not None:
            return self._mission_ref_state
        launch_head = self.caller_head or _git(
            self.project_root, ["rev-parse", "HEAD"],
        ).strip()
        supplied_refs = sorted(set(re.findall(
            r"(?<![0-9a-f])([0-9a-f]{40})(?![0-9a-f])",
            self.mission.lower(),
        )))
        records: List[Dict[str, Any]] = []
        valid_commits: List[Tuple[str, str]] = []
        for supplied in supplied_refs:
            exists = subprocess.run(
                ["git", "cat-file", "-e", f"{supplied}^{{commit}}"],
                cwd=self.project_root,
                capture_output=True,
                check=False,
            )
            if exists.returncode != 0:
                records.append({
                    "supplied": supplied,
                    "exists_as_commit": False,
                })
                continue
            commit = _git(
                self.project_root, ["rev-parse", f"{supplied}^{{commit}}"],
            ).strip()
            valid_commits.append((supplied, commit))
            if commit == launch_head:
                relation = "launch_head"
            elif subprocess.run(
                ["git", "merge-base", "--is-ancestor", commit, launch_head],
                cwd=self.project_root,
                capture_output=True,
                check=False,
            ).returncode == 0:
                relation = "ancestor_of_launch_head"
            elif subprocess.run(
                ["git", "merge-base", "--is-ancestor", launch_head, commit],
                cwd=self.project_root,
                capture_output=True,
                check=False,
            ).returncode == 0:
                relation = "descendant_of_launch_head"
            else:
                relation = "diverged_from_launch_head"
            changed = subprocess.run(
                [
                    "git", "diff", "--name-only", "-z",
                    f"{launch_head}..{commit}",
                ],
                cwd=self.project_root,
                capture_output=True,
                check=False,
            )
            paths = (
                [
                    item.decode("utf-8", errors="surrogateescape")
                    for item in changed.stdout.split(b"\0") if item
                ]
                if changed.returncode == 0 else []
            )
            normal_paths = [
                path for path in paths
                if not path.startswith(ARTIFACT_STAGING_ROOT + "/")
            ]
            records.append({
                "supplied": supplied,
                "exists_as_commit": True,
                "commit": commit,
                "tree": _git(
                    self.project_root,
                    ["rev-parse", f"{commit}^{{tree}}"],
                ).strip(),
                "subject": _git(
                    self.project_root,
                    ["show", "-s", "--format=%s", commit],
                ).strip()[:500],
                "relation_to_launch_head": relation,
                "changed_path_count_vs_launch": len(paths),
                "normal_path_count_vs_launch": len(normal_paths),
                "changed_path_sample_vs_launch": paths[:32],
            })
        ancestry: List[Dict[str, str]] = []
        for index, (left_supplied, left) in enumerate(valid_commits):
            for right_supplied, right in valid_commits[index + 1:]:
                if left == right:
                    ancestry.append({
                        "ancestor": left_supplied,
                        "descendant": right_supplied,
                        "relation": "same_commit",
                    })
                    continue
                if subprocess.run(
                    ["git", "merge-base", "--is-ancestor", left, right],
                    cwd=self.project_root,
                    capture_output=True,
                    check=False,
                ).returncode == 0:
                    ancestry.append({
                        "ancestor": left_supplied,
                        "descendant": right_supplied,
                        "relation": "ancestor",
                    })
                elif subprocess.run(
                    ["git", "merge-base", "--is-ancestor", right, left],
                    cwd=self.project_root,
                    capture_output=True,
                    check=False,
                ).returncode == 0:
                    ancestry.append({
                        "ancestor": right_supplied,
                        "descendant": left_supplied,
                        "relation": "ancestor",
                    })
        self._mission_ref_state = {
            "launch_head": launch_head,
            "mentioned_commits": records,
            "mentioned_commit_ancestry": ancestry,
        }
        return self._mission_ref_state

    def _experiment_loop_snapshot(self) -> Dict[str, Any]:
        return {
            "enabled": self.experiment_policy is not None,
            "policy_path": self.experiment_policy_path,
            "policy_sha256": (
                self.experiment_policy.get("source_sha256")
                if self.experiment_policy else None
            ),
            "one_mutable_file": True,
            "one_host_commit_per_trial": True,
            "baseline_required": True,
            "promotion_requires_goal_progress": bool(
                self.experiment_policy
                and self.experiment_policy.get(
                    "promotion_requires_goal_progress",
                )
            ),
            "ledger_verified": True,
            "ledger_head_sha256": self.experiment_ledger_head_sha256,
            "semantic_single_change_proven": False,
            "contracts": [
                {
                    key: record.get(key)
                    for key in (
                        "sha256",
                        "work_item",
                        "manager_id",
                        "worker_id",
                        "mutable_file",
                        "evaluator_id",
                        "max_trials",
                        "max_consecutive_non_improving",
                        "max_wall_seconds",
                        "status",
                        "activation_baseline_candidate",
                        "goal_sha256",
                        "goal_success_rule",
                        "halt_reason",
                    )
                }
                for record in self.experiment_contracts.values()
            ],
            "trial_count": len(self.experiment_trials),
            "trials": list(self.experiment_trials[-32:]),
            "active_by_worker": dict(self.active_experiment_by_worker),
            "halted_workers": sorted(self.experiment_halted_workers),
            "champions": {
                contract_sha: {
                    key: outcome.get(key)
                    for key in (
                        "candidate",
                        "evaluator_id",
                        "hard_gates",
                        "metrics",
                        "evaluated_at",
                    )
                }
                for contract_sha, outcome
                in self.experiment_champions.items()
            },
            "candidate_progress": self.candidate_progress,
        }

    def _write_host_state_brief(self, round_number: int) -> Dict[str, Any]:
        workspace_state: Dict[str, Dict[str, Any]] = {}
        for agent_id, workspace in self.workspaces.items():
            head = _git(workspace.cwd, ["rev-parse", "HEAD"]).strip()
            workspace_state[agent_id] = {
                "base_commit": workspace.base_commit,
                "head": head,
                "state": self.states.get(agent_id, "idle"),
                "changed_from_base": bool(
                    workspace.base_commit and head != workspace.base_commit
                ),
            }
        candidates = [
            dict(record)
            for _, record in sorted(self.candidate_kinds.items())
        ]
        payload: Dict[str, Any] = {
            "schema": HOST_STATE_SCHEMA,
            "run_id": self.run_id,
            "updated_at": _utc_now(),
            "round": round_number,
            "mechanical_authority": (
                "RecCli owns commit existence, exact identity, ancestry, launch "
                "HEAD, host-created candidate kind, and integration identity. "
                "Agents own interpretation and must report a concrete "
                "contradiction instead of repeating these checks."
            ),
            "repository": {
                "project_root": str(self.project_root),
                "launch_head": self.caller_head,
                "canonical_effects_applied": False,
                "mission_origin": self.mission_origin,
                "continuation_from_run_id": self.continuation_from_run_id,
                "continuation_conclusion_sha256": (
                    self.continuation_conclusion_sha256
                ),
            },
            "mission_commit_inventory": self._mission_commit_inventory(),
            "known_candidates": candidates,
            "integrated_candidates": dict(self.integrated_candidates),
            "governance": self.governance.snapshot(),
            "worker_goals": dict(self.worker_goals),
            "off_goal_flags": list(self.off_goal_flags.values()),
            "workspaces": workspace_state,
            "experiment_budget": {
                "maximum": self.max_experiments,
                "used": self._experiment_used(),
                "remaining": self._experiment_remaining(),
                "records": list(self.experiment_records),
            },
            "experiment_loop": self._experiment_loop_snapshot(),
            "candidate_progress": self.candidate_progress,
        }
        canonical = json.dumps(
            payload,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        ).encode("utf-8")
        payload["content_sha256"] = hashlib.sha256(canonical).hexdigest()
        self.host_state_brief = payload
        self._write_json("host-state.json", payload)
        self._event(
            "host_state.updated",
            round_number,
            content_sha256=payload["content_sha256"],
            known_candidates=len(candidates),
        )
        return payload

    def _host_state_prompt(self, agent_id: str) -> str:
        if not self.host_state_brief:
            return ""
        primary_managers = _supervisor_ids(self.topology)
        tailored: Dict[str, Any] = {}
        has_assignable_predicate = bool(
            self.experiment_policy
            and any(
                evaluator.get("predicates")
                for evaluator in self.experiment_policy[
                    "evaluators"
                ].values()
            )
        )
        if has_assignable_predicate and agent_id in {
            self.topology.leader_id,
            *primary_managers,
        }:
            budget = self.host_state_brief.get("experiment_budget", {})
            tailored["experiment_budget"] = {
                key: budget.get(key)
                for key in ("maximum", "used", "remaining")
            }
        return (
            json.dumps(tailored, indent=2, ensure_ascii=False)
            if tailored else ""
        )

    def _active_experiment_prompt(self, agent_id: str) -> str:
        contract_sha = self.active_experiment_by_worker.get(agent_id)
        if not contract_sha:
            return ""
        contract = self.experiment_contracts[contract_sha]
        return f"""Active autonomous contract:
Contract: {contract['sha256']}
Work item: {contract.get('work_item')}
Evaluator: {contract.get('evaluator_id')}
Mutable file: `{contract.get('mutable_file')}`
Status: {contract.get('status')}
Change only the mutable file above and write one trial intent under
`{self.artifact_staging_prefix}/experiment-loop/trials/current.json`."""

    def _model_prompt_state(self, agent_id: str) -> Dict[str, Any]:
        return {
            "host": self._host_state_prompt(agent_id),
            "goal": self._goal_prompt(agent_id),
            "active_experiment_sha256": (
                self.active_experiment_by_worker.get(agent_id)
            ),
        }

    def _candidate_record(
        self,
        workspace: Workspace,
        candidate: str,
    ) -> Dict[str, Any]:
        cached = self.candidate_kinds.get(candidate)
        if cached is not None:
            return cached
        base = (
            workspace.base_commit
            or self.caller_head
            or _git(workspace.cwd, ["rev-parse", "HEAD"]).strip()
        )
        exists = self._host_git(
            workspace,
            ["cat-file", "-e", f"{candidate}^{{commit}}"],
            check=False,
        )
        if exists.returncode != 0:
            record = {"candidate": candidate, "kind": "unknown", "paths": []}
            self.candidate_kinds[candidate] = record
            return record
        paths = sorted(self._git_paths(
            workspace,
            ["diff", "--name-only", "-z", f"{base}..{candidate}"],
        ))
        if any(not self._artifact_path(path) for path in paths):
            kind = "implementation"
        elif paths:
            kind = "artifact-only"
        else:
            kind = "identity-only"
        record = {
            "candidate": candidate,
            "kind": kind,
            "paths": paths,
            "base": base,
        }
        self.candidate_kinds[candidate] = record
        return record

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
    def _experiment_string(payload: Dict[str, Any], name: str) -> str:
        value = payload.get(name)
        if not isinstance(value, str) or not value.strip():
            raise RuntimeError(
                f"experiment artifact field {name!r} must be a non-empty string"
            )
        return value.strip()

    def _validate_experiment_contract(
        self,
        agent: AgentSpec,
        path: Path,
    ) -> Dict[str, Any]:
        if self.experiment_policy is None:
            raise RuntimeError(
                "experiment-loop contracts require a project experiment policy"
            )
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise RuntimeError(
                f"experiment-loop contract is not valid JSON: {path}"
            ) from exc
        if not isinstance(payload, dict):
            raise RuntimeError("experiment-loop contract must be a JSON object")
        required = {
            "schema",
            "run_id",
            "work_item",
            "manager_id",
            "worker_id",
            "baseline_mode",
            "mutable_file",
            "evaluator_id",
            "objective",
            "success_rule",
            "max_trials",
            "max_consecutive_non_improving",
            "max_wall_seconds",
        }
        if set(payload) != required:
            raise RuntimeError(
                "experiment-loop contract fields must be exactly "
                f"{sorted(required)}"
            )
        if payload.get("schema") != EXPERIMENT_CONTRACT_SCHEMA:
            raise RuntimeError(
                f"experiment-loop contract schema must be "
                f"{EXPERIMENT_CONTRACT_SCHEMA}"
            )
        if payload.get("run_id") != self.run_id:
            raise RuntimeError(
                "experiment-loop contract run_id does not match this run"
            )
        if payload.get("manager_id") != agent.agent_id:
            raise RuntimeError(
                "experiment-loop contract manager_id does not match its author"
            )
        worker_id = self._experiment_string(payload, "worker_id")
        if worker_id not in self.topology.worker_ids:
            raise RuntimeError("experiment-loop contract worker_id is not a worker")
        expected_manager = self._supervisor_for(worker_id)
        if expected_manager != agent.agent_id:
            raise RuntimeError(
                f"{agent.agent_id} does not supervise {worker_id}"
            )
        if payload.get("baseline_mode") != "worker_head_at_activation":
            raise RuntimeError(
                "experiment-loop baseline_mode must be "
                "worker_head_at_activation"
            )
        work_item = self._experiment_string(payload, "work_item")
        mutable_file = _safe_project_relative(
            self._experiment_string(payload, "mutable_file"),
            label="experiment-loop mutable_file",
        )
        evaluator_id = self._experiment_string(payload, "evaluator_id")
        evaluator = self.experiment_policy["evaluators"].get(evaluator_id)
        if evaluator is None:
            raise RuntimeError(
                f"unknown experiment-loop evaluator: {evaluator_id}"
            )
        if not any(
            mutable_file == root or mutable_file.startswith(root + "/")
            for root in evaluator["mutable_roots"]
        ):
            raise RuntimeError(
                f"experiment-loop mutable file {mutable_file} is outside "
                f"evaluator {evaluator_id} mutable roots"
            )
        if any(
            mutable_file == immutable
            or mutable_file.startswith(immutable + "/")
            or immutable.startswith(mutable_file + "/")
            for immutable in evaluator["immutable_paths"]
        ):
            raise RuntimeError(
                "experiment-loop mutable file overlaps the immutable evaluator"
            )
        max_trials = payload.get("max_trials")
        max_non_improving = payload.get("max_consecutive_non_improving")
        max_wall = payload.get("max_wall_seconds")
        if (
            isinstance(max_trials, bool)
            or not isinstance(max_trials, int)
            or max_trials < 1
            or max_trials > self.experiment_policy["max_trials_per_contract"]
            or max_trials > self._experiment_remaining()
        ):
            raise RuntimeError(
                "experiment-loop max_trials must be positive and no greater "
                "than both the project policy and remaining organization budget"
            )
        if (
            isinstance(max_non_improving, bool)
            or not isinstance(max_non_improving, int)
            or max_non_improving < 1
            or max_non_improving > max_trials
            or max_non_improving
            > self.experiment_policy["max_consecutive_non_improving"]
        ):
            raise RuntimeError(
                "experiment-loop max_consecutive_non_improving is invalid"
            )
        if (
            isinstance(max_wall, bool)
            or not isinstance(max_wall, int)
            or max_wall < 1
            or max_wall > self.experiment_policy["max_contract_wall_seconds"]
        ):
            raise RuntimeError(
                "experiment-loop max_wall_seconds exceeds project policy"
            )
        self._experiment_string(payload, "objective")
        success_rule = self._experiment_string(payload, "success_rule")
        policy_success_rule = str(
            evaluator.get("goal_success_rule") or "",
        ).strip()
        if (
            self.experiment_policy.get(
                "promotion_requires_goal_progress",
                False,
            )
            and success_rule != policy_success_rule
        ):
            raise RuntimeError(
                "experiment-loop success_rule must exactly match the "
                "project-owned evaluator goal_success_rule"
            )
        raw = path.read_bytes()
        return {
            "kind": "contract",
            "work_item": work_item,
            "manager_id": agent.agent_id,
            "worker_id": worker_id,
            "mutable_file": mutable_file,
            "evaluator_id": evaluator_id,
            "objective": payload["objective"].strip(),
            "goal_success_rule": success_rule,
            "max_trials": max_trials,
            "max_consecutive_non_improving": max_non_improving,
            "max_wall_seconds": max_wall,
            "sha256": hashlib.sha256(raw).hexdigest(),
            "bytes": len(raw),
            "payload": payload,
            "source_path": str(path),
        }

    def _validate_experiment_trial(
        self,
        agent: AgentSpec,
        path: Path,
    ) -> Dict[str, Any]:
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise RuntimeError(
                f"experiment-loop trial intent is not valid JSON: {path}"
            ) from exc
        if not isinstance(payload, dict):
            raise RuntimeError("experiment-loop trial intent must be an object")
        required = {
            "schema",
            "run_id",
            "contract_sha256",
            "work_item",
            "worker_id",
            "hypothesis",
            "single_change",
            "expected_result",
        }
        if set(payload) != required:
            raise RuntimeError(
                "experiment-loop trial fields must be exactly "
                f"{sorted(required)}"
            )
        if payload.get("schema") != EXPERIMENT_TRIAL_SCHEMA:
            raise RuntimeError(
                f"experiment-loop trial schema must be {EXPERIMENT_TRIAL_SCHEMA}"
            )
        if payload.get("run_id") != self.run_id:
            raise RuntimeError(
                "experiment-loop trial run_id does not match this run"
            )
        if payload.get("worker_id") != agent.agent_id:
            raise RuntimeError(
                "experiment-loop trial worker_id does not match its author"
            )
        contract_sha = self._experiment_string(payload, "contract_sha256")
        if not re.fullmatch(r"[0-9a-f]{64}", contract_sha):
            raise RuntimeError("experiment-loop contract_sha256 is invalid")
        contract = self.experiment_contracts.get(contract_sha)
        if contract is None:
            raise RuntimeError(
                "experiment-loop trial references an unknown contract"
            )
        if (
            contract["worker_id"] != agent.agent_id
            or self.active_experiment_by_worker.get(agent.agent_id)
            != contract_sha
        ):
            raise RuntimeError(
                "experiment-loop trial contract is not active for this worker"
            )
        work_item = self._experiment_string(payload, "work_item")
        if work_item != contract["work_item"]:
            raise RuntimeError(
                "experiment-loop trial work_item does not match its contract"
            )
        for field_name in ("hypothesis", "single_change", "expected_result"):
            self._experiment_string(payload, field_name)
        raw = path.read_bytes()
        return {
            "kind": "trial",
            "work_item": work_item,
            "worker_id": agent.agent_id,
            "contract_sha256": contract_sha,
            "sha256": hashlib.sha256(raw).hexdigest(),
            "bytes": len(raw),
            "payload": payload,
            "source_path": str(path),
        }

    def _validated_experiment_loop_artifacts(
        self,
        agent: AgentSpec,
        workspace: Workspace,
        turn_paths: Set[str],
    ) -> List[Dict[str, Any]]:
        prefix = f"{self.artifact_staging_prefix}/experiment-loop/"
        paths = [
            path for path in sorted(turn_paths)
            if path.startswith(prefix) and path.endswith(".json")
        ]
        if not paths:
            return []
        records: List[Dict[str, Any]] = []
        for relative in paths:
            path = workspace.cwd / relative
            subpath = relative[len(prefix):]
            if subpath.startswith("contracts/"):
                if agent.agent_id not in _supervisor_ids(self.topology):
                    raise RuntimeError(
                        "only a worker's supervisor may author experiment contracts"
                    )
                records.append(self._validate_experiment_contract(agent, path))
            elif subpath.startswith("trials/"):
                if agent.agent_id not in self.topology.worker_ids:
                    raise RuntimeError(
                        "only workers may author experiment trial intents"
                    )
                records.append(self._validate_experiment_trial(agent, path))
            else:
                raise RuntimeError(
                    "experiment-loop JSON must be under contracts/ or trials/"
                )
        if sum(record["kind"] == "contract" for record in records) > 1:
            raise RuntimeError("a manager turn may author only one loop contract")
        if sum(record["kind"] == "trial" for record in records) > 1:
            raise RuntimeError("a worker turn may author only one trial intent")
        return records

    def _register_experiment_contract(
        self,
        record: Dict[str, Any],
        *,
        candidate: str,
        round_number: int,
    ) -> None:
        source_path = Path(str(record["source_path"]))
        raw = source_path.read_bytes()
        if hashlib.sha256(raw).hexdigest() != record["sha256"]:
            raise RuntimeError(
                f"experiment contract changed after validation: {source_path}"
            )
        with self._experiment_loop_lock:
            previous = self.experiment_contract_by_work_item.get(
                record["work_item"]
            )
            if previous and previous != record["sha256"]:
                raise RuntimeError(
                    "an experiment work item may bind only one immutable "
                    "contract; use a new workItem for a revised contract"
                )
            persisted = {
                **record,
                "candidate": candidate,
                "round": round_number,
                "registered_at": _utc_now(),
                "status": "registered",
                "activation_baseline_candidate": None,
                "goal_sha256": None,
            }
            persisted.pop("source_path", None)
            destination_dir = self.experiment_loop_root / "contracts"
            destination_dir.mkdir(parents=True, exist_ok=True)
            destination = destination_dir / f"{record['sha256']}.json"
            if not destination.exists():
                destination.write_bytes(raw)
                destination.chmod(0o444)
            persisted["persisted_path"] = str(destination)
            self.experiment_contracts[record["sha256"]] = persisted
            self.experiment_contract_by_work_item[
                record["work_item"]
            ] = record["sha256"]
        self._append_jsonl(
            "experiment-loop/contracts.jsonl",
            {
                **persisted,
                "action": "registered",
                "ts": _utc_now(),
            },
        )
        self._event(
            "experiment_loop.contract_registered",
            round_number,
            contract_sha256=record["sha256"],
            work_item=record["work_item"],
            manager_id=record["manager_id"],
            worker_id=record["worker_id"],
            mutable_file=record["mutable_file"],
            evaluator_id=record["evaluator_id"],
        )

    def _activate_experiment_contract(
        self,
        *,
        manager_id: str,
        worker_id: str,
        work_item: str,
        round_number: int,
    ) -> Optional[str]:
        contract_sha = self.experiment_contract_by_work_item.get(work_item)
        if not contract_sha:
            return None
        contract = self.experiment_contracts[contract_sha]
        if (
            contract["manager_id"] != manager_id
            or contract["worker_id"] != worker_id
        ):
            raise RuntimeError(
                "experiment-loop assignment does not match its contract"
            )
        if contract.get("status") == "halted":
            raise RuntimeError(
                "a halted experiment contract cannot be reactivated; "
                "manager judgment must create a new workItem and contract"
            )
        if contract["max_trials"] > self._experiment_remaining():
            raise RuntimeError(
                "experiment contract trial cap exceeds the remaining "
                "organization budget"
            )
        goal = self.worker_goals.get(worker_id)
        if not self._goal_is_active(goal):
            raise RuntimeError(
                "experiment contract activation requires the worker's current "
                "active goal"
            )
        if (
            goal.get("work_item") != work_item
            or goal.get("objective") != contract.get("objective")
        ):
            raise RuntimeError(
                "experiment contract objective must exactly match the stated "
                "current worker goal"
            )
        evaluator = self.experiment_policy["evaluators"][
            contract["evaluator_id"]
        ]
        if (
            goal.get("progress_evaluator_id") != contract["evaluator_id"]
            or goal.get("predicate_id")
            not in evaluator.get("predicates", {})
        ):
            raise RuntimeError(
                "experiment contract evaluator must equal the trusted "
                "predicate-bound evaluator captured for the active goal"
            )
        if (
            self.experiment_policy.get(
                "promotion_requires_goal_progress",
                False,
            )
            and contract.get("goal_success_rule")
            != evaluator.get("goal_success_rule")
        ):
            raise RuntimeError(
                "experiment contract is not bound to the project-defined "
                "success rule for the stated current goal"
            )
        active_contracts = [
            other
            for other in self.experiment_contracts.values()
            if other.get("status") == "active"
            and other.get("sha256") != contract_sha
        ]
        if active_contracts:
            raise RuntimeError(
                "only one autonomous experiment loop may be active at a time; "
                f"current contract={active_contracts[0]['sha256']}"
            )
        baseline = _git(
            self.workspaces[worker_id].cwd,
            ["rev-parse", "HEAD"],
        ).strip()
        with self._experiment_loop_lock:
            active = self.active_experiment_by_worker.get(worker_id)
            if active and active != contract_sha:
                active_contract = self.experiment_contracts.get(active, {})
                if active_contract.get("status") == "active":
                    raise RuntimeError(
                        f"{worker_id} already has an active experiment contract"
                    )
            contract["status"] = "active"
            if not contract.get("activation_baseline_candidate"):
                contract["activation_baseline_candidate"] = baseline
                self._experiment_contract_started[contract_sha] = (
                    time.monotonic()
                )
            contract["goal_sha256"] = goal["goal_sha256"]
            self.active_experiment_by_worker[worker_id] = contract_sha
            self.experiment_halted_workers.discard(worker_id)
            goal["progress_contract_sha256"] = contract_sha
            goal["progress_evaluator_id"] = contract["evaluator_id"]
            goal["progress_success_rule"] = contract["goal_success_rule"]
            goal["updated_round"] = round_number
            self._persist_goal_state()
        self._append_jsonl(
            "experiment-loop/contracts.jsonl",
            {
                **contract,
                "action": "activated",
                "activation_round": round_number,
                "ts": _utc_now(),
            },
        )
        self._event(
            "experiment_loop.contract_activated",
            round_number,
            contract_sha256=contract_sha,
            work_item=work_item,
            manager_id=manager_id,
            worker_id=worker_id,
            baseline_candidate=contract["activation_baseline_candidate"],
            goal_sha256=contract["goal_sha256"],
            evaluator_id=contract["evaluator_id"],
            goal_success_rule=contract["goal_success_rule"],
        )
        return contract_sha

    def _experiment_environment(
        self,
        workspace: Workspace,
        *,
        result_path: Optional[Path],
        max_threads: int,
    ) -> Dict[str, str]:
        env = os.environ.copy()
        bridge_bin = workspace.cwd / ".venv" / "bin"
        if ".venv" in workspace.runtime_paths and bridge_bin.is_dir():
            env["PATH"] = (
                f"{bridge_bin}{os.pathsep}{env.get('PATH', '')}"
            )
            env["VIRTUAL_ENV"] = str(workspace.cwd / ".venv")
        python_paths = [str(workspace.cwd / "src"), str(workspace.cwd)]
        if env.get("PYTHONPATH"):
            python_paths.append(env["PYTHONPATH"])
        env["PYTHONPATH"] = os.pathsep.join(python_paths)
        env.update(workspace.environment)
        env["RECCLI_EXPERIMENT_RUN_ID"] = self.run_id
        if result_path is not None:
            env["RECCLI_EXPERIMENT_RESULT_PATH"] = str(result_path)
        thread_limit = str(max_threads)
        for name in (
            "OMP_NUM_THREADS",
            "OPENBLAS_NUM_THREADS",
            "MKL_NUM_THREADS",
            "NUMEXPR_NUM_THREADS",
            "VECLIB_MAXIMUM_THREADS",
        ):
            env[name] = thread_limit
        env["PYTHONHASHSEED"] = "0"
        return env

    @staticmethod
    def _experiment_resource_envelope(
        evaluator: Dict[str, Any],
    ) -> Dict[str, Any]:
        """Describe the enforceable same-host runtime envelope.

        This is deliberately not called hardware normalization: it detects a
        host/runtime change and fixes common numerical thread pools, but it
        does not make different CPUs, accelerators, or kernels equivalent.
        """
        limits = evaluator["resource_limits"]
        payload = {
            "system": platform.system(),
            "release": platform.release(),
            "machine": platform.machine(),
            "processor": platform.processor(),
            "logical_cpu_count": os.cpu_count(),
            "python_implementation": platform.python_implementation(),
            "python_version": platform.python_version(),
            "python_executable": str(Path(sys.executable).resolve()),
            "max_threads": limits["max_threads"],
            "python_hash_seed": 0,
        }
        canonical = json.dumps(
            payload,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
        ).encode("ascii")
        return {
            "fingerprint": payload,
            "sha256": hashlib.sha256(canonical).hexdigest(),
            "same_host_required": limits["same_host_required"],
            "scope": "same-host runtime envelope; not cross-hardware equivalence",
        }

    def _run_experiment_evaluator(
        self,
        contract: Dict[str, Any],
        *,
        candidate: str,
        label: str,
        round_number: int,
    ) -> Dict[str, Any]:
        if self.experiment_policy is None:
            raise RuntimeError("experiment evaluator policy is not configured")
        evaluator = self.experiment_policy["evaluators"][
            contract["evaluator_id"]
        ]
        workspace = self.workspaces[contract["worker_id"]]
        sequence = 1 + sum(
            trial["contract_sha256"] == contract["sha256"]
            for trial in self.experiment_trials
        )
        log_root = (
            self.experiment_loop_root
            / "logs"
            / contract["sha256"][:16]
            / f"{sequence:03d}-{_safe_name(label)}"
        )
        log_root.mkdir(parents=True, exist_ok=False)
        result_path = (
            log_root / "result.json"
            if evaluator["result_mode"] == "json_file"
            else None
        )
        resource_envelope = self._experiment_resource_envelope(evaluator)
        expected_resource_sha = self.experiment_resource_fingerprints.get(
            contract["sha256"]
        )
        resource_error: Optional[str] = None
        if expected_resource_sha is None:
            self.experiment_resource_fingerprints[
                contract["sha256"]
            ] = resource_envelope["sha256"]
        elif (
            evaluator["resource_limits"]["same_host_required"]
            and expected_resource_sha != resource_envelope["sha256"]
        ):
            resource_error = (
                "same-host experiment resource fingerprint changed: "
                f"expected {expected_resource_sha}, got "
                f"{resource_envelope['sha256']}"
            )
        environment = self._experiment_environment(
            workspace,
            result_path=result_path,
            max_threads=evaluator["resource_limits"]["max_threads"],
        )
        command_records: List[Dict[str, Any]] = []
        started = time.monotonic()
        timed_out = False
        for index, command in enumerate(evaluator["commands"], 1):
            if resource_error:
                break
            command_started = time.monotonic()
            stdout = ""
            stderr = ""
            returncode: Optional[int] = None
            try:
                completed = subprocess.run(
                    command["argv"],
                    cwd=workspace.cwd,
                    env=environment,
                    capture_output=True,
                    text=True,
                    timeout=command["timeout_seconds"],
                    check=False,
                )
                returncode = completed.returncode
                stdout = completed.stdout or ""
                stderr = completed.stderr or ""
            except subprocess.TimeoutExpired as exc:
                timed_out = True
                stdout = str(exc.stdout or "")
                stderr = str(exc.stderr or "")
            stdout_path = log_root / f"command-{index:02d}.stdout.txt"
            stderr_path = log_root / f"command-{index:02d}.stderr.txt"
            stdout_bytes = stdout[-1_000_000:].encode(
                "utf-8", errors="replace",
            )
            stderr_bytes = stderr[-1_000_000:].encode(
                "utf-8", errors="replace",
            )
            stdout_path.write_bytes(stdout_bytes)
            stderr_path.write_bytes(stderr_bytes)
            command_records.append({
                "argv": list(command["argv"]),
                "timeout_seconds": command["timeout_seconds"],
                "returncode": returncode,
                "timed_out": returncode is None,
                "duration_ms": int(
                    (time.monotonic() - command_started) * 1000
                ),
                "stdout_path": str(stdout_path),
                "stderr_path": str(stderr_path),
                "stdout_bytes": len(stdout_bytes),
                "stderr_bytes": len(stderr_bytes),
                "stdout_sha256": hashlib.sha256(stdout_bytes).hexdigest(),
                "stderr_sha256": hashlib.sha256(stderr_bytes).hexdigest(),
                "stdout_tail": stdout[-4_000:],
                "stderr_tail": stderr[-4_000:],
            })
            if returncode is None:
                break
        commands_pass = bool(command_records) and all(
            record["returncode"] == 0 for record in command_records
        ) and len(command_records) == len(evaluator["commands"])
        hard_gates: Dict[str, bool]
        metrics: Dict[str, float]
        notes: List[str] = []
        result_sha256: Optional[str] = None
        result_error: Optional[str] = resource_error
        if evaluator["result_mode"] == "command_exit":
            hard_gates = {"commands_pass": commands_pass}
            metrics = {}
        else:
            hard_gates = {}
            metrics = {}
            if result_error:
                pass
            elif result_path is None or not result_path.is_file():
                result_error = (
                    "immutable evaluator did not write "
                    "RECCLI_EXPERIMENT_RESULT_PATH"
                )
            else:
                raw = result_path.read_bytes()
                result_sha256 = hashlib.sha256(raw).hexdigest()
                try:
                    result_payload = json.loads(raw.decode("utf-8"))
                except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                    result_error = f"project experiment result is invalid: {exc}"
                else:
                    if (
                        not isinstance(result_payload, dict)
                        or result_payload.get("schema")
                        != PROJECT_EXPERIMENT_RESULT_SCHEMA
                    ):
                        result_error = (
                            "project experiment result has the wrong schema"
                        )
                    else:
                        raw_gates = result_payload.get("hard_gates")
                        raw_metrics = result_payload.get("metrics")
                        raw_notes = result_payload.get("notes", [])
                        if (
                            not isinstance(raw_gates, dict)
                            or set(raw_gates) != set(evaluator["hard_gates"])
                            or any(
                                not isinstance(value, bool)
                                for value in raw_gates.values()
                            )
                        ):
                            result_error = (
                                "project experiment result hard_gates do not "
                                "match the immutable evaluator profile"
                            )
                        elif (
                            not isinstance(raw_metrics, dict)
                            or set(raw_metrics)
                            != {
                                metric["id"]
                                for metric in evaluator["metrics"]
                            }
                            or any(
                                isinstance(value, bool)
                                or not isinstance(value, (int, float))
                                or not math.isfinite(float(value))
                                for value in raw_metrics.values()
                            )
                        ):
                            result_error = (
                                "project experiment result metrics do not "
                                "match the immutable evaluator profile"
                            )
                        elif (
                            not isinstance(raw_notes, list)
                            or any(
                                not isinstance(value, str)
                                for value in raw_notes
                            )
                        ):
                            result_error = (
                                "project experiment result notes are invalid"
                            )
                        else:
                            hard_gates = dict(raw_gates)
                            metrics = {
                                key: float(value)
                                for key, value in raw_metrics.items()
                            }
                            notes = list(raw_notes)
        evaluator_mutations = sorted(self._uncommitted_paths(workspace))
        if evaluator_mutations:
            result_error = (
                "immutable evaluator modified the worker worktree: "
                f"{evaluator_mutations}"
            )
        outcome = {
            "candidate": candidate,
            "label": label,
            "evaluator_id": evaluator["id"],
            "commands_pass": commands_pass,
            "timed_out": timed_out,
            "hard_gates": hard_gates,
            "metrics": metrics,
            "notes": notes,
            "result_sha256": result_sha256,
            "result_error": result_error,
            "commands": command_records,
            "resource_envelope": resource_envelope,
            "duration_ms": int((time.monotonic() - started) * 1000),
            "evaluated_at": _utc_now(),
        }
        self._event(
            "experiment_loop.evaluator_completed",
            round_number,
            contract_sha256=contract["sha256"],
            work_item=contract["work_item"],
            worker_id=contract["worker_id"],
            candidate=candidate,
            label=label,
            commands_pass=commands_pass,
            timed_out=timed_out,
            result_error=result_error,
        )
        return outcome

    @staticmethod
    def _experiment_outcome_passes(outcome: Dict[str, Any]) -> bool:
        return bool(
            outcome.get("commands_pass")
            and not outcome.get("result_error")
            and outcome.get("hard_gates")
            and all(outcome["hard_gates"].values())
        )

    def _experiment_verdict(
        self,
        contract: Dict[str, Any],
        challenger: Dict[str, Any],
        champion: Dict[str, Any],
    ) -> str:
        if challenger.get("timed_out") or challenger.get("result_error"):
            return "crash"
        challenger_passes = self._experiment_outcome_passes(challenger)
        champion_passes = self._experiment_outcome_passes(champion)
        if not challenger_passes:
            return "discard"
        if not champion_passes:
            return "keep"
        evaluator = self.experiment_policy["evaluators"][
            contract["evaluator_id"]
        ]
        if not evaluator["metrics"]:
            return "inconclusive"
        improved = False
        worsened = False
        for metric in evaluator["metrics"]:
            identifier = metric["id"]
            challenger_value = challenger["metrics"][identifier]
            champion_value = champion["metrics"][identifier]
            tolerance = metric["tolerance"]
            delta = challenger_value - champion_value
            if metric["direction"] == "maximize":
                improved = improved or delta > tolerance
                worsened = worsened or delta < -tolerance
            else:
                improved = improved or delta < -tolerance
                worsened = worsened or delta > tolerance
        if improved and not worsened:
            return "keep"
        if worsened and not improved:
            return "discard"
        return "inconclusive"

    def _candidate_goal_progress_verdict(
        self,
        candidate: str,
        *,
        round_number: int,
    ) -> Dict[str, Any]:
        """Bind promotion retention to measured progress on the stated goal."""
        required = bool(
            self.experiment_policy
            and self.experiment_policy.get(
                "promotion_requires_goal_progress",
                False,
            )
        )
        known_goals = {
            str(goal.get("goal_sha256")): goal
            for goal in [
                *self.worker_goal_history,
                *self.worker_goals.values(),
            ]
            if goal.get("goal_sha256")
        }
        qualifying_trials: List[Dict[str, Any]] = []
        retained_trials = [
            trial
            for trial in self.experiment_trials
            if trial.get("verdict") == "keep"
        ]
        workspace = (
            self.workspaces.get(self.topology.finalizer_id)
            or next(iter(self.workspaces.values()))
        )
        for trial in retained_trials:
            contract = self.experiment_contracts.get(
                str(trial.get("contract_sha256") or ""),
                {},
            )
            goal_sha = str(contract.get("goal_sha256") or "")
            goal = known_goals.get(goal_sha)
            if (
                not goal
                or goal.get("work_item") != contract.get("work_item")
                or goal.get("objective") != contract.get("objective")
                or goal.get("progress_contract_sha256")
                != contract.get("sha256")
                or goal.get("progress_evaluator_id")
                != contract.get("evaluator_id")
                or goal.get("progress_success_rule")
                != contract.get("goal_success_rule")
            ):
                continue
            challenger = str(trial.get("challenger_candidate") or "")
            possible_ancestors = [
                challenger,
                str(self.integrated_candidates.get(challenger) or ""),
            ]
            ancestor = next(
                (
                    value
                    for value in possible_ancestors
                    if value
                    and self._host_git(
                        workspace,
                        [
                            "merge-base",
                            "--is-ancestor",
                            value,
                            candidate,
                        ],
                        check=False,
                    ).returncode
                    == 0
                ),
                None,
            )
            if ancestor:
                qualifying_trials.append({
                    "contract_sha256": contract["sha256"],
                    "goal_sha256": goal_sha,
                    "work_item": contract["work_item"],
                    "objective": contract["objective"],
                    "evaluator_id": contract["evaluator_id"],
                    "goal_success_rule": contract["goal_success_rule"],
                    "trial_number": trial.get("trial_number"),
                    "challenger_candidate": challenger,
                    "retained_ancestor": ancestor,
                    "baseline_candidate": contract.get(
                        "activation_baseline_candidate"
                    ),
                    "hard_gates": (
                        trial.get("outcome", {}).get("hard_gates")
                    ),
                    "metrics": trial.get("outcome", {}).get("metrics"),
                })
        qualifying_goal_evaluations: List[Dict[str, Any]] = []
        for record in self.goal_candidate_evaluations:
            if record.get("verdict") != "keep":
                continue
            challenger = str(record.get("candidate") or "")
            possible_ancestors = [
                challenger,
                str(self.integrated_candidates.get(challenger) or ""),
            ]
            ancestor = next(
                (
                    value
                    for value in possible_ancestors
                    if value
                    and self._host_git(
                        workspace,
                        [
                            "merge-base",
                            "--is-ancestor",
                            value,
                            candidate,
                        ],
                        check=False,
                    ).returncode
                    == 0
                ),
                None,
            )
            if ancestor:
                qualifying_goal_evaluations.append({
                    **record,
                    "retained_ancestor": ancestor,
                })
        qualifies = not required or bool(
            qualifying_trials or qualifying_goal_evaluations
        )
        verdict = {
            "schema": "reccli.organization-candidate-goal-progress.v1",
            "run_id": self.run_id,
            "candidate": candidate,
            "required": required,
            "qualifies": qualifies,
            "decision": "retain" if qualifies else "discard",
            "reason": (
                "candidate contains host-retained evaluator improvement bound "
                "to the exact stated worker goal"
                if qualifying_trials or qualifying_goal_evaluations
                else (
                    "project policy does not require goal-bound measured progress"
                    if not required
                    else
                    "no host-retained improvement bound to the exact stated "
                    "current goal is present in this candidate"
                )
            ),
            "qualifying_trials": qualifying_trials,
            "qualifying_goal_evaluations": qualifying_goal_evaluations,
            "retained_trial_count": len(retained_trials),
            "evaluated_round": round_number,
            "evaluated_at": _utc_now(),
        }
        canonical = json.dumps(
            verdict,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        ).encode("utf-8")
        verdict["verdict_sha256"] = hashlib.sha256(canonical).hexdigest()
        self.candidate_progress = verdict
        self._write_json("candidate-progress.json", verdict)
        self._event(
            "candidate.goal_progress",
            round_number,
            candidate=candidate,
            required=required,
            qualifies=qualifies,
            decision=verdict["decision"],
            qualifying_trials=len(qualifying_trials),
            qualifying_goal_evaluations=len(qualifying_goal_evaluations),
        )
        return verdict

    def _record_experiment_trial(
        self,
        contract: Dict[str, Any],
        *,
        intent: Optional[Dict[str, Any]],
        outcome: Dict[str, Any],
        verdict: str,
        round_number: int,
        resulting_head: str,
        budget_slot: Optional[int],
    ) -> Dict[str, Any]:
        if verdict not in EXPERIMENT_VERDICTS:
            raise RuntimeError(f"unsupported experiment-loop verdict: {verdict}")
        compact_outcome = {
            key: outcome.get(key)
            for key in (
                "candidate",
                "label",
                "evaluator_id",
                "commands_pass",
                "timed_out",
                "hard_gates",
                "metrics",
                "result_sha256",
                "result_error",
                "duration_ms",
                "evaluated_at",
                "resource_envelope",
                "patch_shape",
            )
        }
        compact_outcome["notes"] = [
            str(note)[:500] for note in outcome.get("notes", [])[:10]
        ]
        compact_outcome["commands"] = [
            {
                key: command.get(key)
                for key in (
                    "argv",
                    "timeout_seconds",
                    "returncode",
                    "timed_out",
                    "duration_ms",
                    "stdout_path",
                    "stderr_path",
                    "stdout_bytes",
                    "stderr_bytes",
                    "stdout_sha256",
                    "stderr_sha256",
                )
            }
            for command in outcome.get("commands", [])
        ]
        record = {
            "schema": "reccli.organization-experiment-loop-record.v1",
            "run_id": self.run_id,
            "contract_sha256": contract["sha256"],
            "work_item": contract["work_item"],
            "manager_id": contract["manager_id"],
            "worker_id": contract["worker_id"],
            "round": round_number,
            "trial_number": (
                0 if verdict == "baseline" else 1 + sum(
                    trial["contract_sha256"] == contract["sha256"]
                    and trial["verdict"] != "baseline"
                    for trial in self.experiment_trials
                )
            ),
            "intent_sha256": intent.get("sha256") if intent else None,
            "intent": intent.get("payload") if intent else None,
            "intent_persisted_path": (
                intent.get("persisted_path") if intent else None
            ),
            "challenger_candidate": outcome["candidate"],
            "resulting_head": resulting_head,
            "verdict": verdict,
            "budget_slot": budget_slot,
            "outcome": compact_outcome,
            "ts": _utc_now(),
        }
        with self._experiment_loop_lock:
            ledger_path = self.experiment_loop_root / "trials.jsonl"
            persisted_records: List[Dict[str, Any]] = []
            if ledger_path.is_file():
                try:
                    persisted_records = [
                        json.loads(line)
                        for line in ledger_path.read_text(
                            encoding="utf-8",
                        ).splitlines()
                        if line.strip()
                    ]
                except json.JSONDecodeError as exc:
                    raise RuntimeError(
                        "experiment trial ledger is not valid JSONL"
                    ) from exc
            verified, persisted_head, ledger_error = (
                verify_experiment_trial_records(persisted_records)
            )
            if not verified:
                raise RuntimeError(
                    f"experiment trial ledger verification failed: "
                    f"{ledger_error}"
                )
            if (
                len(persisted_records) != len(self.experiment_trials)
                or persisted_head != self.experiment_ledger_head_sha256
            ):
                raise RuntimeError(
                    "experiment trial ledger diverged from host memory"
                )
            record["previous_record_sha256"] = (
                self.experiment_ledger_head_sha256
            )
            canonical = json.dumps(
                record,
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=False,
            ).encode("utf-8")
            record["record_sha256"] = hashlib.sha256(canonical).hexdigest()
            self.experiment_trials.append(record)
            self.experiment_ledger_head_sha256 = record["record_sha256"]
            self._append_jsonl("experiment-loop/trials.jsonl", record)
        self._event(
            f"experiment_loop.{verdict}",
            round_number,
            contract_sha256=contract["sha256"],
            work_item=contract["work_item"],
            worker_id=contract["worker_id"],
            challenger_candidate=outcome["candidate"],
            resulting_head=resulting_head,
            trial_number=record["trial_number"],
            budget_slot=budget_slot,
        )
        return record

    def _ensure_experiment_baseline(
        self,
        agent: AgentSpec,
        round_number: int,
    ) -> None:
        contract_sha = self.active_experiment_by_worker.get(agent.agent_id)
        if not contract_sha or contract_sha in self.experiment_baselines:
            return
        contract = self.experiment_contracts[contract_sha]
        baseline = str(contract["activation_baseline_candidate"])
        head = _git(
            self.workspaces[agent.agent_id].cwd,
            ["rev-parse", "HEAD"],
        ).strip()
        if head != baseline:
            raise RuntimeError(
                "experiment-loop baseline must run before the worker changes HEAD"
            )
        goal = self.worker_goals.get(agent.agent_id, {})
        goal_baseline = self.goal_baselines.get(
            str(goal.get("goal_sha256") or ""),
        )
        if (
            goal_baseline is not None
            and goal.get("baseline_candidate") == baseline
            and goal.get("progress_evaluator_id") == contract["evaluator_id"]
        ):
            outcome = goal_baseline
        else:
            outcome = self._run_experiment_evaluator(
                contract,
                candidate=baseline,
                label="baseline",
                round_number=round_number,
            )
        self.experiment_baselines[contract_sha] = outcome
        self.experiment_champions[contract_sha] = outcome
        self.experiment_non_improving[contract_sha] = 0
        self._record_experiment_trial(
            contract,
            intent=None,
            outcome=outcome,
            verdict="baseline",
            round_number=round_number,
            resulting_head=head,
            budget_slot=None,
        )
        self._system_message(
            agent.agent_id,
            "status",
            "Host baseline completed for experiment contract "
            f"{contract_sha}. commands_pass={outcome['commands_pass']} "
            f"hard_gates={outcome['hard_gates']} metrics={outcome['metrics']}. "
            "This control run did not consume a challenger slot.",
            round_number,
            baseline,
            contract["work_item"],
            "routine",
        )

    def _halt_experiment_loop(
        self,
        contract: Dict[str, Any],
        *,
        reason: str,
        round_number: int,
        candidate: str,
    ) -> None:
        worker_id = contract["worker_id"]
        contract["status"] = "halted"
        contract["halt_reason"] = reason
        contract["halted_at"] = _utc_now()
        self.experiment_halted_workers.add(worker_id)
        self.active_experiment_by_worker.pop(worker_id, None)
        self._append_jsonl(
            "experiment-loop/contracts.jsonl",
            {
                **contract,
                "action": "halted",
                "reason": reason,
                "ts": _utc_now(),
            },
        )
        self._system_message(
            contract["manager_id"],
            "status",
            "Experiment loop stopped and requires manager judgment. "
            f"contract={contract['sha256']} workItem={contract['work_item']} "
            f"worker={worker_id} candidate={candidate} reason={reason}. "
            f"Review {self.experiment_loop_root / 'trials.jsonl'}; consult "
            "another manager directly if the decision crosses a subsystem.",
            round_number,
            candidate,
            contract["work_item"],
            "high",
        )
        self._event(
            "experiment_loop.manager_wake",
            round_number,
            contract_sha256=contract["sha256"],
            work_item=contract["work_item"],
            worker_id=worker_id,
            manager_id=contract["manager_id"],
            candidate=candidate,
            reason=reason,
        )

    def _experiment_patch_shape(
        self,
        contract: Dict[str, Any],
        challenger: str,
    ) -> Dict[str, Any]:
        workspace = self.workspaces[contract["worker_id"]]
        parent = _git(
            workspace.cwd,
            ["rev-parse", f"{challenger}^"],
        ).strip()
        numstat = _git(
            workspace.cwd,
            ["diff", "--numstat", parent, challenger, "--"],
        )
        added = 0
        deleted = 0
        binary = False
        for line in numstat.splitlines():
            fields = line.split("\t", 2)
            if len(fields) < 2 or "-" in fields[:2]:
                binary = True
                continue
            added += int(fields[0])
            deleted += int(fields[1])
        patch = _git(
            workspace.cwd,
            ["diff", "--unified=0", "--no-ext-diff", parent, challenger, "--"],
        )
        diff_hunks = sum(
            1 for line in patch.splitlines() if line.startswith("@@")
        )
        limits = self.experiment_policy["evaluators"][
            contract["evaluator_id"]
        ]["change_limits"]
        changed_lines = added + deleted
        violations: List[str] = []
        if binary:
            violations.append("binary changes are not allowed")
        if changed_lines > limits["max_changed_lines"]:
            violations.append(
                f"changed lines {changed_lines} exceed "
                f"{limits['max_changed_lines']}"
            )
        if diff_hunks > limits["max_diff_hunks"]:
            violations.append(
                f"diff hunks {diff_hunks} exceed "
                f"{limits['max_diff_hunks']}"
            )
        return {
            "parent": parent,
            "candidate": challenger,
            "added_lines": added,
            "deleted_lines": deleted,
            "changed_lines": changed_lines,
            "diff_hunks": diff_hunks,
            "binary": binary,
            "limits": dict(limits),
            "passes": not violations,
            "violations": violations,
            "scope": (
                "mechanical atomicity bound; does not prove one semantic idea"
            ),
        }

    def _experiment_rejected_outcome(
        self,
        contract: Dict[str, Any],
        *,
        candidate: str,
        label: str,
        patch_shape: Dict[str, Any],
    ) -> Dict[str, Any]:
        evaluator = self.experiment_policy["evaluators"][
            contract["evaluator_id"]
        ]
        return {
            "candidate": candidate,
            "label": label,
            "evaluator_id": evaluator["id"],
            "commands_pass": False,
            "timed_out": False,
            "hard_gates": {},
            "metrics": {},
            "notes": [],
            "result_sha256": None,
            "result_error": (
                "experiment challenger violates the bounded patch shape: "
                + "; ".join(patch_shape["violations"])
            ),
            "commands": [],
            "patch_shape": patch_shape,
            "resource_envelope": self._experiment_resource_envelope(evaluator),
            "duration_ms": 0,
            "evaluated_at": _utc_now(),
        }

    def _process_experiment_trial(
        self,
        agent: AgentSpec,
        record: Dict[str, Any],
        *,
        challenger: str,
        round_number: int,
    ) -> str:
        contract = self.experiment_contracts[record["contract_sha256"]]
        if contract["sha256"] not in self.experiment_baselines:
            raise RuntimeError(
                "experiment-loop challenger cannot run before its baseline"
            )
        intent_source = Path(str(record["source_path"]))
        intent_raw = intent_source.read_bytes()
        if hashlib.sha256(intent_raw).hexdigest() != record["sha256"]:
            raise RuntimeError(
                f"experiment trial intent changed after validation: "
                f"{intent_source}"
            )
        intent_dir = self.experiment_loop_root / "intents"
        intent_dir.mkdir(parents=True, exist_ok=True)
        intent_destination = intent_dir / f"{record['sha256']}.json"
        if not intent_destination.exists():
            intent_destination.write_bytes(intent_raw)
            intent_destination.chmod(0o444)
        record["persisted_path"] = str(intent_destination)
        budget = self._experiment_records_by_turn.get(
            (agent.agent_id, int(round_number))
        )
        trial_number = 1 + sum(
            trial["contract_sha256"] == contract["sha256"]
            and trial["verdict"] != "baseline"
            for trial in self.experiment_trials
        )
        patch_shape = self._experiment_patch_shape(contract, challenger)
        if patch_shape["passes"]:
            outcome = self._run_experiment_evaluator(
                contract,
                candidate=challenger,
                label=f"trial-{trial_number}",
                round_number=round_number,
            )
            outcome["patch_shape"] = patch_shape
        else:
            outcome = self._experiment_rejected_outcome(
                contract,
                candidate=challenger,
                label=f"trial-{trial_number}",
                patch_shape=patch_shape,
            )
        champion = self.experiment_champions[contract["sha256"]]
        verdict = self._experiment_verdict(contract, outcome, champion)
        resulting_head = challenger
        if verdict == "keep":
            self.experiment_champions[contract["sha256"]] = outcome
            self.experiment_non_improving[contract["sha256"]] = 0
        else:
            self._host_git(
                self.workspaces[agent.agent_id],
                [
                    "-c",
                    "commit.gpgsign=false",
                    "revert",
                    "--no-edit",
                    "--no-gpg-sign",
                    challenger,
                ],
            )
            resulting_head = _git(
                self.workspaces[agent.agent_id].cwd,
                ["rev-parse", "HEAD"],
            ).strip()
            self.experiment_non_improving[contract["sha256"]] = (
                self.experiment_non_improving.get(contract["sha256"], 0) + 1
            )
        trial = self._record_experiment_trial(
            contract,
            intent=record,
            outcome=outcome,
            verdict=verdict,
            round_number=round_number,
            resulting_head=resulting_head,
            budget_slot=budget.get("slot") if budget else None,
        )
        self._system_message(
            agent.agent_id,
            "status",
            "Host evaluator verdict for experiment trial "
            f"{trial['trial_number']}: {verdict}. "
            f"challenger={challenger} resulting_head={resulting_head} "
            f"hard_gates={outcome['hard_gates']} metrics={outcome['metrics']}.",
            round_number,
            resulting_head,
            contract["work_item"],
            "routine" if verdict == "keep" else "high",
        )
        trial_count = trial["trial_number"]
        elapsed = (
            time.monotonic()
            - self._experiment_contract_started.get(
                contract["sha256"],
                time.monotonic(),
            )
        )
        stop_reason: Optional[str] = None
        if trial_count >= contract["max_trials"]:
            stop_reason = f"fixed trial budget reached ({trial_count})"
        elif (
            self.experiment_non_improving.get(contract["sha256"], 0)
            >= contract["max_consecutive_non_improving"]
        ):
            stop_reason = (
                "consecutive non-improving limit reached "
                f"({self.experiment_non_improving[contract['sha256']]})"
            )
        elif elapsed >= contract["max_wall_seconds"]:
            stop_reason = (
                f"fixed wall-clock budget reached ({int(elapsed)} seconds)"
            )
        elif verdict in {"crash", "inconclusive"}:
            stop_reason = f"host evaluator returned {verdict}"
        if stop_reason:
            self._halt_experiment_loop(
                contract,
                reason=stop_reason,
                round_number=round_number,
                candidate=resulting_head,
            )
        return resulting_head

    @staticmethod
    def _resolve_reply_candidate(
        reply: Dict[str, Any],
        candidate: Optional[str],
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
            excluded_roots: Set[str] = {self.artifact_staging_prefix}
            for raw in reply.get("artifacts", []):
                supplied = Path(raw).expanduser()
                source = supplied if supplied.is_absolute() else workspace.cwd / supplied
                try:
                    relative = source.resolve().relative_to(
                        workspace.cwd.resolve(),
                    ).as_posix()
                except ValueError:
                    continue
                excluded_roots.add(relative)
            normal_paths = {
                path for path in self._uncommitted_paths(workspace)
                if not any(
                    path == root or path.startswith(root + "/")
                    for root in excluded_roots
                )
            }
            self._stage_literal_paths(workspace, normal_paths)
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

        turn_paths = self._git_paths(
            workspace,
            [
                "diff", "--cached", "--diff-filter=AMCR",
                "--name-only", "-z",
            ],
        )
        if provider_head != previous_head:
            turn_paths.update(self._git_paths(
                workspace,
                [
                    "diff", "--diff-filter=AMCR", "--name-only", "-z",
                    f"{previous_head}..{provider_head}",
                ],
            ))
        experiment_loop_artifacts = (
            self._validated_experiment_loop_artifacts(
                agent,
                workspace,
                turn_paths,
            )
        )
        trial_records = [
            record for record in experiment_loop_artifacts
            if record["kind"] == "trial"
        ]
        active_contract_sha = self.active_experiment_by_worker.get(
            agent.agent_id
        )
        if active_contract_sha and provider_head != previous_head:
            raise RuntimeError(
                "an active experiment trial must leave Git history to the "
                "RecCli host; provider-authored commits are not allowed"
            )
        normal_turn_paths = {
            path for path in turn_paths if not self._artifact_path(path)
        }
        if active_contract_sha and normal_turn_paths:
            contract = self.experiment_contracts[active_contract_sha]
            if len(trial_records) != 1:
                raise RuntimeError(
                    "an active experiment-loop change requires exactly one "
                    "structured trial intent"
                )
            if normal_turn_paths != {contract["mutable_file"]}:
                raise RuntimeError(
                    "experiment-loop trials may change exactly one tracked "
                    f"file: {contract['mutable_file']}; got "
                    f"{sorted(normal_turn_paths)}"
                )
        elif trial_records:
            raise RuntimeError(
                "an experiment-loop trial intent requires one change to its "
                "single mutable tracked file"
            )
        experiment_paths = self._metered_experiment_paths(
            agent, turn_paths,
        )
        if experiment_paths:
            self._claim_experiment_slot(
                agent,
                round_number,
                kind="git-backed-probe-or-data",
                candidate=None,
                paths=experiment_paths,
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

        for record in experiment_loop_artifacts:
            if record["kind"] == "contract":
                self._register_experiment_contract(
                    record,
                    candidate=head,
                    round_number=round_number,
                )
        if experiment_paths:
            self._claim_experiment_slot(
                agent,
                round_number,
                kind="git-backed-probe-or-data",
                candidate=head,
                paths=experiment_paths,
            )
        if trial_records:
            head = self._process_experiment_trial(
                agent,
                trial_records[0],
                challenger=head,
                round_number=round_number,
            )
        candidate_record = self._candidate_record(workspace, head)
        is_implementation = candidate_record["kind"] == "implementation"
        is_terminal_report = reply.get("disposition") in {
            "no_promotion",
            "pending_human",
        }
        self._resolve_reply_candidate(
            reply,
            head if is_implementation or is_terminal_report else None,
            previous_heads={previous_head, provider_head},
            rewrite_previous_candidates=(
                is_implementation
                and
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
                candidate_kind=candidate_record["kind"],
                changed_paths=candidate_record["paths"],
                experiment_paths=experiment_paths,
            )
            self._warn_invalid_gate_proposal(
                agent, workspace, head, round_number,
            )
            self._append_jsonl("candidates.jsonl", {
                "runId": self.run_id,
                "round": round_number,
                "agentId": agent.agent_id,
                **candidate_record,
                "empty": create_empty_identity,
                "experiment_paths": experiment_paths,
                "ts": _utc_now(),
            })
        return head

    def _sync_reviewed_candidates(
        self,
        round_number: int,
    ) -> None:
        """Apply already-reviewed candidates in the integration worktree.

        Native provider sandboxes deliberately protect Git administrative
        files.  Git mutation therefore belongs to the trusted RecCli host, not
        to Claude or Codex.  Integration keys off the durable assignment
        ledger, not the finalizer's inbox: the inbox-only finalizer consumes
        its inbox the round a handoff arrives, one round before the review
        completes, so an inbox scan could only ever integrate a candidate the
        worker happened to re-send. Routing and veto checks already ran when
        the assignment was created and reviewed.
        """
        finalizer_id = self.topology.finalizer_id
        workspace = self.workspaces[finalizer_id]
        for assignment in list(self.governance.assignments.values()):
            if assignment.get("status") not in {"approved", "reviewed"}:
                continue
            candidate = str(assignment.get("candidate") or "")
            if not candidate or candidate in self.integrated_candidates:
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
        if agent.agent_id in self.topology.worker_ids:
            try:
                self._ensure_experiment_baseline(agent, round_number)
            except Exception as exc:
                contract_sha = self.active_experiment_by_worker.get(
                    agent.agent_id
                )
                if contract_sha:
                    contract = self.experiment_contracts[contract_sha]
                    candidate = _git(
                        self.workspaces[agent.agent_id].cwd,
                        ["rev-parse", "HEAD"],
                    ).strip()
                    self._halt_experiment_loop(
                        contract,
                        reason=f"baseline evaluator infrastructure failure: {exc}",
                        round_number=round_number,
                        candidate=candidate,
                    )
                raise
        # Keep the inbox durable until a provider turn completes. A timeout,
        # quota error, or malformed reply must not silently consume messages.
        inbox = list(self.inboxes[agent.agent_id])
        previous_head = _git(
            self.workspaces[agent.agent_id].cwd,
            ["rev-parse", "HEAD"],
        ).strip()
        first_turn = (
            agent.fresh_session
            or agent.agent_id not in self.prompt_bootstrapped
        )
        prompt_state = self._model_prompt_state(agent.agent_id)
        prompt = self._build_prompt(
            agent, inbox, round_number, first_turn,
        )
        host_state_sha256 = self.host_state_brief.get("content_sha256")
        session = (
            None
            if agent.fresh_session
            else self.sessions.get(agent.agent_id)
        )
        if session is None:
            provider = self.provider_by_agent[agent.agent_id]
            session = SubscriptionSession(
                provider, self.workspaces[agent.agent_id], agent.writable,
                agent.agent_id, self.run_dir, self.model, agent.reasoning,
                fresh=agent.fresh_session,
                web_research=agent.web_research,
            )
            if agent.fresh_session:
                session.turn = max(0, round_number - 1)
            if not agent.fresh_session:
                self.sessions[agent.agent_id] = session
        result = session.run(prompt, AGENT_REPLY_SCHEMA, self.turn_timeout_seconds)
        # A provider that failed before accepting the turn must receive the
        # complete bootstrap again on retry. Once a native turn returned, its
        # resumable session retains the static contract even if reply
        # validation or host materialization subsequently rejects the turn.
        if not agent.fresh_session:
            self.prompt_bootstrapped.add(agent.agent_id)
            self.model_prompt_state_by_agent[agent.agent_id] = prompt_state
        reply = validate_agent_reply(result["value"])
        if (
            agent.agent_id != self.topology.finalizer_id
            and reply["disposition"] != "continue"
            and not (
                agent.agent_id == self.topology.leader_id
                and reply["disposition"] == "no_op"
            )
        ):
            raise ValueError(
                f"{agent.agent_id} is not the finalizer and must use "
                "disposition=continue"
            )
        session.record_reply_disposition(reply)
        self._materialize_agent_candidate(
            agent, reply, previous_head, round_number,
        )
        self.inboxes[agent.agent_id] = []
        return {
            "agent": agent, "reply": reply, "usage": result.get("usage", {}),
            "provider": session.provider,
            "session_id": result.get("session_id"),
            "duration_ms": int((time.monotonic() - started) * 1000),
            "prompt_chars": len(prompt),
            "prompt_mode": "bootstrap" if first_turn else "incremental",
            "inbox_count": len(inbox),
            "host_state_sha256": host_state_sha256,
        }

    def _scientific_review_context(
        self, inbox: List[Dict[str, Any]], max_chars: int = 8_000,
    ) -> str:
        if self.topology.review_policy != "veto" or not any(
            message.get("tag") == "review" for message in inbox
        ):
            return ""
        candidates = {
            message.get("candidate") for message in inbox
            if message.get("tag") == "review" and message.get("candidate")
        }
        work_items = {
            message.get("workItem") for message in inbox
            if message.get("tag") == "review" and message.get("workItem")
        }
        current_messages = {
            (
                message.get("from"),
                message.get("to"),
                message.get("tag"),
                message.get("candidate"),
                message.get("workItem"),
                message.get("content"),
            )
            for message in inbox
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
                ) and record.get("tag") in {
                    "handoff", "review", "decision", "blocker",
                }:
                    identity = (
                        record.get("from"),
                        record.get("to"),
                        record.get("tag"),
                        record.get("candidate"),
                        record.get("workItem"),
                        record.get("content"),
                    )
                    if identity in current_messages:
                        continue
                    records.append({
                        "round": record.get("round"),
                        "from": record.get("from"),
                        "to": record.get("to"),
                        "tag": record.get("tag"),
                        "candidate": record.get("candidate"),
                        "workItem": record.get("workItem"),
                        "risk": record.get("risk"),
                        "content": str(record.get("content") or "")[:1_200],
                    })
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
        if not records and not bundles:
            return ""
        payload = json.dumps(
            {
                "durable_messages": str(message_path),
                "prior_decisions_and_objections": records[-12:],
                "sealed_bundles": bundles,
            },
            indent=2, ensure_ascii=False,
        )
        return payload[-max_chars:]

    def _admission_section(self, agent: AgentSpec) -> str:
        if not self.admission:
            return ""
        lead_no_op = ""
        if agent.agent_id == self.topology.leader_id:
            lead_no_op = (
                "\n\nIf the done condition is already satisfied, or any stop "
                "condition holds, reply with final=true, disposition=no_op, "
                "candidate=null, and a summary naming the condition. Stopping "
                "is a successful outcome; do not manufacture work to justify "
                "the run."
            )
        return (
            "\n## Admission contract\n\n"
            + render_admission_prompt(self.admission)
            + lead_no_op
            + "\n"
        )

    def _build_prompt(
        self, agent: AgentSpec, inbox: List[Dict[str, Any]], round_number: int, first_turn: bool,
    ) -> str:
        inbox_text = "\n".join(
            f"{index}. From {message['from']} [{message['tag']}]: {message['content']}"
            + (f" candidate={message.get('candidate')} work={message.get('workItem')} risk={message.get('risk')}" if message.get("candidate") else "")
            for index, message in enumerate(inbox, 1)
        )
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
        state = self._model_prompt_state(agent.agent_id)
        if not first_turn:
            previous = self.model_prompt_state_by_agent.get(agent.agent_id)
            sections: List[str] = []
            if previous is None or state["host"] != previous.get("host"):
                if state["host"]:
                    sections.append(
                        "## Changed host decision state\n\n" + state["host"]
                    )
            if previous is None or state["goal"] != previous.get("goal"):
                if state["goal"]:
                    sections.append("## Changed goal state\n\n" + state["goal"])
                elif previous and previous.get("goal"):
                    sections.append("## Changed goal state\n\nNo active goal.")
            if (
                state["active_experiment_sha256"]
                and (
                    previous is None
                    or state["active_experiment_sha256"]
                    != previous.get("active_experiment_sha256")
                )
            ):
                sections.append(
                    "## Activated experiment\n\n"
                    + self._active_experiment_prompt(agent.agent_id)
                )
            review_context = self._scientific_review_context(inbox)
            if review_context:
                sections.append(
                    "## Prior review evidence\n\n" + review_context
                )
            if inbox_text:
                sections.append("## New inbox\n\n" + inbox_text)
            if round_number > self.max_rounds:
                sections.append(
                    "## Closeout event\n\n"
                    f"Closeout {round_number - self.max_rounds}/"
                    f"{self.max_closeout_rounds}: do not start new work. "
                    "Route or review an existing exact candidate, answer a "
                    "release blocker, finalize, or return state=done."
                )
            if not sections:
                sections.append(
                    "No host, goal, inbox, review, or experiment state changed. "
                    "Continue the retained goal."
                )
            return (
                f"# RecCli delta {agent.agent_id} R{round_number}\n\n"
                + "\n\n".join(sections)
            )
        pack_note = "No project context pack is configured."
        if self.context_pack_manifest:
            pack = self.context_pack_manifest["agent_packs"][agent.agent_id]
            required_paths = "\n".join(
                f"- `{path}`" for path in pack["declared_reading_paths"]
            ) or "- none"
            pack_note = f"""Context index: `{pack['index']}`
Required foundation:
{required_paths}

Read the complete required foundation now, once for this native session.
Retrieve non-Critical entries through the index only when this goal or exact
candidate needs them. The context box is read-only."""
        evidence_note = (
            f"Evidence manifest: `{self.run_dir / 'evidence-manifest.json'}`; "
            f"immutable snapshot: `{self.evidence_manifest['snapshot_root']}`."
            if self.evidence_manifest else ""
        )
        experiment_note = self._active_experiment_prompt(agent.agent_id)
        if (
            not experiment_note
            and self.experiment_policy
            and agent.agent_id in _supervisor_ids(self.topology)
        ):
            experiment_note = (
                f"Evaluator policy: `{self.experiment_policy_path}`. "
                "Use an autonomous contract only after binding one existing "
                "goal to one declared predicate; contract details and limits "
                f"are durable in `{self.run_dir / 'run.json'}`. "
                # The exact ids, inline: a lead once bound the policy FILENAME
                # as evaluatorId, the delegation was rejected, and the round
                # was lost. Never make the model guess an identifier the host
                # already knows.
                "Declared ids (use these exact strings for evaluatorId and "
                "predicateId): " + "; ".join(
                    f"evaluator `{evaluator_id}` with predicates " + ", ".join(
                        f"`{predicate_id}`" for predicate_id in sorted(
                            profile.get("predicates") or {}
                        )
                    )
                    for evaluator_id, profile in sorted(
                        (self.experiment_policy or {})
                        .get("evaluators", {}).items()
                    )
                )
            )
        web_note = (
            "External research is available. Use primary sources only for a "
            "material current-goal question; never disclose private project "
            "content, and do not treat external evidence as project authority."
            if agent.web_research else ""
        )
        finalizer_boundary = (
            "You alone may propose a terminal disposition for an exact reviewed "
            "candidate or terminal dossier. Use final=true, risk=release, the "
            f"exact candidate marker `{HOST_CANDIDATE}`, and disposition=promote, "
            "no_promotion, or pending_human. For a no_promotion or "
            "pending_human closure, author the dossier YOURSELF as a file "
            f"under `{self.artifact_staging_prefix}/` in your own worktree "
            "(worker findings arrive in messages; restate them in the "
            "dossier), then in the same reply send a release-risk review "
            "request for the exact candidate to "
            f"{self.governance.release_reviewer_id or 'the release auditor'}, "
            "whose NO_VETO on that exact candidate must be recorded before "
            "your final reply can close the run. Canonical effects remain "
            "human-owned."
            if agent.agent_id == self.topology.finalizer_id
            else
            "Set final=false, disposition=continue, and top-level candidate/risk "
            "to null."
        )
        runtime = (
            f"Use `{workspace.cwd / '.venv/bin/python'}` for project Python."
            if ".venv" in workspace.runtime_paths else
            "Use the repository's documented runtime."
        )
        host_section = (
            f"\n## Current host decision state\n\n{state['host']}\n"
            if state["host"] else ""
        )
        goal_section = (
            f"\n## One active goal\n\n{state['goal']}\n"
            if state["goal"] else ""
        )
        inbox_section = (
            f"\n## New inbox\n\n{inbox_text}\n"
            if inbox_text else ""
        )
        review_context = self._scientific_review_context(inbox)
        review_section = (
            f"\n## Prior review evidence\n\n{review_context}\n"
            if review_context else ""
        )
        event_notes = "\n".join(
            note for note in (
                evidence_note,
                experiment_note,
                web_note,
            )
            if note
        )
        return f"""# RecCli role bootstrap: {agent.agent_id}

Run: {self.run_id}
Role: {agent.role}
Provider: {self.provider_by_agent[agent.agent_id]}
Working directory: {workspace.cwd}
Operational boundary: {write_policy}
Run artifacts: `{self.artifact_staging_prefix}/<path-relative-to-the-run-directory>`
{runtime}

## Mission

{self.mission}
{self._admission_section(agent)}
## Role authority

{agent.instructions}

## Shared foundation and on-demand context

{pack_note}
{host_section}{goal_section}{inbox_section}{review_section}
{event_notes}

Complete only the current goal or exact event. {finalizer_boundary}

Return the constrained reply. Messages require to, tag, content, candidate,
workItem, and risk; use null when inapplicable. Manager worker delegations
require goalClass, predicateId, and evaluatorId. Worker handoffs use
candidate=`{HOST_CANDIDATE}`; RecCli creates the commit."""

    @staticmethod
    def _goal_is_active(goal: Optional[Dict[str, Any]]) -> bool:
        return bool(
            goal
            and goal.get("status") not in WORKER_GOAL_TERMINAL_STATES
        )

    def _latest_off_goal_flag(
        self,
        *,
        worker_id: Optional[str] = None,
        manager_id: Optional[str] = None,
        work_item: Optional[str] = None,
        statuses: Optional[Set[str]] = None,
    ) -> Optional[Dict[str, Any]]:
        for flag in reversed(list(self.off_goal_flags.values())):
            if worker_id is not None and flag["worker_id"] != worker_id:
                continue
            if manager_id is not None and flag["manager_id"] != manager_id:
                continue
            if work_item is not None and flag["work_item"] != work_item:
                continue
            if statuses is not None and flag["status"] not in statuses:
                continue
            return flag
        return None

    def _persist_goal_state(self) -> None:
        self._write_json("goal-state.json", {
            "schema": GOAL_STATE_SCHEMA,
            "run_id": self.run_id,
            "updated_at": _utc_now(),
            "worker_goals": self.worker_goals,
            "goal_history": self.worker_goal_history,
            "off_goal_flags": list(self.off_goal_flags.values()),
        })

    @staticmethod
    def _problem_solving_goal_error(content: str) -> Optional[str]:
        normalized = " ".join(content.lower().split())
        non_work_patterns = (
            r"\bstandby\b",
            r"\bstand by\b",
            r"\bwait for (?:further|more|another) (?:work|instructions?|tasks?)\b",
            r"\bmonitor only\b",
            r"\bno action\b",
            r"\brepository census\b",
            r"\bdocumentation census\b",
            r"\bprepare (?:a |the )?(?:status |release )?(?:report|checklist|dossier)\b",
            r"\bwrite (?:a |the )?(?:status |progress )?(?:report|summary)\b",
        )
        if any(re.search(pattern, normalized) for pattern in non_work_patterns):
            return (
                "worker goals must name a concrete problem-solving outcome; "
                "standby, waiting, monitoring-only, and no-action assignments "
                "are management state, not worker goals"
            )
        return None

    def _resolve_goal_measurement(
        self,
        *,
        goal_class: Optional[str],
        predicate_id: Optional[str],
        evaluator_id: Optional[str],
    ) -> Tuple[Optional[Dict[str, Any]], str]:
        if not (
            self.experiment_policy
            and self.experiment_policy.get(
                "promotion_requires_goal_progress",
                False,
            )
        ):
            return None, ""
        resolved_goal_class = str(goal_class or "").strip()
        resolved_predicate = str(predicate_id or "").strip()
        resolved_evaluator = str(evaluator_id or "").strip()
        # Under a gate-authoring admission, any delegation that does not
        # explicitly request production_pipeline measurement binds unmeasured:
        # its measurability is the gate-proposal discrimination validator and
        # its exit is human ratification. This must run BEFORE profile
        # resolution, or an unclassed ceremony goal can auto-bind (and
        # single-owner-capture) a production predicate the run has no
        # business measuring.
        if (
            (self.admission or {}).get("work_class")
            in {"uncertainty_reduction", "hypothesis_test"}
            and resolved_goal_class != "production_pipeline"
        ):
            return None, ""
        matches: List[Tuple[Dict[str, Any], Dict[str, Any]]] = []
        for evaluator in self.experiment_policy["evaluators"].values():
            if resolved_evaluator and evaluator["id"] != resolved_evaluator:
                continue
            for predicate in evaluator.get("predicates", {}).values():
                if resolved_predicate and predicate["id"] != resolved_predicate:
                    continue
                if (
                    resolved_goal_class
                    and predicate["goal_class"] != resolved_goal_class
                ):
                    continue
                matches.append((evaluator, predicate))
        if len(matches) != 1:
            # A gate-authoring goal's purpose is to create a predicate that
            # does not exist yet, so requiring it to bind an existing one is
            # a category error: run six burned half its rounds on exactly
            # this. When the policy declares no evaluator_infrastructure
            # profile and the run's admission is a gate-authoring class, the
            # goal binds unmeasured; its measurability comes from the
            # gate-proposal validator (the discrimination proof), and its
            # exit is pending_human ratification, not promote.
            if (
                resolved_goal_class == "evaluator_infrastructure"
                and not any(
                    predicate.get("goal_class") == "evaluator_infrastructure"
                    for evaluator in self.experiment_policy[
                        "evaluators"
                    ].values()
                    for predicate in evaluator.get("predicates", {}).values()
                )
            ):
                return None, (
                    "the policy declares no evaluator_infrastructure "
                    "profile; unprofiled gate-authoring goals require an "
                    "uncertainty_reduction or hypothesis_test admission"
                )
            return None, (
                "implementation goal is unevaluable: select exactly one "
                "project-declared goalClass, predicateId, and evaluatorId; "
                f"matching profiles={len(matches)}"
            )
        evaluator, predicate = matches[0]
        return {
            "goal_class": predicate["goal_class"],
            "predicate_id": predicate["id"],
            "evaluator_id": evaluator["id"],
            "evaluator_profile_sha256": evaluator["profile_sha256"],
            "immutable_ground_truth_sha256": evaluator[
                "immutable_ground_truth_sha256"
            ],
            "comparison_rule_id": predicate["comparison_rule_id"],
            "predicate": predicate,
            "evaluator": evaluator,
        }, ""

    @staticmethod
    def _goal_predicate_value(
        predicate: Dict[str, Any],
        outcome: Dict[str, Any],
    ) -> Any:
        source = predicate["source"]
        if source == "commands_pass":
            return bool(outcome.get("commands_pass"))
        if source == "hard_gate":
            return outcome.get("hard_gates", {}).get(predicate["result_id"])
        return outcome.get("metrics", {}).get(predicate["result_id"])

    @staticmethod
    def _goal_predicate_improved(
        evaluator: Dict[str, Any],
        predicate: Dict[str, Any],
        baseline_value: Any,
        candidate_value: Any,
    ) -> bool:
        rule = predicate["comparison_rule_id"]
        if rule == "false_to_true":
            return not bool(baseline_value) and bool(candidate_value)
        if (
            isinstance(baseline_value, bool)
            or isinstance(candidate_value, bool)
            or not isinstance(baseline_value, (int, float))
            or not isinstance(candidate_value, (int, float))
        ):
            return False
        tolerance = next(
            (
                metric["tolerance"]
                for metric in evaluator["metrics"]
                if metric["id"] == predicate["result_id"]
            ),
            0.0,
        )
        delta = float(candidate_value) - float(baseline_value)
        return (
            delta > tolerance
            if rule == "maximize"
            else delta < -tolerance
        )

    def _goal_evaluator_contract(
        self,
        goal: Dict[str, Any],
    ) -> Dict[str, Any]:
        canonical = json.dumps(
            {
                "goal_sha256": goal["goal_sha256"],
                "worker_id": goal["worker_id"],
                "work_item": goal["work_item"],
                "evaluator_id": goal["progress_evaluator_id"],
                "predicate_id": goal["predicate_id"],
            },
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        ).encode("utf-8")
        return {
            "sha256": hashlib.sha256(canonical).hexdigest(),
            "worker_id": goal["worker_id"],
            "work_item": goal["work_item"],
            "evaluator_id": goal["progress_evaluator_id"],
        }

    def _persist_goal_evaluation(
        self,
        goal: Dict[str, Any],
        *,
        label: str,
        outcome: Dict[str, Any],
    ) -> Tuple[str, str]:
        root = self.run_dir / "goal-evaluations" / goal["goal_sha256"]
        root.mkdir(parents=True, exist_ok=True)
        path = root / f"{_safe_name(label)}.json"
        payload = {
            "schema": "reccli.organization-goal-evaluation.v1",
            "run_id": self.run_id,
            "goal_sha256": goal["goal_sha256"],
            "goal_class": goal["goal_class"],
            "predicate_id": goal["predicate_id"],
            "evaluator_profile_sha256": goal["evaluator_profile_sha256"],
            "immutable_ground_truth_sha256": goal[
                "immutable_ground_truth_sha256"
            ],
            "label": label,
            "outcome": outcome,
        }
        raw = (
            json.dumps(payload, indent=2, ensure_ascii=False, sort_keys=True)
            + "\n"
        ).encode("utf-8")
        path.write_bytes(raw)
        return str(path), hashlib.sha256(raw).hexdigest()

    def _capture_goal_baseline(
        self,
        goal: Dict[str, Any],
        *,
        round_number: int,
    ) -> Tuple[bool, str]:
        evaluator = self.experiment_policy["evaluators"][
            goal["progress_evaluator_id"]
        ]
        predicate = evaluator["predicates"][goal["predicate_id"]]
        workspace = self.workspaces[goal["worker_id"]]
        baseline = _git(workspace.cwd, ["rev-parse", "HEAD"]).strip()
        goal["baseline_candidate"] = baseline
        outcome = self._run_experiment_evaluator(
            self._goal_evaluator_contract(goal),
            candidate=baseline,
            label="goal-baseline",
            round_number=round_number,
        )
        result_path, result_sha = self._persist_goal_evaluation(
            goal,
            label="baseline",
            outcome=outcome,
        )
        value = self._goal_predicate_value(predicate, outcome)
        goal["baseline_value"] = value
        goal["baseline_result_sha256"] = result_sha
        goal["baseline_result_path"] = result_path
        self.goal_baselines[goal["goal_sha256"]] = outcome
        if outcome.get("timed_out") or outcome.get("result_error"):
            return False, (
                "implementation goal is unevaluable: baseline evaluator "
                f"failed ({outcome.get('result_error') or 'timeout'})"
            )
        if value is None:
            return False, (
                "implementation goal is unevaluable: evaluator did not expose "
                f"predicate {goal['predicate_id']}"
            )
        if (
            goal["comparison_rule_id"] == "false_to_true"
            and bool(value)
        ):
            return False, (
                "implementation goal is unevaluable: predicate "
                f"{goal['predicate_id']} is already satisfied at baseline"
            )
        return True, ""

    def _evaluate_bound_goal_candidate(
        self,
        *,
        worker_id: str,
        candidate: str,
        round_number: int,
    ) -> Dict[str, Any]:
        goal = self.worker_goals.get(worker_id)
        if not goal or not goal.get("predicate_id"):
            raise RuntimeError(
                f"{worker_id} has no predicate-bound measurable goal"
            )
        evaluator = self.experiment_policy["evaluators"][
            goal["progress_evaluator_id"]
        ]
        head = _git(
            self.workspaces[worker_id].cwd,
            ["rev-parse", "HEAD"],
        ).strip()
        if head != candidate:
            raise RuntimeError(
                "goal evaluator requires the worker worktree HEAD to equal "
                f"the exact candidate: head={head} candidate={candidate}"
            )
        candidate_record = self._candidate_record(
            self.workspaces[worker_id],
            candidate,
        )
        trusted_paths = [
            *evaluator["immutable_paths"],
            self.experiment_policy["source_relative"],
        ]
        overlaps = sorted({
            changed
            for changed in candidate_record.get("paths", [])
            for immutable in trusted_paths
            if (
                changed == immutable
                or changed.startswith(immutable + "/")
                or immutable.startswith(changed + "/")
            )
        })
        if overlaps:
            raise RuntimeError(
                "candidate changes its trusted evaluator or immutable ground "
                f"truth: {overlaps}"
            )
        baseline = self.goal_baselines.get(goal["goal_sha256"])
        if baseline is None:
            raise RuntimeError("goal baseline is missing")
        outcome = self._run_experiment_evaluator(
            self._goal_evaluator_contract(goal),
            candidate=candidate,
            label="goal-candidate",
            round_number=round_number,
        )
        result_path, result_sha = self._persist_goal_evaluation(
            goal,
            label=f"candidate-{candidate[:12]}",
            outcome=outcome,
        )
        predicate = evaluator["predicates"][goal["predicate_id"]]
        baseline_value = self._goal_predicate_value(predicate, baseline)
        candidate_value = self._goal_predicate_value(predicate, outcome)
        verdict = self._experiment_verdict(
            self._goal_evaluator_contract(goal),
            outcome,
            baseline,
        )
        predicate_improved = self._goal_predicate_improved(
            evaluator,
            predicate,
            baseline_value,
            candidate_value,
        )
        if verdict == "keep" and not predicate_improved:
            verdict = "inconclusive"
        record = {
            "schema": "reccli.organization-goal-candidate-evaluation.v1",
            "goal_sha256": goal["goal_sha256"],
            "goal_class": goal["goal_class"],
            "predicate_id": goal["predicate_id"],
            "worker_id": worker_id,
            "candidate": candidate,
            "baseline_candidate": goal["baseline_candidate"],
            "baseline_value": baseline_value,
            "candidate_value": candidate_value,
            "predicate_improved": predicate_improved,
            "comparison_rule_id": goal["comparison_rule_id"],
            "evaluator_profile_sha256": goal["evaluator_profile_sha256"],
            "immutable_ground_truth_sha256": goal[
                "immutable_ground_truth_sha256"
            ],
            "result_sha256": result_sha,
            "result_path": result_path,
            "verdict": verdict,
            "round": round_number,
        }
        self.goal_candidate_evaluations.append(record)
        self._event(
            f"worker.goal_candidate.{verdict}",
            round_number,
            worker_id=worker_id,
            goal_sha256=goal["goal_sha256"],
            predicate_id=goal["predicate_id"],
            candidate=candidate,
            result_sha256=result_sha,
        )
        return record

    def _bind_worker_goal(
        self,
        *,
        worker_id: str,
        manager_id: str,
        work_item: str,
        objective: str,
        risk: str,
        round_number: int,
        source: str = "manager",
        force: bool = False,
        goal_class: Optional[str] = None,
        predicate_id: Optional[str] = None,
        evaluator_id: Optional[str] = None,
    ) -> Tuple[bool, str]:
        # Resolve through the supervisor lookup rather than the manager map: a
        # flat topology has no primary manager, and comparing against None
        # rejected every assignment the coordinator made.
        primary = _supervisor_of(self.topology, worker_id)
        if not force and manager_id != primary:
            return (
                False,
                f"worker goals may be assigned only by {primary}",
            )
        goal_error = self._problem_solving_goal_error(objective)
        if goal_error:
            return False, goal_error
        measurement, measurement_error = self._resolve_goal_measurement(
            goal_class=goal_class,
            predicate_id=predicate_id,
            evaluator_id=evaluator_id,
        )
        if measurement_error:
            return False, measurement_error
        if measurement:
            owner = next(
                (
                    worker
                    for worker, existing in self.worker_goals.items()
                    if worker != worker_id
                    and self._goal_is_active(existing)
                    and existing.get("predicate_id")
                    == measurement["predicate_id"]
                ),
                None,
            )
            if owner:
                return False, (
                    f"predicate {measurement['predicate_id']} already has "
                    f"active owner {owner}"
                )

        current = self.worker_goals.get(worker_id)
        same_goal = bool(current and current.get("work_item") == work_item)
        # A rebind by the supervisor adjudicates any open flag on the current
        # goal: with no manager layer there is no peer to consult, and the
        # supervisor changing the goal IS the decision.
        open_flag = self._latest_off_goal_flag(
            worker_id=worker_id,
            work_item=current.get("work_item") if current else None,
            statuses={"raised"},
        )
        if (
            self._goal_is_active(current)
            and not same_goal
            and not force
            and open_flag is None
        ):
            return (
                False,
                f"{worker_id} already has active goal "
                f"{current.get('work_item')}; finish it or raise an off-goal "
                "flag before assigning another",
            )

        if current and not same_goal:
            archived = dict(current)
            archived["status"] = "superseded"
            archived["superseded_round"] = round_number
            archived["superseded_by"] = work_item
            self.worker_goal_history.append(archived)
            current["status"] = "superseded"
            if open_flag:
                open_flag["status"] = "acted"
                open_flag["decision_round"] = round_number
                open_flag["decision"] = f"Replaced goal with {work_item}"

        created_round = (
            current.get("created_round", round_number)
            if same_goal and current else round_number
        )
        goal = {
            "schema": "reccli.organization-worker-goal.v1",
            "worker_id": worker_id,
            "manager_id": primary or manager_id,
            "work_item": work_item,
            "objective": objective.strip(),
            "risk": risk,
            "source": source,
            "status": "active",
            "created_round": created_round,
            "updated_round": round_number,
            "candidate": None,
        }
        goal_identity = {
            key: goal[key]
            for key in (
                "worker_id",
                "manager_id",
                "work_item",
                "objective",
                "risk",
                "source",
                "created_round",
            )
        }
        goal["goal_sha256"] = hashlib.sha256(
            json.dumps(
                goal_identity,
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=False,
            ).encode("utf-8")
        ).hexdigest()
        goal["progress_contract_sha256"] = None
        goal["goal_class"] = (
            measurement["goal_class"] if measurement else None
        )
        goal["predicate_id"] = (
            measurement["predicate_id"] if measurement else None
        )
        goal["progress_evaluator_id"] = (
            measurement["evaluator_id"] if measurement else None
        )
        goal["evaluator_profile_sha256"] = (
            measurement["evaluator_profile_sha256"]
            if measurement else None
        )
        goal["immutable_ground_truth_sha256"] = (
            measurement["immutable_ground_truth_sha256"]
            if measurement else None
        )
        goal["comparison_rule_id"] = (
            measurement["comparison_rule_id"] if measurement else None
        )
        goal["progress_success_rule"] = (
            measurement["predicate"]["id"] if measurement else None
        )
        goal["baseline_candidate"] = None
        goal["baseline_value"] = None
        goal["baseline_result_sha256"] = None
        goal["baseline_result_path"] = None
        self.worker_goals[worker_id] = goal
        if measurement:
            measurable, reason = self._capture_goal_baseline(
                goal,
                round_number=round_number,
            )
            if not measurable:
                goal["status"] = "unevaluable"
                goal["updated_round"] = round_number
                goal["terminal_reason"] = reason
                self.worker_goal_history.append(dict(goal))
                self._persist_goal_state()
                self._event(
                    "worker.goal.unevaluable",
                    round_number,
                    worker_id=worker_id,
                    work_item=work_item,
                    predicate_id=goal["predicate_id"],
                    reason=reason,
                )
                return False, reason
        if open_flag and same_goal:
            open_flag["status"] = "acted"
            open_flag["decision_round"] = round_number
            open_flag["decision"] = "Kept and refined the active goal"
        self._persist_goal_state()
        self._event(
            "worker.goal.bound",
            round_number,
            worker_id=worker_id,
            manager_id=manager_id,
            work_item=work_item,
            source=source,
        )
        return True, ""

    def _record_off_goal_flag(
        self,
        *,
        worker_id: str,
        content: str,
        work_item: str,
        risk: str,
        round_number: int,
    ) -> Tuple[bool, str]:
        primary = _supervisor_of(self.topology, worker_id)
        goal = self.worker_goals.get(worker_id)
        if not self._goal_is_active(goal):
            return False, f"{worker_id} has no active goal to preserve"
        if work_item != goal.get("work_item"):
            return (
                False,
                "off-goal flags must retain the active goal workItem so the "
                "worker does not silently switch scope",
            )
        if risk != goal.get("risk"):
            return (
                False,
                "off-goal flags must retain the active goal risk so they "
                "cannot silently reprioritize the worker",
            )
        existing = self._latest_off_goal_flag(
            worker_id=worker_id,
            work_item=work_item,
            statuses={"raised"},
        )
        if existing:
            return (
                False,
                f"off-goal flag {existing['flag_id']} is already open for "
                f"{worker_id}; continue the active goal while it is resolved",
            )
        canonical = json.dumps(
            {
                "run_id": self.run_id,
                "worker_id": worker_id,
                "work_item": work_item,
                "round": round_number,
                "content": content.strip(),
            },
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        ).encode("utf-8")
        flag_id = hashlib.sha256(canonical).hexdigest()
        flag = {
            "schema": "reccli.organization-off-goal-flag.v1",
            "flag_id": flag_id,
            "worker_id": worker_id,
            "manager_id": primary,
            "work_item": work_item,
            "content": content.strip(),
            "risk": risk,
            # raised -> acted. The supervisor adjudicates directly; there is
            # no manager layer to consult.
            "status": "raised",
            "created_round": round_number,
        }
        self.off_goal_flags[flag_id] = flag
        self._persist_goal_state()
        self._event(
            "worker.off_goal.flagged",
            round_number,
            flag_id=flag_id,
            worker_id=worker_id,
            manager_id=primary,
            work_item=work_item,
        )
        return True, ""

    def _process_goal_protocol_message(
        self,
        sender: str,
        message: Dict[str, Any],
        round_number: int,
    ) -> Tuple[bool, str]:
        recipient = str(message.get("to") or "")
        tag = str(message.get("tag") or "")
        work_item = str(message.get("workItem") or "")

        if sender in self.topology.worker_ids:
            primary = _supervisor_of(self.topology, sender)
            if recipient != primary:
                return (
                    False,
                    f"worker traffic must go through {primary}",
                )
            goal = self.worker_goals.get(sender)
            if not goal:
                return (
                    False,
                    f"{sender} cannot produce organization traffic without "
                    "one active goal",
                )
            if (
                goal.get("status") in WORKER_GOAL_TERMINAL_STATES
                and tag not in {"answer", "status"}
            ):
                return (
                    False,
                    f"{sender} goal {goal.get('work_item')} is "
                    f"{goal.get('status')}; only a same-goal answer or status "
                    "is allowed until the primary manager binds another goal",
                )
            if (
                message.get("workItem")
                and work_item != goal.get("work_item")
            ):
                return (
                    False,
                    "worker messages must retain the one active goal workItem",
                )
            if tag == "flag":
                if message.get("candidate"):
                    return False, "off-goal flags cannot carry a candidate"
                return self._record_off_goal_flag(
                    worker_id=sender,
                    content=str(message.get("content") or ""),
                    work_item=work_item,
                    risk=str(message.get("risk") or "routine"),
                    round_number=round_number,
                )

        if (
            sender in _supervisor_ids(self.topology)
            and recipient in self.topology.worker_ids
            and tag == "decision"
            and work_item
        ):
            flag = self._latest_off_goal_flag(
                worker_id=recipient,
                manager_id=sender,
                work_item=work_item,
                statuses={"raised"},
            )
            if flag:
                flag["status"] = "acted"
                flag["decision"] = str(message.get("content") or "").strip()
                flag["decision_round"] = round_number
                self._persist_goal_state()
                self._event(
                    "worker.off_goal.decided",
                    round_number,
                    flag_id=flag["flag_id"],
                    manager_id=sender,
                    worker_id=recipient,
                )
        return True, ""

    def _update_worker_goal_after_reply(
        self,
        worker_id: str,
        reply: Dict[str, Any],
        round_number: int,
    ) -> None:
        goal = self.worker_goals.get(worker_id)
        if not self._goal_is_active(goal):
            return
        matching = [
            message
            for message in reply.get("messages", [])
            if message.get("workItem") == goal.get("work_item")
        ]
        primary = _supervisor_of(self.topology, worker_id)
        handoff = next(
            (
                message
                for message in self.inboxes.get(primary or "", [])
                if message.get("from") == worker_id
                and message.get("workItem") == goal.get("work_item")
                if message.get("tag") == "handoff"
                and message.get("candidate")
            ),
            None,
        )
        wake_supervisor = False
        if handoff:
            goal["status"] = "candidate_ready"
            goal["candidate"] = handoff.get("candidate")
            goal["completed_round"] = round_number
        elif reply.get("state") == "done":
            goal["status"] = "completed"
            goal["completed_round"] = round_number
            # A candidate_ready transition rides a handoff already sitting in
            # the supervisor's inbox; a completed transition has no inbox
            # event anywhere, so nothing ever schedules the supervisor to
            # route review or close. The governance close-out run finished
            # all its packet work and then stalled over it in exactly this
            # silence: worker done at 05:36:18, no event of any kind until
            # the conclusion. Completed work must wake its supervisor once.
            wake_supervisor = True
        elif any(message.get("tag") == "blocker" for message in matching):
            goal["status"] = "blocked"
            goal["updated_round"] = round_number
        else:
            return
        self._persist_goal_state()
        self._event(
            "worker.goal.status",
            round_number,
            worker_id=worker_id,
            work_item=goal.get("work_item"),
            status=goal["status"],
            candidate=goal.get("candidate"),
        )
        if wake_supervisor and primary:
            self._system_message(
                primary,
                "status",
                (
                    f"{worker_id} completed goal '{goal.get('work_item')}' "
                    "with state=done. Its latest candidate is "
                    f"{reply.get('candidate') or 'none'}. Route the release "
                    "review or close the outcome; completed work does not "
                    "schedule anyone by itself."
                ),
                round_number,
                reply.get("candidate"),
                goal.get("work_item"),
                goal.get("risk"),
            )

    def _goal_prompt(self, agent_id: str) -> str:
        if agent_id in self.topology.worker_ids:
            goal = self.worker_goals.get(agent_id)
            if not self._goal_is_active(goal):
                return (
                    "No active goal is bound. Do not perform substantive work, "
                    "invent a task, audit the repository, or write a report. "
                    "Wait for one concrete goal from the lead or "
                    "the human operator."
                )
            primary = _supervisor_of(self.topology, agent_id)
            return f"""Goal: [{goal['work_item']}] {goal['objective']}
Predicate: {goal.get('predicate_id') or 'unbound'}
Baseline value: {goal.get('baseline_value')}
Success condition: {goal.get('comparison_rule_id') or 'not bound'}
Handoff: {primary}; risk={goal['risk']}

Advance only this goal. Send one same-workItem flag to {primary} for a material
off-goal finding; do not expand scope or substitute administrative prose."""

        primary_managers = set(
            self.topology.primary_manager_by_worker.values()
        )
        if agent_id in primary_managers:
            owned = [
                goal
                for worker_id, goal in self.worker_goals.items()
                if _supervisor_of(self.topology, worker_id) == agent_id
                and self._goal_is_active(goal)
            ]
            open_flags = [
                flag
                for flag in self.off_goal_flags.values()
                if flag["manager_id"] == agent_id
                and flag["status"] == "raised"
            ]
            goal_lines = [
                f"- {goal['worker_id']}: {goal['work_item']} "
                f"[{goal['status']}] "
                f"predicate={goal.get('predicate_id') or 'unbound'} "
                f"— {goal['objective']}"
                for goal in owned
            ] or ["- No worker goal is bound yet."]
            predicates = [
                (
                    f"- {predicate['id']}: class={predicate['goal_class']} "
                    f"evaluator={evaluator['id']} "
                    f"comparison={predicate['comparison_rule_id']}"
                )
                for evaluator in (
                    self.experiment_policy.get("evaluators", {}).values()
                    if self.experiment_policy else []
                )
                for predicate in evaluator.get("predicates", {}).values()
            ] or ["- No project-declared predicate is currently assignable."]
            flag_lines = [
                f"- {flag['flag_id'][:12]} for {flag['worker_id']} "
                f"[{flag['status']}]: {flag['content']}"
                for flag in open_flags
            ] or ["- No off-goal flag requires action."]
            return "\n".join([
                "OWNED WORKER GOALS",
                *goal_lines,
                "",
                "ASSIGNABLE PREDICATES",
                *predicates,
                "",
                "OFF-GOAL FLAGS",
                *flag_lines,
                "",
                "Activate only a measurable goal using goalClass, predicateId, "
                "and evaluatorId. One active worker may own a predicate. Zero "
                "active workers is valid.",
            ])

        if agent_id == self.topology.leader_id:
            active = [
                f"- {worker_id}: {goal['work_item']} [{goal['status']}]"
                for worker_id, goal in sorted(self.worker_goals.items())
                if self._goal_is_active(goal)
            ]
            return (
                "\n".join(["ACTIVE WORKER GOALS", *active])
                if active else ""
            )
        return ""

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
        work_item = f"operator-control/{control_id}"
        if recipient in self.topology.worker_ids:
            current = self.worker_goals.get(recipient)
            if tag == "plan":
                work_item = f"operator-goal/{control_id}"
                primary = self.topology.primary_manager_by_worker.get(
                    recipient,
                ) or requested_by
                accepted, reason = self._bind_worker_goal(
                    worker_id=recipient,
                    manager_id=primary,
                    work_item=work_item,
                    objective=content,
                    risk="routine",
                    round_number=round_number,
                    source="human",
                    force=True,
                )
                if not accepted:
                    raise ValueError(reason)
            elif self._goal_is_active(current):
                work_item = str(current["work_item"])
        message = {
            "runId": self.run_id,
            "round": round_number,
            "from": requested_by or "human-operator",
            "to": recipient,
            "tag": tag,
            "content": content,
            "candidate": None,
            "workItem": work_item,
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
        message = dict(message)
        recipient = message.get("to", "")
        tag = message.get("tag", "")
        if recipient == "organization":
            targets = [
                neighbor
                for neighbor in self.topology.neighbors(sender)
                if self.topology.can_route(sender, neighbor, tag)[0]
            ]
            if not targets:
                self.dropped_messages += 1
                self._append_jsonl("messages.jsonl", {
                    "round": round_number,
                    "from": sender,
                    **message,
                    "status": "dropped",
                    "reason": (
                        "organization broadcast had no directly connected "
                        f"recipient accepting tag {tag}"
                    ),
                    "ts": _utc_now(),
                })
                return
            for target in targets:
                self._deliver_message(
                    sender,
                    {**message, "to": target},
                    round_number,
                )
            return
        candidate = message.get("candidate")
        content = str(message.get("content") or "")
        decision_marker: Optional[str] = None
        if (
            tag == "review"
            and candidate
            and sender in self.topology.final_reviewer_pool
        ):
            parsed = disposition_marker(content)
            allowed = (
                ("NO_VETO", "REVIEWED", "BLOCKED", "VETO")
                if self.topology.review_policy == "veto"
                else ("APPROVED", "BLOCKED")
            )
            decision_marker = parsed if parsed in allowed else None
        if decision_marker:
            if str(candidate).lower() not in content.lower():
                self.dropped_messages += 1
                self._append_jsonl("messages.jsonl", {
                    "round": round_number,
                    "from": sender,
                    **message,
                    "status": "dropped",
                    "reason": (
                        f"{decision_marker} review must name exact candidate "
                        f"{candidate}; decision was not recorded"
                    ),
                    "ts": _utc_now(),
                })
                self._system_message(
                    sender,
                    "blocker",
                    (
                        f"Your {decision_marker} review was not recorded "
                        f"because its content did not name exact candidate "
                        f"{candidate}. Resend it with tag=decision and the "
                        "complete candidate identity."
                    ),
                    round_number,
                    str(candidate),
                    message.get("workItem"),
                    message.get("risk"),
                )
                return
            message["tag"] = "decision"
            message["normalizedFromTag"] = "review"
            tag = "decision"
            self._event(
                "message.decision_normalized",
                round_number,
                sender=sender,
                recipient=recipient,
                candidate=candidate,
                marker=decision_marker,
            )
        if (
            self.topology.review_policy == "veto"
            and tag in {"review", "decision"}
            and (
                recipient in self.topology.final_reviewer_pool
                or sender in self.topology.final_reviewer_pool
            )
            and not candidate
        ):
            self.dropped_messages += 1
            self._append_jsonl("messages.jsonl", {
                "round": round_number,
                "from": sender,
                **message,
                "status": "dropped",
                "reason": (
                    "veto-auditor review and decision traffic requires an "
                    "exact candidate or release-dossier identity; use plan, "
                    "question, answer, or blocker for candidate-less traffic"
                ),
                "ts": _utc_now(),
            })
            return
        if (
            candidate
            and tag in {"handoff", "review", "decision"}
            and sender in self.workspaces
        ):
            candidate_record = self._candidate_record(
                self.workspaces[sender], str(candidate),
            )
            artifact_report_review = (
                tag in {"review", "decision"}
                and bool(message.get("workItem"))
                and message.get("risk") == "release"
            )
            if (
                candidate_record["kind"] in {"artifact-only", "identity-only"}
                and not artifact_report_review
            ):
                reason = (
                    f"{candidate_record['kind']} commit {candidate} is a "
                    "durable report identity, not an implementation "
                    "candidate. Artifact reports may receive "
                    "release-risk review/decision traffic, but they cannot "
                    "be handed off as implementation candidates"
                )
                self.dropped_messages += 1
                self._append_jsonl("messages.jsonl", {
                    "round": round_number,
                    "from": sender,
                    **message,
                    "status": "dropped",
                    "reason": reason,
                    "ts": _utc_now(),
                })
                if tag in {"review", "decision"}:
                    # A silently dropped review request strands the whole
                    # release lane: run nine routed its dossier review, the
                    # gate ate it, and the run concluded over the auditor's
                    # never-scheduled turn. The sender must learn, in-band,
                    # what to fix (a report review needs workItem AND
                    # risk=release).
                    self._system_message(
                        sender,
                        "blocker",
                        (
                            f"Your {tag} request to {recipient} was dropped: "
                            f"{reason}. Re-send with a named workItem and "
                            "risk=release to route a report review."
                        ),
                        round_number,
                        str(candidate),
                        message.get("workItem"),
                        "release",
                    )
                return
            if (
                candidate_record["kind"] == "implementation"
                and tag == "handoff"
                and sender in self.topology.worker_ids
                and self.experiment_policy
                and self.experiment_policy.get(
                    "promotion_requires_goal_progress",
                    False,
                )
            ):
                progress = self._candidate_goal_progress_verdict(
                    str(candidate),
                    round_number=round_number,
                )
                exact_goal_trial = any(
                    trial.get("challenger_candidate") == str(candidate)
                    for trial in progress.get("qualifying_trials", [])
                )
                evaluation_error: Optional[str] = None
                if not exact_goal_trial:
                    try:
                        self._evaluate_bound_goal_candidate(
                            worker_id=sender,
                            candidate=str(candidate),
                            round_number=round_number,
                        )
                    except Exception as exc:
                        evaluation_error = str(exc)
                    progress = self._candidate_goal_progress_verdict(
                        str(candidate),
                        round_number=round_number,
                    )
                exact_goal_evaluation = any(
                    record.get("candidate") == str(candidate)
                    for record in progress.get(
                        "qualifying_goal_evaluations",
                        [],
                    )
                )
                if (
                    not progress["qualifies"]
                    or not (exact_goal_trial or exact_goal_evaluation)
                ):
                    reason = (
                        "implementation handoff discarded: the exact candidate "
                        "is not a host-retained evaluator improvement bound to "
                        "this worker's exact stated current goal"
                    )
                    if evaluation_error:
                        reason += f"; evaluator error: {evaluation_error}"
                    workspace = self.workspaces[sender]
                    parent = _git(
                        workspace.cwd,
                        ["rev-parse", f"{candidate}^"],
                    ).strip()
                    self._host_git(
                        workspace,
                        ["reset", "--hard", parent],
                    )
                    self._event(
                        "candidate.discarded_no_goal_progress",
                        round_number,
                        agent_id=sender,
                        candidate=str(candidate),
                        resulting_head=parent,
                        verdict_sha256=progress["verdict_sha256"],
                    )
                    goal = self.worker_goals.get(sender)
                    if goal:
                        goal["status"] = "blocked"
                        goal["candidate"] = None
                        goal["updated_round"] = round_number
                        goal["progress_discarded_candidate"] = str(candidate)
                        self._persist_goal_state()
                    self.dropped_messages += 1
                    self._append_jsonl("messages.jsonl", {
                        "round": round_number,
                        "from": sender,
                        **message,
                        "status": "dropped",
                        "reason": reason,
                        "ts": _utc_now(),
                    })
                    primary = _supervisor_of(self.topology, sender)
                    self._system_message(
                        sender,
                        "blocker",
                        (
                            f"{reason}. Keep only compact failure evidence. "
                            "The primary manager must bind the current goal to "
                            "a project-owned evaluator and baseline before "
                            "another implementation can be retained."
                        ),
                        round_number,
                        str(candidate),
                        message.get("workItem"),
                        message.get("risk"),
                    )
                    if primary:
                        self._system_message(
                            primary,
                            "blocker",
                            (
                                f"{reason} for {sender}. Do not send the "
                                "candidate into review. Either register an "
                                "evaluator contract whose objective exactly "
                                "matches the current goal, or close the route "
                                "as no progress."
                            ),
                            round_number,
                            str(candidate),
                            message.get("workItem"),
                            message.get("risk"),
                        )
                    return
        allowed, reason = self.topology.can_route(sender, recipient, tag)
        if not allowed:
            self.dropped_messages += 1
            self._append_jsonl("messages.jsonl", {"round": round_number, "from": sender, **message, "status": "dropped", "reason": reason, "ts": _utc_now()})
            return
        if (
            recipient in self.topology.worker_ids
            and sender == _supervisor_of(self.topology, recipient)
            and tag in DELEGATION_TAGS
            and (
                not message.get("workItem")
                or message.get("risk") not in RISKS
            )
        ):
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
        accepted, reason = self._process_goal_protocol_message(
            sender,
            message,
            round_number,
        )
        if not accepted:
            self.dropped_messages += 1
            self._append_jsonl("messages.jsonl", {
                "round": round_number,
                "from": sender,
                **message,
                "status": "dropped",
                "reason": reason,
                "ts": _utc_now(),
            })
            return
        accepted, reason, system_message = self.governance.process_message(sender, message, round_number)
        if not accepted:
            self.dropped_messages += 1
            self._append_jsonl("messages.jsonl", {"round": round_number, "from": sender, **message, "status": "dropped", "reason": reason, "ts": _utc_now()})
            return
        if (
            recipient in self.topology.worker_ids
            and tag in {"plan", "handoff"}
            and sender != _supervisor_of(self.topology, recipient)
        ):
            # Goal-carrying traffic to a worker comes only from its supervisor.
            # Anyone else's plan/handoff binds nothing and would still wake the
            # worker, which is a manufactured turn.
            supervisor = _supervisor_of(self.topology, recipient)
            self.dropped_messages += 1
            self._append_jsonl("messages.jsonl", {
                "round": round_number,
                "from": sender,
                **message,
                "status": "dropped",
                "reason": (
                    f"worker plan/handoff traffic may come only from "
                    f"{supervisor}"
                ),
                "ts": _utc_now(),
            })
            return
        if (
            recipient in self.topology.worker_ids
            and sender == _supervisor_of(self.topology, recipient)
            and tag in DELEGATION_TAGS
        ):
            accepted, reason = self._bind_worker_goal(
                worker_id=recipient,
                manager_id=sender,
                work_item=str(message["workItem"]),
                objective=content,
                risk=str(message["risk"]),
                round_number=round_number,
                goal_class=message.get("goalClass"),
                predicate_id=message.get("predicateId"),
                evaluator_id=message.get("evaluatorId"),
            )
            if not accepted:
                self.dropped_messages += 1
                self._append_jsonl("messages.jsonl", {
                    "round": round_number,
                    "from": sender,
                    **message,
                    "status": "dropped",
                    "reason": reason,
                    "ts": _utc_now(),
                })
                self._system_message(
                    sender,
                    "blocker",
                    reason,
                    round_number,
                    None,
                    message.get("workItem"),
                    message.get("risk"),
                )
                return
        if (
            recipient in self.topology.worker_ids
            and sender in self.topology.manager_ids
            and tag in {"plan", "handoff"}
            and message.get("workItem")
            in self.experiment_contract_by_work_item
        ):
            try:
                self._activate_experiment_contract(
                    manager_id=sender,
                    worker_id=recipient,
                    work_item=str(message["workItem"]),
                    round_number=round_number,
                )
            except Exception as exc:
                self.dropped_messages += 1
                self._append_jsonl("messages.jsonl", {
                    "round": round_number,
                    "from": sender,
                    **message,
                    "status": "dropped",
                    "reason": f"experiment-loop activation failed: {exc}",
                    "ts": _utc_now(),
                })
                return
        active_contract_sha = self.active_experiment_by_worker.get(sender)
        if (
            active_contract_sha
            and recipient
            == self.experiment_contracts[active_contract_sha]["manager_id"]
            and tag in {"blocker", "question", "handoff"}
        ):
            self._halt_experiment_loop(
                self.experiment_contracts[active_contract_sha],
                reason=(
                    "worker submitted a reviewable candidate"
                    if tag == "handoff"
                    else "worker requested manager judgment"
                ),
                round_number=round_number,
                candidate=str(
                    message.get("candidate")
                    or _git(
                        self.workspaces[sender].cwd,
                        ["rev-parse", "HEAD"],
                    ).strip()
                ),
            )
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

    def _terminal_conclusion_digest(
        self,
        status: str,
        rounds: int,
        *,
        verified_candidate: Optional[str],
        promotion_candidate: Optional[str],
        promotion_request: Optional[Dict[str, Any]],
        no_promotion_report: Optional[str] = None,
        pending_human_report: Optional[str] = None,
    ) -> Dict[str, Any]:
        agent_summaries: List[Dict[str, Any]] = []
        failures: List[str] = []
        reported_artifacts: Set[str] = set()
        for agent in self.topology.agents:
            path = self.run_dir / "turns" / f"{_safe_name(agent.agent_id)}.jsonl"
            records: List[Dict[str, Any]] = []
            if path.exists():
                for raw in path.read_text(encoding="utf-8").splitlines():
                    try:
                        records.append(json.loads(raw))
                    except json.JSONDecodeError:
                        continue
            completed = [
                record for record in records
                if record.get("status") == "completed"
                and isinstance(record.get("reply"), dict)
            ]
            if completed:
                last = completed[-1]
                reply = last["reply"]
                agent_summaries.append({
                    "agent_id": agent.agent_id,
                    "role": agent.role,
                    "round": last.get("round"),
                    "state": reply.get("state"),
                    "summary": str(reply.get("summary") or "")[:800],
                })
                reported_artifacts.update(
                    str(item) for record in completed
                    for item in (record.get("reply") or {}).get("artifacts", [])
                    if item
                )
            for record in records:
                if record.get("status") != "failed":
                    continue
                detail = str(record.get("error") or "unknown turn failure")
                failures.append(
                    f"{agent.agent_id} R{record.get('round', '?')}: "
                    f"{detail[:900]}"
                )

        decisions: List[Dict[str, Any]] = []
        message_path = self.run_dir / "messages.jsonl"
        if message_path.exists():
            for raw in message_path.read_text(encoding="utf-8").splitlines():
                try:
                    message = json.loads(raw)
                except json.JSONDecodeError:
                    continue
                if (
                    message.get("status", "delivered") == "delivered"
                    and message.get("tag") in {
                        "decision", "blocker", "review", "handoff",
                    }
                ):
                    decisions.append({
                        "round": message.get("round"),
                        "from": message.get("from"),
                        "to": message.get("to"),
                        "tag": message.get("tag"),
                        "candidate": message.get("candidate"),
                        "workItem": message.get("workItem"),
                        "content": str(message.get("content") or "")[:500],
                    })

        candidate_records: Dict[str, Dict[str, Any]] = {
            candidate: dict(record)
            for candidate, record in self.candidate_kinds.items()
        }
        candidate_path = self.run_dir / "candidates.jsonl"
        if candidate_path.exists():
            for raw in candidate_path.read_text(encoding="utf-8").splitlines():
                try:
                    record = json.loads(raw)
                except json.JSONDecodeError:
                    continue
                candidate = record.get("candidate")
                if candidate:
                    candidate_records[str(candidate)] = {
                        "candidate": str(candidate),
                        "kind": record.get("kind", "unknown"),
                        "paths": list(record.get("paths") or []),
                        "agent_id": record.get("agentId"),
                        "round": record.get("round"),
                    }

        artifacts = set(reported_artifacts)
        artifacts.update(
            str(item.get("manifest_path"))
            for item in self.candidate_artifact_manifests
            if item.get("manifest_path")
        )
        if promotion_request:
            artifacts.add(str(self.run_dir / "promotion-request.json"))
        if (self.run_dir / "deliverables" / "manifest.json").is_file():
            artifacts.add(str(self.run_dir / "deliverables" / "manifest.json"))
        if (self.run_dir / "experiments.jsonl").is_file():
            artifacts.add(str(self.run_dir / "experiments.jsonl"))
        if (self.experiment_loop_root / "contracts.jsonl").is_file():
            artifacts.add(str(self.experiment_loop_root / "contracts.jsonl"))
        if (self.experiment_loop_root / "trials.jsonl").is_file():
            artifacts.add(str(self.experiment_loop_root / "trials.jsonl"))
        if (self.run_dir / "goal-state.json").is_file():
            artifacts.add(str(self.run_dir / "goal-state.json"))
        if (self.run_dir / "candidate-progress.json").is_file():
            artifacts.add(str(self.run_dir / "candidate-progress.json"))

        return {
            "terminal_status": status,
            "rounds": rounds,
            "working_rounds": min(rounds, self.max_rounds),
            "closeout_rounds": max(0, rounds - self.max_rounds),
            "admission": self.admission,
            "candidate_counts": self._candidate_counts(),
            "mission_sha256": hashlib.sha256(
                self.mission.encode("utf-8")
            ).hexdigest(),
            "mission_path": str(self.run_dir / "run.json"),
            "agent_states": dict(self.states),
            "worker_goals": dict(self.worker_goals),
            "off_goal_flags": list(self.off_goal_flags.values()),
            "turn_counts": {
                "attempted": self.attempted_turns,
                "completed": self.completed_turns,
                "failed": self.failed_turns,
            },
            "agent_final_summaries": agent_summaries,
            "recent_decisions_and_blockers": decisions[-12:],
            "infrastructure_failures": failures[-12:],
            "governance": {
                "required_final_approvers": sorted(
                    self.governance.required_final_approvers()
                ),
                "durable_messages": str(self.run_dir / "messages.jsonl"),
            },
            "candidate_kind_counts": {
                kind: sum(
                    record.get("kind") == kind
                    for record in candidate_records.values()
                )
                for kind in (
                    "implementation", "artifact-only", "identity-only",
                    "unknown",
                )
            },
            "candidates": [
                record
                for record in candidate_records.values()
                if (
                    record.get("kind") == "implementation"
                    or record.get("candidate") in {
                        verified_candidate,
                        promotion_candidate,
                        no_promotion_report,
                        pending_human_report,
                    }
                )
            ][:16],
            "integrated_candidates": dict(self.integrated_candidates),
            "verified_candidate": verified_candidate,
            "promotion_candidate": promotion_candidate,
            "promotion_request": (
                str(self.run_dir / "promotion-request.json")
                if promotion_request else None
            ),
            "no_promotion_report": no_promotion_report,
            "pending_human_report": pending_human_report,
            "artifacts": sorted(artifacts)[:24],
            "artifact_registry": str(self.run_dir),
            "experiment_budget": {
                "maximum": self.max_experiments,
                "used": self._experiment_used(),
                "remaining": self._experiment_remaining(),
            },
            "experiment_loop": {
                "enabled": self.experiment_policy is not None,
                "contract_count": len(self.experiment_contracts),
                "trial_count": len(self.experiment_trials),
                "active_by_worker": dict(self.active_experiment_by_worker),
                "registry": str(self.experiment_loop_root),
            },
            "candidate_progress": (
                {
                    key: self.candidate_progress.get(key)
                    for key in (
                        "candidate", "required", "qualifies", "decision",
                        "reason", "verdict_sha256",
                    )
                }
                if self.candidate_progress else None
            ),
        }

    def _authoritative_promotion_readiness(
        self,
        status: str,
        digest: Dict[str, Any],
    ) -> str:
        if status == "cancelled":
            return "cancelled"
        if status == "completed_no_promotion":
            return "no_candidate"
        if status == "completed_pending_human":
            return "awaiting_human_approval"
        if digest.get("verified_candidate"):
            return (
                "ready_for_human_review"
                if self.topology.human_promotion_required
                else "verified"
            )
        if any(
            record.get("kind") == "implementation"
            for record in digest.get("candidates", [])
        ):
            return "not_ready"
        return "no_candidate"

    def _fallback_run_conclusion(
        self,
        status: str,
        digest: Dict[str, Any],
        *,
        reason: str,
    ) -> Dict[str, Any]:
        lead_summary = next(
            (
                record["summary"]
                for record in reversed(digest["agent_final_summaries"])
                if record["agent_id"] == self.topology.leader_id
                and record.get("summary")
            ),
            "",
        )
        blocked = [
            f"{record['agent_id']}: {record['summary']}"
            for record in digest["agent_final_summaries"]
            if record.get("state") == "blocked" and record.get("summary")
        ]
        accomplishments = [
            (
                f"Completed {self.completed_turns} of "
                f"{self.attempted_turns} attempted organization turns."
            ),
        ]
        if digest["integrated_candidates"]:
            accomplishments.append(
                f"Integrated {len(digest['integrated_candidates'])} reviewed "
                "candidate(s) in the disposable integration worktree."
            )
        if digest["artifacts"]:
            accomplishments.append(
                f"Preserved {len(digest['artifacts'])} reported or sealed "
                "artifact reference(s)."
            )
        readiness = self._authoritative_promotion_readiness(status, digest)
        if readiness == "ready_for_human_review":
            next_action = (
                "Review the exact promotion request and candidate artifacts; "
                "RecCli applied no canonical effects."
            )
        elif readiness == "awaiting_human_approval":
            next_action = (
                "Review the exact approval dossier in the console. Approval "
                "will bind the request and start a fresh successor; the "
                "terminal supervisor will not be resumed."
            )
        elif readiness == "verified":
            next_action = "Review and apply the exact verified candidate."
        elif status == "cancelled":
            next_action = (
                "Inspect the preserved run record before deciding whether to "
                "resume the mission in a new organization."
            )
        else:
            next_action = (
                "Resolve the named blockers and infrastructure failures, then "
                "start a successor run from a clean checkpoint."
            )
        return {
            "summary": (
                lead_summary
                or f"The organization ended with status {status}; no "
                "successful terminal lead synthesis was available."
            ),
            "accomplishments": accomplishments,
            "conclusive_findings": [],
            "evidence_and_tests": [],
            "scientific_or_product_blockers": blocked,
            "infrastructure_failures": list(digest["infrastructure_failures"]),
            "unresolved": blocked or [
                "The durable record requires human interpretation."
            ],
            "promotion_readiness": readiness,
            "next_action": next_action,
            "proposed_successor_admission": None,
            "limitations": [
                reason,
                (
                    "This conservative fallback reports durable mechanics and "
                    "the last lead summary; it does not infer scientific or "
                    "product truth."
                ),
            ],
        }

    def _write_terminal_lead_conclusion(
        self,
        status: str,
        rounds: int,
        *,
        verified_candidate: Optional[str],
        promotion_candidate: Optional[str],
        promotion_request: Optional[Dict[str, Any]],
        no_promotion_report: Optional[str] = None,
        pending_human_report: Optional[str] = None,
    ) -> Dict[str, Any]:
        digest = self._terminal_conclusion_digest(
            status,
            rounds,
            verified_candidate=verified_candidate,
            promotion_candidate=promotion_candidate,
            promotion_request=promotion_request,
            no_promotion_report=no_promotion_report,
            pending_human_report=pending_human_report,
        )
        lead_id = self.topology.leader_id
        generated_by = "lead"
        usage: Dict[str, Any] = {}
        failure_reason: Optional[str] = None
        if status == "cancelled":
            generated_by = "host-fallback"
            failure_reason = (
                "The run was cancelled, so RecCli did not start another native "
                "model turn after the stop request."
            )
            value = self._fallback_run_conclusion(
                status, digest, reason=failure_reason,
            )
        else:
            existing_session = self.sessions.get(lead_id)
            mission_context = (
                f"Mission retained from this session's bootstrap; durable copy: "
                f"`{self.run_dir / 'run.json'}`."
                if existing_session is not None else self.mission
            )
            prompt = f"""# Terminal organization conclusion

You are the organization lead. Execution and release handling are over. Produce
the conclusive after-action report for the operator. This synthesis is outside
the working-round and closeout budgets. It cannot authorize promotion, modify
files, start experiments, or send more team messages.

Separate actual accomplishments from proposals. Distinguish scientific or
product blockers from infrastructure failures. Never describe an artifact-only
or identity-only commit as an implementation candidate. Treat the host-supplied
promotion readiness, exact candidate identities, turn counts, and artifact
paths as authoritative. State uncertainty plainly and recommend exactly one
smallest next action.

If, and only if, this run's findings define a sharper next mission, set
proposed_successor_admission to a complete admission block (consumer,
work_class, done_condition, stop_conditions) scoped to that one next mission
in service of the same standing product goal; a proposal is a refinement,
never a pivot. The host validates it and an autonomous continuation will use
it. Set it to null when the standing contract should simply carry. Do not
propose work this run's evidence does not support. Rounds and agent turns are different units: this run used
{digest['working_rounds']} working rounds and {digest['closeout_rounds']}
closeout rounds, containing {digest['turn_counts']['completed']} completed agent
turns. Never describe a round limit as a turn limit.

## Original mission

{mission_context}

## Durable terminal digest

{json.dumps(digest, indent=2, ensure_ascii=False)}
"""
            try:
                session = existing_session
                if session is None:
                    lead = self.topology.agent(lead_id)
                    session = SubscriptionSession(
                        self.provider_by_agent[lead_id],
                        self.workspaces[lead_id],
                        False,
                        lead_id,
                        self.run_dir,
                        self.model,
                        lead.reasoning,
                    )
                    self.sessions[lead_id] = session
                result = session.run(
                    prompt,
                    RUN_CONCLUSION_SCHEMA,
                    min(self.turn_timeout_seconds, 300),
                )
                value = validate_run_conclusion(result["value"])
                usage = result.get("usage", {})
                accounted_usage = self._add_usage(
                    usage,
                    session.provider,
                    result.get("session_id"),
                )
                self._append_jsonl("turns/lead-conclusion.jsonl", {
                    "round": rounds,
                    "agent_id": lead_id,
                    "provider": session.provider,
                    "status": "completed",
                    "session_id": result.get("session_id"),
                    "usage": usage,
                    "accounted_usage": accounted_usage,
                    "reply": value,
                    "ts": _utc_now(),
                })
                self._event(
                    "conclusion.completed",
                    rounds,
                    agent_id=lead_id,
                    provider=session.provider,
                )
            except Exception as exc:
                generated_by = "host-fallback"
                failure_reason = (
                    f"Terminal lead conclusion failed: {type(exc).__name__}: "
                    f"{str(exc)[:900]}"
                )
                self.failed_turns += 1
                digest["infrastructure_failures"].append(failure_reason)
                value = self._fallback_run_conclusion(
                    status, digest, reason=failure_reason,
                )
                self._append_jsonl("turns/lead-conclusion.jsonl", {
                    "round": rounds,
                    "agent_id": lead_id,
                    "provider": self.provider_by_agent[lead_id],
                    "status": "failed",
                    "error": failure_reason,
                    "ts": _utc_now(),
                })
                self._event(
                    "conclusion.fallback",
                    rounds,
                    agent_id=lead_id,
                    reason=failure_reason,
                )

        value = _normalize_round_language(value, digest)
        readiness = self._authoritative_promotion_readiness(status, digest)
        value["promotion_readiness"] = readiness
        durable_failures = list(digest["infrastructure_failures"])
        value["infrastructure_failures"] = list(dict.fromkeys([
            *value["infrastructure_failures"],
            *durable_failures,
        ]))
        conclusion = {
            "schema": "reccli.organization-run-conclusion.v1",
            "run_id": self.run_id,
            "terminal_status": status,
            "generated_at": _utc_now(),
            "generated_by": generated_by,
            "lead_agent_id": lead_id,
            "lead_provider": self.provider_by_agent[lead_id],
            **value,
            "candidates": digest["candidates"],
            "integrated_candidates": digest["integrated_candidates"],
            "verified_candidate": verified_candidate,
            "promotion_candidate": promotion_candidate,
            "promotion_request": digest["promotion_request"],
            "no_promotion_report": no_promotion_report,
            "pending_human_report": pending_human_report,
            "artifacts": digest["artifacts"],
            "turn_counts": {
                "attempted": self.attempted_turns,
                "completed": self.completed_turns,
                "failed": self.failed_turns,
            },
            "round_counts": {
                "total": rounds,
                "working": digest["working_rounds"],
                "closeout": digest["closeout_rounds"],
            },
            "experiment_budget": digest["experiment_budget"],
            "experiment_loop": digest["experiment_loop"],
            "canonical_effects_applied": False,
        }
        _write_run_conclusion_files(self.run_dir, conclusion)
        return conclusion

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
                f"and its index {pack['index']}. Before verification, read the "
                "complete required Critical foundation once for this fresh "
                "session. Canonical repository documentation remains authoritative."
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

"""
        result = session.run(prompt, BLIND_REVIEW_SCHEMA, self.turn_timeout_seconds)
        review = result["value"]
        if not isinstance(review, dict) or review.get("candidate") != candidate:
            raise ValueError("blind verifier did not review the exact candidate")
        if review.get("verdict") not in {"approved", "blocked"}:
            raise ValueError("blind verifier returned an invalid verdict")
        accounted_usage = self._add_usage(
            result.get("usage", {}),
            self.blind_verifier_provider,
            result.get("session_id"),
        )
        self._append_jsonl("turns/blind-verifier.jsonl", {
            "round": round_number, "candidate": candidate,
            "provider": self.blind_verifier_provider,
            "session_id": result.get("session_id"), "usage": result.get("usage", {}),
            "accounted_usage": accounted_usage,
            "review": review,
        })
        return review

    def _record_turn_failure(
        self,
        agent: AgentSpec,
        error: str,
        round_number: int,
    ) -> None:
        """Release a dead lane on the first failed worker turn.

        In the first live flat run, one worker's provider turns died three
        rounds straight while it retained sole ownership of the mission's only
        predicate, serializing every other lane behind it with no signal to
        the lead: a dead worker cannot raise the flag that would free its own
        goal. The host cancels the goal immediately (releasing predicate
        ownership), clears the stale delegation inbox so the lane stops being
        scheduled to die, and tells the supervisor to rebind. Blind retry is
        never cheaper than that: a retried dead turn costs up to a full turn
        timeout, while a released transient costs one lead turn, and the
        released worker remains eligible for the rebind.
        """
        consecutive = self._consecutive_turn_failures.get(agent.agent_id, 0) + 1
        self._consecutive_turn_failures[agent.agent_id] = consecutive
        if agent.agent_id not in self.topology.worker_ids:
            return
        goal = self.worker_goals.get(agent.agent_id)
        if not self._goal_is_active(goal):
            return
        goal["status"] = "cancelled"
        goal["cancelled_reason"] = (
            f"provider turn failure ({consecutive} consecutive)"
        )
        goal["updated_round"] = round_number
        self._persist_goal_state()
        self.inboxes[agent.agent_id] = []
        supervisor = _supervisor_of(self.topology, agent.agent_id)
        self._event(
            "worker.goal.released_after_failures",
            round_number,
            worker_id=agent.agent_id,
            work_item=goal.get("work_item"),
            consecutive_failures=consecutive,
        )
        self._system_message(
            supervisor,
            "blocker",
            (
                f"{agent.agent_id} failed a provider turn ({error[:200]}). "
                f"Its goal '{goal.get('work_item')}' was released and its "
                "inbox cleared; rebind the work (the same worker remains "
                "eligible) or stop."
            ),
            round_number,
            None,
            goal.get("work_item"),
            goal.get("risk"),
        )

    def _has_initial_worker_assignment(self, worker_id: str) -> bool:
        if self._goal_is_active(self.worker_goals.get(worker_id)):
            return True
        primary = _supervisor_of(self.topology, worker_id)
        if self._has_delegation(
            self.inboxes[worker_id],
            sender=primary,
            recipient=worker_id,
        ):
            return True
        # A direct assignment from the lead also counts. When a manager fails to
        # delegate, the barrier falls back to the lead assigning the worker
        # itself; that message is written truthfully as coming from the lead, so
        # this check has to recognise it or the barrier would keep re-firing on a
        # worker that does in fact have work.
        return self._has_delegation(
            self.inboxes[worker_id],
            sender=self.topology.leader_id,
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
            and message.get("tag") in DELEGATION_TAGS
            and bool(message.get("workItem"))
            and message.get("risk") in RISKS
            for message in messages
        )

    def _select_agents(self, round_number: int) -> List[AgentSpec]:
        if self.topology.scheduler == "all":
            return list(self.topology.agents)
        if round_number == 1:
            return [self.topology.agent(self.topology.leader_id)]
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
        selected = [
            agent for agent in selected
            if agent.agent_id not in self.experiment_halted_workers
        ]
        # A worker turn is not synonymous with a sealed experiment. Workers
        # may inspect evidence, reproduce tracked tests, review interfaces, or
        # report that a route is indeterminate without consuming an artifact
        # slot. The hard experiment limit is enforced when generated outputs
        # are sealed, so it must not silently starve an explicitly assigned
        # worker before the team has even evaluated the lane.
        return selected

    def _nudge_pending_reviews(self, round_number: int) -> int:
        """Re-route assigned-but-undisposed release reviews before winding down.

        The mirror of the completed-goal wake, on the reviewer's side of the
        table: a review that was routed but never taken does not count as
        pending work anywhere the scheduler looks, so a run can conclude over
        the exact disposition it is waiting for. Each undisposed assignment
        gets one host re-route to its reviewer; a reviewer that ignores the
        nudge lets the run end honestly.
        """
        nudged = 0
        for assignment in self.governance.assignments.values():
            if assignment.get("status") != "assigned":
                continue
            candidate = str(assignment.get("candidate") or "")
            reviewer = str(assignment.get("reviewerId") or "")
            if not candidate or not reviewer:
                continue
            if candidate in self.integrated_candidates:
                continue
            key = f"{candidate}:{reviewer}"
            if key in self._nudged_assignments:
                continue
            self._nudged_assignments.add(key)
            self._system_message(
                reviewer,
                "review",
                (
                    f"Assignment {assignment.get('workItem')} for exact "
                    f"candidate {candidate} still awaits your recorded "
                    "disposition. Reply with NO_VETO or a veto with "
                    "reproducible grounds; the run cannot close over an "
                    "untaken review."
                ),
                round_number,
                candidate,
                assignment.get("workItem"),
                assignment.get("risk"),
            )
            nudged += 1
        if nudged:
            self._event(
                "review.nudged", round_number, count=nudged,
            )
        return nudged

    def _select_closeout_agents(self) -> List[AgentSpec]:
        """Schedule only review, integration, and release traffic after cap.

        The configured round count remains the exploration/work budget.
        Closeout cannot wake implementation workers or initiate another
        experiment; it only gives already-produced candidates enough message
        boundaries to complete adversarial review and integration.
        """
        def release_relevant(message: Dict[str, Any]) -> bool:
            return bool(
                message.get("operator_message")
                or message.get("candidate")
                or message.get("risk") == "release"
            )

        return [
            agent for agent in self.topology.agents
            if (
                agent.agent_id not in self.topology.worker_ids
                and any(
                    release_relevant(message)
                    for message in self.inboxes[agent.agent_id]
                )
            )
        ]

    def _candidate_counts(self) -> Dict[str, int]:
        """Count materialized candidates by kind, so status cannot present a
        stack of reports as a stack of work."""
        counts = {"implementation": 0, "artifact_only": 0, "identity_only": 0}
        for record in self.candidate_kinds.values():
            kind = str(record.get("kind") or "")
            key = kind.replace("-", "_")
            if key in counts:
                counts[key] += 1
        return counts

    def _closeout_progress_signature(self) -> str:
        """Fingerprint only state that can advance release closeout.

        Agent prose and ``state=working`` are intentionally excluded. Repeated
        review wording without a new candidate, decision, integration head, or
        sealed artifact cannot buy another model round.
        """
        actionable_inbox = sorted(
            (
                recipient,
                str(message.get("from") or ""),
                str(message.get("tag") or ""),
                str(message.get("candidate") or ""),
                str(message.get("workItem") or ""),
                str(message.get("risk") or ""),
                str(message.get("control_id") or ""),
            )
            for recipient, messages in self.inboxes.items()
            for message in messages
            if (
                message.get("operator_message")
                or message.get("candidate")
                or message.get("risk") == "release"
            )
        )
        integration_head = None
        finalizer_workspace = self.workspaces.get(self.topology.finalizer_id)
        if finalizer_workspace is not None:
            integration_head = _git(
                finalizer_workspace.cwd, ["rev-parse", "HEAD"],
            ).strip()
        payload = {
            "governance": self.governance.snapshot(),
            # Only implementation candidates advance closeout. An artifact-only
            # report or an empty identity commit is paper: in the recorded
            # 63-turn run, 29 of 32 candidates were artifact-only, and each one
            # bought review traffic and closeout rounds. Sealed experiment
            # bundles still count below; they are metered real work.
            "candidate_kinds": {
                candidate: record
                for candidate, record in self.candidate_kinds.items()
                if record.get("kind") == "implementation"
            },
            "integrated_candidates": self.integrated_candidates,
            "candidate_artifacts": [
                {
                    "candidate": item.get("candidate"),
                    "manifest_sha256": item.get("manifest_sha256"),
                }
                for item in self.candidate_artifact_manifests
            ],
            "experiment_records": list(self.experiment_records),
            "integration_head": integration_head,
            "actionable_inbox": actionable_inbox,
        }
        return hashlib.sha256(json.dumps(
            payload,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        ).encode("utf-8")).hexdigest()

    def _add_usage(
        self,
        usage: Dict[str, Any],
        provider: Optional[str] = None,
        session_id: Optional[str] = None,
    ) -> Dict[str, int]:
        raw = {
            key: int(usage.get(key, 0) or 0)
            for key in self.usage
        }
        accounted = dict(raw)
        if provider == "codex" and session_id:
            key = (provider, session_id)
            previous = self._provider_session_usage.get(key)
            if previous is not None:
                accounted = {
                    token_key: (
                        raw[token_key] - previous[token_key]
                        if raw[token_key] >= previous[token_key]
                        else raw[token_key]
                    )
                    for token_key in self.usage
                }
            self._provider_session_usage[key] = raw
        for key in self.usage:
            value = accounted[key]
            self.usage[key] += value
            if provider and provider in self.usage_by_provider:
                self.usage_by_provider[provider][key] += value
        return accounted

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
        if phase is None and round_number <= 1:
            resolved_phase = "lead_recon"
        if phase is None and self.active_experiment_by_worker:
            resolved_phase = "experiment_loop"
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
            "admission": self.admission,
            "host_provider": self.host_provider,
            "provider_assignments": self.provider_by_agent,
            "blind_verifier_provider": self.blind_verifier_provider,
            "topology": self.topology.topology_id, "updated_at": _utc_now(),
            "pid": os.getpid(), "run_dir": str(self.run_dir),
            "agent_states": self.states,
            "worker_goals": self.worker_goals,
            "off_goal_flags": list(self.off_goal_flags.values()),
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
            "candidate_counts": self._candidate_counts(),
            "scientific_work_bundles": self._experiment_used(),
            "experiment_records": str(self.run_dir / "experiments.jsonl"),
            "host_state_brief": str(self.run_dir / "host-state.json"),
            "host_state_sha256": self.host_state_brief.get("content_sha256"),
            "max_experiments": self.max_experiments,
            "experiments_remaining": self._experiment_remaining(),
            "experiment_loop_enabled": self.experiment_policy is not None,
            "experiment_loop_contracts": len(self.experiment_contracts),
            "experiment_loop_trials": len(self.experiment_trials),
            "experiment_loop_active_workers": sorted(
                self.active_experiment_by_worker
            ),
            "experiment_loop_halted_workers": sorted(
                self.experiment_halted_workers
            ),
            "experiment_loop_root": str(self.experiment_loop_root),
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


def organization_root(project_root: Path) -> Path:
    return project_root / "devsession" / "agent-organizations"


def create_run_request(
    working_directory: str,
    mission: str,
    provider: str = "auto",
    topology: str = "flat",
    max_rounds: int = 8,
    max_concurrency: int = 5,
    turn_timeout_seconds: int = 1200,
    model: str = "auto",
    evidence_paths: Optional[List[str]] = None,
    protected_paths: Optional[List[str]] = None,
    context_manifest: Optional[str] = None,
    experiment_policy: Optional[str] = None,
    max_experiments: int = 3,
    continuation_from_run_id: Optional[str] = None,
    continuation_conclusion_sha256: Optional[str] = None,
    mission_origin: str = "direct",
    admission: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    project_root = discover_project_root(Path(working_directory).expanduser().resolve())
    if project_root is None:
        raise FileNotFoundError(f"No RecCli/Git project found from {working_directory}")
    if not mission or not mission.strip():
        raise ValueError("mission must not be empty")
    # The admission gate: no consumer, work class, done condition, and stop
    # conditions means no supervisor. Enforced before any filesystem effect so
    # a rejected launch leaves nothing behind.
    normalized_admission = validate_admission(admission)
    normalized_parent = (
        str(continuation_from_run_id).strip()
        if continuation_from_run_id else None
    )
    normalized_conclusion_sha = (
        str(continuation_conclusion_sha256).strip()
        if continuation_conclusion_sha256 else None
    )
    normalized_origin = str(mission_origin or "direct").strip()
    if bool(normalized_parent) != bool(normalized_conclusion_sha):
        raise ValueError(
            "continuation run id and conclusion SHA-256 must be supplied together"
        )
    if normalized_conclusion_sha and not re.fullmatch(
        r"[0-9a-f]{64}", normalized_conclusion_sha,
    ):
        raise ValueError("continuation conclusion SHA-256 is invalid")
    if normalized_origin not in {
        "direct",
        "project-emitter",
        "terminal-conclusion",
    }:
        raise ValueError("unsupported mission origin")
    _validate_clean_repository(project_root)
    resolved_evidence = resolve_evidence_paths(project_root, evidence_paths)
    resolved_protected = resolve_protected_paths(project_root, protected_paths)
    resolved_context = resolve_context_manifest(project_root, context_manifest)
    resolved_experiment_policy = resolve_experiment_policy(
        project_root,
        experiment_policy,
    )
    if resolved_experiment_policy is not None:
        experiment_policy_definition = _load_experiment_policy_definition(
            project_root,
            resolved_experiment_policy,
        )
        immutable_loop_paths = {
            resolved_experiment_policy.relative_to(project_root).as_posix(),
            *(
                path
                for evaluator in experiment_policy_definition[
                    "evaluators"
                ].values()
                for path in evaluator["immutable_paths"]
            ),
        }
        for relative in sorted(immutable_loop_paths):
            if relative not in resolved_protected:
                resolved_protected.append(relative)
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
        "admission": normalized_admission,
        "provider": provider_plan.mode, "provider_requested": provider,
        "host_provider": provider_plan.host_provider,
        "available_providers": provider_plan.available_providers,
        "provider_assignments": provider_plan.provider_assignments,
        "blind_verifier_provider": provider_plan.blind_verifier_provider,
        "provider_authentication": provider_plan.authentication,
        "topology": topology_config.topology_id,
        "topology_requested": topology,
        "max_rounds": max(1, int(max_rounds)),
        "mission_origin": normalized_origin,
        "continuation_from_run_id": normalized_parent,
        "continuation_conclusion_sha256": normalized_conclusion_sha,
        "scheduler": topology_config.scheduler,
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
        "experiment_policy": (
            resolved_experiment_policy.relative_to(project_root).as_posix()
            if resolved_experiment_policy else None
        ),
        "max_experiments": max(0, int(max_experiments)),
        "control_protocol": "reccli.organization-control.v1",
    }
    (run_dir / "request.json").write_text(json.dumps(request, indent=2) + "\n", encoding="utf-8")
    (run_dir / "admission.json").write_text(
        json.dumps(normalized_admission, indent=2) + "\n", encoding="utf-8",
    )
    (run_dir / "status.json").write_text(json.dumps({
        "run_id": run_id, "status": "starting", "round": 0,
        "admission": normalized_admission,
        "max_rounds": max(1, int(max_rounds)),
        "rounds_remaining": max(1, int(max_rounds)),
        "scheduled_turns": 0, "completed_turns": 0, "attempted_turns": 0,
        "detail": "Background organization worker is starting",
        "provider": provider_plan.mode,
        "host_provider": provider_plan.host_provider,
        "provider_assignments": provider_plan.provider_assignments,
        "blind_verifier_provider": provider_plan.blind_verifier_provider,
        "topology": topology_config.topology_id,
        "topology_requested": topology,
        "mission_origin": normalized_origin,
        "continuation_from_run_id": normalized_parent,
        "continuation_conclusion_sha256": normalized_conclusion_sha,
        "human_promotion_required": topology_config.human_promotion_required,
        "evidence_paths": [str(path) for path in resolved_evidence],
        "protected_paths": resolved_protected,
        "context_manifest": (
            resolved_context.relative_to(project_root).as_posix()
            if resolved_context else None
        ),
        "experiment_policy": (
            resolved_experiment_policy.relative_to(project_root).as_posix()
            if resolved_experiment_policy else None
        ),
        "max_experiments": max(0, int(max_experiments)),
        "control_protocol": "reccli.organization-control.v1",
        "phase": "lead_recon",
        "agent_states": {
            agent.agent_id: "idle" for agent in topology_config.agents
        },
        "worker_goals": {},
        "off_goal_flags": [],
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
        experiment_policy=request.get("experiment_policy"),
        max_experiments=request.get("max_experiments", 3),
        continuation_from_run_id=request.get("continuation_from_run_id"),
        continuation_conclusion_sha256=request.get(
            "continuation_conclusion_sha256",
        ),
        mission_origin=request.get("mission_origin", "direct"),
        admission=request.get("admission"),
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
