"""Atomic project-owned organization launch contracts.

Projects keep mission selection, scientific policy, and readiness checks in
their own repository. RecCli validates a small tracked contract, runs its
commands without a shell, and verifies the exact dynamically selected mission.
An opt-in continuation policy may replace that initial mission with a bounded
successor mission derived from the latest durable terminal lead conclusion.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional

from .organization_control import TERMINAL_STATUSES, list_organization_runs
from .project.devproject import discover_project_root


PROJECT_LAUNCH_SCHEMA = "reccli.project-organization-launch.v1"
PROJECT_LAUNCH_FILENAME = "reccli.organization-launch.json"
DEFAULT_EMITTER = (
    ".venv/bin/python",
    "scripts/validate_organization_readiness.py",
    "--emit-launch",
)
ALLOWED_START_ARGUMENTS = {
    "working_directory",
    "mission",
    "provider",
    "topology",
    "max_rounds",
    "max_concurrency",
    "turn_timeout_seconds",
    "model",
    "evidence_paths",
    "protected_paths",
    "context_manifest",
    "experiment_policy",
    "max_experiments",
}
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
CONTINUATION_MODE = "latest-terminal-conclusion"
DEFAULT_CONTINUATION_STATUSES = {
    "completed_no_promotion",
    "round_limit",
    "stalled",
    # A run that ended because it authored no experiment contract. Excluding it
    # did not merely stop a successor from auto-launching: _apply_terminal_continuation
    # RAISES continuation_not_authorized for any ineligible latest status, so the
    # project could not launch at all until someone intervened by hand. Ending a
    # run early must not brick the launch path, and a successor inherits the
    # scrubbed conclusion and is free to take a different approach.
    "no_experiment_contract",
}
DEFAULT_CONTINUATION_READINESS = {"not_ready", "no_candidate"}


class ProjectOrganizationLaunchError(RuntimeError):
    """A fail-closed project launch contract error."""

    def __init__(
        self,
        message: str,
        *,
        code: str = "launch_contract_error",
        detail: Optional[Dict[str, Any]] = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.detail = detail or {}


def _sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _git(root: Path, *args: str) -> str:
    completed = subprocess.run(
        ["git", *args],
        cwd=root,
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )
    if completed.returncode != 0:
        raise ProjectOrganizationLaunchError(
            f"git {' '.join(args)} failed: "
            f"{(completed.stderr or completed.stdout).strip()}",
            code="git_validation_failed",
        )
    return completed.stdout.strip()


def _tracked_relative_path(root: Path, raw: str, *, label: str) -> Path:
    supplied = Path(str(raw))
    if supplied.is_absolute() or ".." in supplied.parts:
        raise ProjectOrganizationLaunchError(
            f"{label} must be a safe project-relative path: {raw}",
            code="unsafe_contract_path",
        )
    lexical = Path(os.path.abspath(root / supplied))
    try:
        lexical.relative_to(root)
    except ValueError as exc:
        raise ProjectOrganizationLaunchError(
            f"{label} escapes the project root: {raw}",
            code="unsafe_contract_path",
        ) from exc
    if not lexical.is_file() or lexical.is_symlink():
        raise ProjectOrganizationLaunchError(
            f"{label} is missing or is a symlink: {raw}",
            code="missing_contract_path",
        )
    tracked = subprocess.run(
        ["git", "ls-files", "--error-unmatch", "--", supplied.as_posix()],
        cwd=root,
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )
    if tracked.returncode != 0 or not tracked.stdout.strip():
        raise ProjectOrganizationLaunchError(
            f"{label} must be tracked by Git: {raw}",
            code="untracked_contract_path",
        )
    return lexical


def _load_contract(root: Path) -> tuple[Dict[str, Any], str]:
    contract_path = root / PROJECT_LAUNCH_FILENAME
    if contract_path.exists():
        path = _tracked_relative_path(
            root,
            PROJECT_LAUNCH_FILENAME,
            label="organization launch contract",
        )
        try:
            contract = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ProjectOrganizationLaunchError(
                f"cannot read organization launch contract: {exc}",
                code="invalid_launch_contract",
            ) from exc
        if not isinstance(contract, dict):
            raise ProjectOrganizationLaunchError(
                "organization launch contract must be a JSON object",
                code="invalid_launch_contract",
            )
        if contract.get("schema") != PROJECT_LAUNCH_SCHEMA:
            raise ProjectOrganizationLaunchError(
                f"organization launch contract schema must be "
                f"{PROJECT_LAUNCH_SCHEMA}",
                code="invalid_launch_contract",
            )
        return contract, PROJECT_LAUNCH_FILENAME

    fallback_script = root / DEFAULT_EMITTER[1]
    if fallback_script.is_file():
        _tracked_relative_path(
            root,
            DEFAULT_EMITTER[1],
            label="organization readiness emitter",
        )
        return {
            "schema": PROJECT_LAUNCH_SCHEMA,
            "preflight_commands": [],
            "emitter_command": {
                "id": "project-readiness-emitter",
                "argv": list(DEFAULT_EMITTER),
                "timeout_seconds": 900,
            },
            "require_dynamic_mission": False,
        }, f"convention:{DEFAULT_EMITTER[1]}"

    raise ProjectOrganizationLaunchError(
        f"no tracked {PROJECT_LAUNCH_FILENAME} or conventional "
        f"{DEFAULT_EMITTER[1]} emitter exists",
        code="launch_contract_not_found",
    )


def _validated_command(
    root: Path,
    raw: Any,
    *,
    default_id: str,
) -> Dict[str, Any]:
    if not isinstance(raw, dict):
        raise ProjectOrganizationLaunchError(
            f"command {default_id} must be an object",
            code="invalid_launch_contract",
        )
    identifier = str(raw.get("id") or default_id).strip()
    argv = raw.get("argv")
    if (
        not identifier
        or not isinstance(argv, list)
        or not argv
        or any(not isinstance(value, str) or not value for value in argv)
        or any("\0" in value for value in argv)
    ):
        raise ProjectOrganizationLaunchError(
            f"command {default_id} has an invalid id or argv",
            code="invalid_launch_contract",
        )
    timeout = int(raw.get("timeout_seconds", 900))
    if timeout < 1 or timeout > 1800:
        raise ProjectOrganizationLaunchError(
            f"command {identifier} timeout must be between 1 and 1800 seconds",
            code="invalid_launch_contract",
        )
    executable = argv[0]
    if "/" in executable:
        supplied = Path(executable)
        if supplied.is_absolute() or ".." in supplied.parts:
            raise ProjectOrganizationLaunchError(
                f"command {identifier} executable must be project-relative",
                code="unsafe_contract_command",
            )
        lexical = Path(os.path.abspath(root / supplied))
        try:
            lexical.relative_to(root)
        except ValueError as exc:
            raise ProjectOrganizationLaunchError(
                f"command {identifier} executable escapes the project root",
                code="unsafe_contract_command",
            ) from exc
        if not lexical.exists():
            raise ProjectOrganizationLaunchError(
                f"command {identifier} executable does not exist: {executable}",
                code="missing_contract_command",
            )
    elif shutil.which(executable) is None:
        raise ProjectOrganizationLaunchError(
            f"command {identifier} executable is not available: {executable}",
            code="missing_contract_command",
        )
    return {"id": identifier, "argv": list(argv), "timeout_seconds": timeout}


def _run_contract_command(
    root: Path,
    command: Dict[str, Any],
    *,
    preserve_stdout: bool = False,
) -> Dict[str, Any]:
    environment = os.environ.copy()
    environment["RECCLI_PROJECT_ORGANIZATION_LAUNCH"] = "1"
    try:
        completed = subprocess.run(
            command["argv"],
            cwd=root,
            env=environment,
            capture_output=True,
            text=True,
            timeout=command["timeout_seconds"],
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise ProjectOrganizationLaunchError(
            f"{command['id']} timed out after {command['timeout_seconds']} seconds",
            code="preflight_timeout",
            detail={"command": command["id"]},
        ) from exc
    stdout = completed.stdout or ""
    result = {
        "id": command["id"],
        "argv": command["argv"],
        "exit_code": completed.returncode,
        "stdout": stdout if preserve_stdout else stdout[-12_000:],
        "stderr": (completed.stderr or "")[-12_000:],
    }
    if completed.returncode != 0:
        raise ProjectOrganizationLaunchError(
            f"{command['id']} failed with exit {completed.returncode}",
            code="preflight_failed",
            detail=result,
        )
    return result


def _validate_dynamic_selection(
    root: Path,
    emitted: Dict[str, Any],
    launch_arguments: Dict[str, Any],
    *,
    required: bool,
) -> Dict[str, Any]:
    selection = emitted.get("mission_selection")
    if not isinstance(selection, dict):
        if required:
            raise ProjectOrganizationLaunchError(
                "project emitter did not provide required dynamic mission selection",
                code="dynamic_mission_missing",
            )
        return {
            "mode": "emitter_default",
            "mission_id": None,
            "mission_sha256": _sha256_text(launch_arguments["mission"]),
            "checked_head": _git(root, "rev-parse", "HEAD"),
            "warning": (
                "compatibility emitter did not provide a verified dynamic "
                "mission selection"
            ),
        }
    required_strings = (
        "mode",
        "mission_id",
        "mission_path",
        "mission_sha256",
        "checked_head",
        "state_fingerprint",
        "reason",
    )
    if any(
        not isinstance(selection.get(key), str)
        or not selection[key].strip()
        for key in required_strings
    ):
        raise ProjectOrganizationLaunchError(
            "dynamic mission selection is missing required string fields",
            code="invalid_dynamic_mission",
        )
    if selection["mode"] != "dynamic":
        raise ProjectOrganizationLaunchError(
            "mission_selection.mode must be dynamic",
            code="invalid_dynamic_mission",
        )
    current_head = _git(root, "rev-parse", "HEAD")
    if selection["checked_head"] != current_head:
        raise ProjectOrganizationLaunchError(
            "dynamic mission selection was evaluated against a stale Git HEAD",
            code="stale_dynamic_mission",
            detail={
                "selected_head": selection["checked_head"],
                "current_head": current_head,
            },
        )
    expected_sha = _sha256_text(launch_arguments["mission"])
    if (
        selection["mission_sha256"] != expected_sha
        or not SHA256_RE.fullmatch(selection["state_fingerprint"])
    ):
        raise ProjectOrganizationLaunchError(
            "dynamic mission hashes do not match the emitted launch request",
            code="invalid_dynamic_mission",
        )
    mission_path = _tracked_relative_path(
        root,
        selection["mission_path"],
        label="selected organization mission",
    )
    if mission_path.read_text(encoding="utf-8").strip() != launch_arguments["mission"]:
        raise ProjectOrganizationLaunchError(
            "selected mission file bytes do not match the emitted mission",
            code="dynamic_mission_mismatch",
        )
    return dict(selection)


def _validate_emitted_launch(
    root: Path,
    emitted: Dict[str, Any],
    *,
    require_dynamic_mission: bool,
) -> tuple[Dict[str, Any], Dict[str, Any]]:
    if emitted.get("status") != "ready":
        raise ProjectOrganizationLaunchError(
            f"project emitter status is not ready: {emitted.get('status')}",
            code="project_not_ready",
            detail={"emitted_status": emitted.get("status")},
        )
    launch_arguments = emitted.get("start_organization")
    if not isinstance(launch_arguments, dict):
        raise ProjectOrganizationLaunchError(
            "project emitter did not provide start_organization arguments",
            code="invalid_emitter_output",
        )
    unknown = sorted(set(launch_arguments) - ALLOWED_START_ARGUMENTS)
    if unknown:
        raise ProjectOrganizationLaunchError(
            f"project emitter supplied unsupported launch arguments: {unknown}",
            code="invalid_emitter_output",
        )
    if not isinstance(launch_arguments.get("mission"), str) or not launch_arguments[
        "mission"
    ].strip():
        raise ProjectOrganizationLaunchError(
            "project emitter supplied an empty mission",
            code="invalid_emitter_output",
        )
    emitted_root = discover_project_root(
        Path(str(launch_arguments.get("working_directory", "")))
        .expanduser()
        .resolve()
    )
    if emitted_root is None or emitted_root.resolve() != root:
        raise ProjectOrganizationLaunchError(
            "emitted working_directory does not resolve to the launch project root",
            code="invalid_emitter_output",
        )
    selection = _validate_dynamic_selection(
        root,
        emitted,
        launch_arguments,
        required=require_dynamic_mission,
    )
    return dict(launch_arguments), selection


def _validated_continuation_policy(
    contract: Dict[str, Any],
) -> Optional[Dict[str, Any]]:
    raw = contract.get("continuation_policy")
    if raw is None:
        return None
    if not isinstance(raw, dict) or raw.get("mode") != CONTINUATION_MODE:
        raise ProjectOrganizationLaunchError(
            f"continuation_policy.mode must be {CONTINUATION_MODE}",
            code="invalid_launch_contract",
        )

    def string_set(name: str, default: set[str]) -> set[str]:
        values = raw.get(name, sorted(default))
        if (
            not isinstance(values, list)
            or not values
            or any(not isinstance(value, str) or not value.strip() for value in values)
        ):
            raise ProjectOrganizationLaunchError(
                f"continuation_policy.{name} must be a non-empty string list",
                code="invalid_launch_contract",
            )
        return {value.strip() for value in values}

    statuses = string_set("eligible_statuses", DEFAULT_CONTINUATION_STATUSES)
    # Always eligible, even when a project declares its list explicitly. RecCli
    # invented this status after existing contracts were written, so those
    # contracts cannot name it; and an ineligible latest status does not merely
    # skip continuation, it makes the launch RAISE. Adding it to the defaults was
    # not enough: the defaults are consulted only when the field is omitted, so
    # every project that declares eligible_statuses (including the one this work
    # was written for) stayed bricked.
    statuses = statuses | {"no_experiment_contract"}
    unknown_statuses = statuses - TERMINAL_STATUSES
    if unknown_statuses:
        raise ProjectOrganizationLaunchError(
            "continuation_policy has unknown terminal statuses: "
            f"{sorted(unknown_statuses)}",
            code="invalid_launch_contract",
        )
    readiness = string_set(
        "eligible_promotion_readiness",
        DEFAULT_CONTINUATION_READINESS,
    )
    return {
        "mode": CONTINUATION_MODE,
        "eligible_statuses": statuses,
        "eligible_promotion_readiness": readiness,
        "carry_experiment_budget": bool(
            raw.get("carry_experiment_budget", False),
        ),
    }


def _read_json_object(path: Path, *, label: str) -> tuple[Dict[str, Any], bytes]:
    try:
        payload = path.read_bytes()
        value = json.loads(payload)
    except (OSError, json.JSONDecodeError) as exc:
        raise ProjectOrganizationLaunchError(
            f"cannot read {label}: {exc}",
            code="terminal_conclusion_missing",
        ) from exc
    if not isinstance(value, dict):
        raise ProjectOrganizationLaunchError(
            f"{label} must be a JSON object",
            code="terminal_conclusion_missing",
        )
    return value, payload


def _latest_terminal_record(
    root: Path,
    *,
    excluded_run_ids: Optional[set[str]] = None,
) -> Optional[Dict[str, Any]]:
    listed = list_organization_runs(str(root), limit=100)
    runs = listed.get("runs", [])
    if not isinstance(runs, list) or not runs:
        return None
    excluded = excluded_run_ids or set()
    latest = next(
        (
            run for run in runs
            if isinstance(run, dict)
            and str(run.get("run_id") or "") not in excluded
        ),
        None,
    )
    if latest is None:
        return None
    status = str(latest.get("status") or "unknown")
    if status not in TERMINAL_STATUSES:
        return None
    run_dir = Path(str(latest.get("run_dir") or "")).expanduser().resolve()
    organization_root = (
        root / "devsession" / "agent-organizations"
    ).resolve()
    try:
        run_dir.relative_to(organization_root)
    except ValueError as exc:
        raise ProjectOrganizationLaunchError(
            "latest terminal run escapes the project organization directory",
            code="terminal_conclusion_invalid",
        ) from exc
    run, _ = _read_json_object(
        run_dir / "run.json",
        label="latest terminal run metadata",
    )
    conclusion, conclusion_bytes = _read_json_object(
        run_dir / "run-conclusion.json",
        label="latest terminal run conclusion",
    )
    operator_decision: Optional[Dict[str, Any]] = None
    operator_decision_path = run_dir / "operator-decision.json"
    if operator_decision_path.is_file():
        operator_decision, _ = _read_json_object(
            operator_decision_path,
            label="latest terminal operator decision",
        )
    run_id = str(latest.get("run_id") or run.get("run_id") or "")
    if (
        not run_id
        or conclusion.get("schema") != "reccli.organization-run-conclusion.v1"
        or str(conclusion.get("run_id") or "") != run_id
        or str(conclusion.get("terminal_status") or "") != status
    ):
        raise ProjectOrganizationLaunchError(
            "latest terminal conclusion identity does not match its run",
            code="terminal_conclusion_invalid",
        )
    return {
        "run_id": run_id,
        "run_dir": run_dir,
        "status": status,
        "run": run,
        "conclusion": conclusion,
        "conclusion_sha256": hashlib.sha256(conclusion_bytes).hexdigest(),
        "operator_decision": operator_decision,
    }


def _retryable_infrastructure_failure(terminal: Dict[str, Any]) -> bool:
    """Identify a failed supervisor run that cannot author a successor mission."""
    conclusion = terminal.get("conclusion")
    if not isinstance(conclusion, dict):
        return False
    return bool(
        terminal.get("status") == "failed"
        and conclusion.get("generated_by") == "host-fallback"
        and conclusion.get("canonical_effects_applied") is False
        and conclusion.get("promotion_readiness") == "no_candidate"
        and not conclusion.get("verified_candidate")
        and not conclusion.get("promotion_candidate")
        and not conclusion.get("promotion_request")
        and conclusion.get("infrastructure_failures")
    )


def _bounded_conclusion_view(conclusion: Dict[str, Any]) -> Dict[str, Any]:
    """Keep successor context useful without copying an unbounded transcript."""

    def text(value: Any, limit: int = 1_200) -> str:
        return str(value or "")[:limit]

    def strings(
        name: str,
        *,
        count: int = 3,
        chars: int = 500,
    ) -> List[str]:
        values = conclusion.get(name)
        if not isinstance(values, list):
            return []
        return [
            text(value, chars)
            for value in values[:count]
            if str(value).strip()
        ]

    summary = text(conclusion.get("summary"))
    summary = re.sub(
        r"\b(\d+)-turn limit(?=:\s*\d+\s+working rounds\b)",
        r"\1-round limit",
        summary,
    )
    summary = re.sub(
        r"\b(\d+)\s+working turns plus\s+(\d+)\s+closeout turns\b",
        r"\1 working rounds plus \2 closeout rounds",
        summary,
    )
    return {
        "summary": summary,
        "accomplishments": strings("accomplishments", count=2),
        "conclusive_findings": strings("conclusive_findings"),
        "evidence_and_tests": strings("evidence_and_tests", count=2),
        "scientific_or_product_blockers": strings(
            "scientific_or_product_blockers",
        ),
        "infrastructure_failures": strings(
            "infrastructure_failures",
            count=2,
        ),
        "unresolved": strings("unresolved"),
        "promotion_readiness": text(conclusion.get("promotion_readiness"), 200),
        "next_action": text(conclusion.get("next_action"), 800),
        "limitations": strings("limitations", count=2),
    }


def _rejected_candidates(root: Path) -> List[Dict[str, Any]]:
    """Every candidate a human has rejected anywhere in this project's history.

    A rejection is permanent, but the scrub used to see only the LATEST run's own
    operator-decision.json. That made the prohibition last exactly one
    generation: gen-1 is rejected and gen-2 is scrubbed, but gen-2 terminates
    without a decision of its own, so gen-3 inherits gen-2's conclusion with the
    dead artifact back in it.

    Auto-terminated statuses make this the common path rather than a corner: a
    run that ends because it authored no experiment contract is never adjudicated
    by a human, so it never has a decision file at all.
    """
    decisions: List[Dict[str, Any]] = []
    try:
        listed = list_organization_runs(str(root), limit=100)
    except Exception:
        return decisions
    organization_root = (root / "devsession" / "agent-organizations").resolve()
    for run in listed.get("runs", []) or []:
        if not isinstance(run, dict):
            continue
        raw_dir = str(run.get("run_dir") or "")
        if not raw_dir:
            continue
        try:
            run_dir = Path(raw_dir).expanduser().resolve()
            run_dir.relative_to(organization_root)
        except (ValueError, OSError):
            continue
        path = run_dir / "operator-decision.json"
        if not path.is_file():
            continue
        try:
            decision = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if (
            isinstance(decision, dict)
            and decision.get("decision") == "rejected"
            and decision.get("candidate")
        ):
            decisions.append(decision)
    return decisions


def _scrub_rejected_candidate(
    view: Dict[str, Any],
    operator_decision: Optional[Dict[str, Any]],
) -> Dict[str, Any]:
    """Remove a rejected candidate from what a successor run inherits.

    A recorded operator decision put it plainly: a rejected candidate "must not
    seed or satisfy a successor mission." A prose warning appended after the
    parent conclusion was not enough, because the conclusion itself was still
    handed over intact, and its `next_action` was written by a run whose entire
    subject was that candidate. Two successors then spent every turn
    re-adjudicating a dead artifact: reconciling authority documents, arguing
    about a docstring adjective, producing review dossiers about the review.

    Carrying forward what was LEARNED is fine and is preserved here. What is
    removed is the artifact as the object of work:

      * `next_action` is replaced outright. It is the directive field, and a
        directive produced by a run about a rejected candidate points at the
        rejected candidate by construction.
      * `accomplishments` is dropped. A run whose only output was rejected has
        no accomplishment to build on, and listing one invites resumption.
      * Candidate identifiers are redacted everywhere else, so the successor
        cannot address the artifact even incidentally.

    Findings, evidence, blockers, limitations and unresolved questions all
    survive: those are the lesson, and the lesson is what should carry.
    """
    if not isinstance(operator_decision, dict):
        return view
    if operator_decision.get("decision") != "rejected":
        return view

    candidate = str(operator_decision.get("candidate") or "").strip()
    scrubbed = dict(view)

    identifiers = [candidate] if len(candidate) >= 7 else []
    if len(candidate) >= 12:
        # Short SHAs appear in prose far more often than full ones.
        identifiers.extend(candidate[:n] for n in (12, 10, 8, 7))

    def redact(text: str) -> str:
        for ident in identifiers:
            text = text.replace(ident, "[rejected candidate]")
        return text

    def mentions(text: str) -> bool:
        return any(ident in text for ident in identifiers)

    for key, value in list(scrubbed.items()):
        if isinstance(value, str):
            scrubbed[key] = redact(value)
        elif isinstance(value, list):
            scrubbed[key] = [
                redact(item) if isinstance(item, str) else item
                for item in value
                if not (isinstance(item, str) and mentions(item))
            ]

    scrubbed["accomplishments"] = []
    scrubbed["next_action"] = (
        "Superseded. The parent run's candidate was rejected by the human "
        "operator and must not seed or satisfy this mission. Select new work "
        "against the current repository and the project's active contracts. Do "
        "not resume, repackage, re-review, or measure progress against the "
        "rejected artifact."
    )
    return scrubbed


def _continuation_mission(
    root: Path,
    terminal: Dict[str, Any],
    base_selection: Dict[str, Any],
) -> str:
    conclusion = terminal["conclusion"]
    current_head = _git(root, "rev-parse", "HEAD")
    view = _bounded_conclusion_view(conclusion)
    operator_decision = terminal.get("operator_decision")
    # Scrub before rendering. The rejection notice below is guidance; this is
    # the part that actually removes the artifact from the successor's reach.
    #
    # Apply every rejection this project has recorded, not just the latest run's
    # own. A rejection is permanent, and the run that inherits it is usually the
    # one with no decision file of its own.
    for decision in _rejected_candidates(root):
        view = _scrub_rejected_candidate(view, decision)
    view = _scrub_rejected_candidate(view, operator_decision)
    rejection = ""
    if (
        isinstance(operator_decision, dict)
        and operator_decision.get("decision") == "rejected"
    ):
        rejection = f"""
## Binding human rejection

The human operator rejected exact candidate
`{operator_decision.get('candidate')}`.

Reason: {operator_decision.get('reason')}

Do not revive, re-review, repackage, or use that candidate as evidence of
progress. Preserve only its compact failed-attempt lesson. A successor must
earn progress against its own stated current goal and host-bound evaluator
baseline.
"""
    return f"""# Successor mission from terminal organization conclusion

Parent run: `{terminal['run_id']}`
Parent terminal status: `{terminal['status']}`
Parent conclusion SHA-256: `{terminal['conclusion_sha256']}`
Current launch HEAD: `{current_head}`
Project-selected baseline mission: `{base_selection.get('mission_id') or 'emitter-default'}`

Independently verify the parent conclusion against the current repository,
project authority, primary evidence, and reproducible tests. Then execute the
smallest reversible next action that remains justified. The parent conclusion
is a handoff, not authority, and its recommendation may be corrected.

Do not repeat work listed as conclusively accomplished unless a concrete
contradiction requires reproduction. Do not merely restate blockers. When a
blocker requires a contract, design, or acceptance decision, complete all
reversible work first: research primary sources, compare explicit alternatives,
define falsifiable predicates and failure semantics, create truth-known tests
or prototypes where existing authority permits, and prepare one exact reviewed
decision dossier. Use `pending_human` only when a specific irreversible or
authority-changing choice remains after that work. If existing authority and
evidence already justify behavior, delegate a bounded worker implementation,
test it, and route its exact candidate through adversarial review.

Preserve all project-owned evidence, protected-path, experiment-budget,
promotion, and human-authorization boundaries supplied with this run. A
terminal request for human involvement does not prohibit reversible proposal
work; it prohibits agents from granting themselves the final authority.

## Parent terminal conclusion

```json
{json.dumps(view, indent=2, ensure_ascii=False)}
```

{rejection}

The historical mission remains available in the parent run's durable
`run.json`; it is not copied into this successor. Current tracked project
contracts define the active scope and authority boundaries.

## Required final output

State what new reversible work was completed, what parent claims were confirmed
or corrected, the exact candidate or decision dossier produced, the remaining
human decision if any, and the single next action. Never call a report-only
commit an implementation candidate."""


def _apply_terminal_continuation(
    root: Path,
    arguments: Dict[str, Any],
    selection: Dict[str, Any],
    policy: Optional[Dict[str, Any]],
) -> tuple[Dict[str, Any], Dict[str, Any]]:
    if policy is None:
        return arguments, selection
    terminal = _latest_terminal_record(root)
    skipped_retryable_runs: List[str] = []
    while (
        terminal is not None
        and _retryable_infrastructure_failure(terminal)
    ):
        skipped_retryable_runs.append(terminal["run_id"])
        terminal = _latest_terminal_record(
            root,
            excluded_run_ids=set(skipped_retryable_runs),
        )
    if terminal is None:
        return arguments, {
            **selection,
            "skipped_retryable_run_ids": skipped_retryable_runs,
        }
    conclusion = terminal["conclusion"]
    readiness = str(conclusion.get("promotion_readiness") or "")
    if (
        terminal["status"] not in policy["eligible_statuses"]
        or readiness not in policy["eligible_promotion_readiness"]
    ):
        raise ProjectOrganizationLaunchError(
            "latest terminal organization is not eligible for autonomous "
            "continuation; resolve its review or approval state first",
            code="continuation_not_authorized",
            detail={
                "run_id": terminal["run_id"],
                "status": terminal["status"],
                "promotion_readiness": readiness,
            },
        )
    if conclusion.get("generated_by") != "lead":
        raise ProjectOrganizationLaunchError(
            "latest terminal conclusion was not produced by the organization "
            "lead and cannot drive an autonomous successor",
            code="continuation_not_authorized",
            detail={"run_id": terminal["run_id"]},
        )
    if conclusion.get("canonical_effects_applied") is not False:
        raise ProjectOrganizationLaunchError(
            "latest terminal conclusion does not certify that canonical effects "
            "were withheld",
            code="continuation_not_authorized",
            detail={"run_id": terminal["run_id"]},
        )
    updated = dict(arguments)
    updated["mission"] = _continuation_mission(root, terminal, selection)
    updated["continuation_from_run_id"] = terminal["run_id"]
    updated["continuation_conclusion_sha256"] = terminal[
        "conclusion_sha256"
    ]
    updated["mission_origin"] = "terminal-conclusion"
    if policy["carry_experiment_budget"]:
        budget = conclusion.get("experiment_budget")
        if isinstance(budget, dict):
            try:
                remaining = max(0, int(budget.get("remaining")))
                configured = max(0, int(updated.get("max_experiments", remaining)))
                updated["max_experiments"] = min(configured, remaining)
            except (TypeError, ValueError):
                pass
    continuation_selection = {
        "mode": "terminal_continuation",
        "mission_id": f"continuation:{terminal['run_id']}",
        "mission_sha256": _sha256_text(updated["mission"]),
        "checked_head": _git(root, "rev-parse", "HEAD"),
        "state_fingerprint": _sha256_text(
            f"{terminal['conclusion_sha256']}\0"
            f"{selection.get('state_fingerprint') or ''}"
        ),
        "reason": (
            "The tracked project contract opted into continuation from the "
            "latest lead-authored terminal conclusion."
        ),
        "parent_run_id": terminal["run_id"],
        "parent_terminal_status": terminal["status"],
        "parent_promotion_readiness": readiness,
        "parent_conclusion_sha256": terminal["conclusion_sha256"],
        "base_selection": selection,
        "skipped_retryable_run_ids": skipped_retryable_runs,
    }
    return updated, continuation_selection


def _blocking_run(root: Path) -> Optional[Dict[str, Any]]:
    listed = list_organization_runs(str(root), limit=100)
    for run in listed.get("runs", []):
        status = str(run.get("status") or "unknown")
        if run.get("approval_pending") or status == "completed_pending_human":
            return {**run, "blocker": "human_approval_required"}
        if status not in TERMINAL_STATUSES or run.get("process_live") is True:
            return {**run, "blocker": "organization_already_active"}
    return None


@contextmanager
def _launch_lock(root: Path) -> Iterator[None]:
    git_dir_raw = _git(root, "rev-parse", "--git-dir")
    git_dir = Path(git_dir_raw)
    if not git_dir.is_absolute():
        git_dir = root / git_dir
    lock_path = git_dir.resolve() / "reccli-project-organization-launch.lock"
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    handle = lock_path.open("a+", encoding="utf-8")
    try:
        try:
            import fcntl

            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        except (ImportError, OSError):
            pass
        yield
    finally:
        try:
            import fcntl

            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        except (ImportError, OSError):
            pass
        handle.close()


def start_project_organization(
    working_directory: str,
    *,
    open_console: bool = True,
    console_port: int = 8777,
) -> Dict[str, Any]:
    """Run a project's tracked launch contract and start exactly one run."""
    root = discover_project_root(
        Path(working_directory).expanduser().resolve(),
    )
    if root is None:
        raise ProjectOrganizationLaunchError(
            f"no RecCli/Git project found from {working_directory}",
            code="project_not_found",
        )
    root = root.resolve()
    contract, contract_source = _load_contract(root)
    preflight_specs = contract.get("preflight_commands", [])
    if not isinstance(preflight_specs, list):
        raise ProjectOrganizationLaunchError(
            "preflight_commands must be a list",
            code="invalid_launch_contract",
        )
    preflights = [
        _validated_command(root, value, default_id=f"preflight-{index + 1}")
        for index, value in enumerate(preflight_specs)
    ]
    emitter = _validated_command(
        root,
        contract.get("emitter_command"),
        default_id="organization-emitter",
    )
    require_dynamic = bool(contract.get("require_dynamic_mission", False))
    continuation_policy = _validated_continuation_policy(contract)

    from .organization_launch import (
        launch_organization_console,
        start_organization_from_arguments,
    )

    with _launch_lock(root):
        blocking = _blocking_run(root)
        if blocking is not None:
            console = (
                launch_organization_console(
                    root,
                    port=int(console_port),
                    open_browser=open_console,
                )
                if open_console
                else None
            )
            return {
                "status": (
                    "approval_required"
                    if blocking["blocker"] == "human_approval_required"
                    else "already_running"
                ),
                "project_root": str(root),
                "run_id": blocking.get("run_id"),
                "run_dir": blocking.get("run_dir"),
                "blocker": blocking["blocker"],
                "existing_run": blocking,
                "console": console,
                "detail": (
                    "RecCli refused to launch a duplicate organization. "
                    "Resolve the existing run before starting another."
                ),
            }

        preflight_results = [
            _run_contract_command(root, command) for command in preflights
        ]
        emitter_result = _run_contract_command(
            root,
            emitter,
            preserve_stdout=True,
        )
        try:
            emitted = json.loads(emitter_result["stdout"])
        except json.JSONDecodeError as exc:
            raise ProjectOrganizationLaunchError(
                "project organization emitter did not return one JSON object",
                code="invalid_emitter_output",
                detail=emitter_result,
            ) from exc
        if not isinstance(emitted, dict):
            raise ProjectOrganizationLaunchError(
                "project organization emitter output must be a JSON object",
                code="invalid_emitter_output",
            )
        arguments, selection = _validate_emitted_launch(
            root,
            emitted,
            require_dynamic_mission=require_dynamic,
        )
        arguments, selection = _apply_terminal_continuation(
            root,
            arguments,
            selection,
            continuation_policy,
        )
        # Recheck after preflights because a long validation command can overlap
        # a launch from another host process that does not share this lock.
        blocking = _blocking_run(root)
        if blocking is not None:
            raise ProjectOrganizationLaunchError(
                f"organization {blocking.get('run_id')} became active during preflight",
                code="organization_already_active",
                detail={"existing_run": blocking},
            )
        started = start_organization_from_arguments(arguments)
        console = (
            launch_organization_console(
                root,
                port=int(console_port),
                open_browser=open_console,
            )
            if open_console
            else None
        )
        return {
            **started,
            "project_root": str(root),
            "launch_contract": {
                "schema": PROJECT_LAUNCH_SCHEMA,
                "source": contract_source,
                "dynamic_mission_required": require_dynamic,
                "continuation_mode": (
                    continuation_policy["mode"]
                    if continuation_policy else None
                ),
                "experiment_budget_scope": (
                    (
                        "chain"
                        if continuation_policy["carry_experiment_budget"]
                        else "per_run"
                    )
                    if continuation_policy
                    else None
                ),
            },
            "mission_selection": selection,
            "preflights": [
                {
                    "id": item["id"],
                    "argv": item["argv"],
                    "exit_code": item["exit_code"],
                    "stdout": item["stdout"],
                    "stderr": item["stderr"],
                }
                for item in preflight_results
            ],
            "emitter": {
                "id": emitter_result["id"],
                "argv": emitter_result["argv"],
                "exit_code": emitter_result["exit_code"],
            },
            "console": console,
        }


def start_project_organization_result(
    working_directory: str,
    *,
    open_console: bool = True,
    console_port: int = 8777,
) -> Dict[str, Any]:
    """Never-throws result wrapper used by MCP and other user-facing surfaces."""
    try:
        return start_project_organization(
            working_directory,
            open_console=open_console,
            console_port=console_port,
        )
    except ProjectOrganizationLaunchError as exc:
        return {
            "status": "launch_blocked",
            "code": exc.code,
            "error": str(exc),
            "detail": exc.detail,
        }
    except Exception as exc:
        return {
            "status": "failed_to_start",
            "code": "unexpected_launch_error",
            "error": str(exc),
        }
