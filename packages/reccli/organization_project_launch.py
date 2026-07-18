"""Atomic project-owned organization launch contracts.

Projects keep mission selection, scientific policy, and readiness checks in
their own repository. RecCli validates a small tracked contract, runs its
commands without a shell, verifies the exact dynamically selected mission, and
then launches the emitted organization request without rewriting it.
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
    "max_experiments",
}
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


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


def _run_contract_command(root: Path, command: Dict[str, Any]) -> Dict[str, Any]:
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
    result = {
        "id": command["id"],
        "argv": command["argv"],
        "exit_code": completed.returncode,
        "stdout": (completed.stdout or "")[-12_000:],
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
        emitter_result = _run_contract_command(root, emitter)
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
