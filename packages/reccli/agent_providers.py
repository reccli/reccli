"""Subscription-auth provider adapters for agent audit execution."""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any, Dict, List


SUPPORTED_PROVIDERS = {"none", "claude", "codex"}

QUOTA_ERROR_PATTERNS = [
    re.compile(r"hit your usage limit", re.IGNORECASE),
    re.compile(r"upgrade to (?:pro|plus)", re.IGNORECASE),
    re.compile(r"\brate.?limit", re.IGNORECASE),
    re.compile(r"\bquota\b", re.IGNORECASE),
    re.compile(r"too many requests", re.IGNORECASE),
    re.compile(r"insufficient quota", re.IGNORECASE),
    re.compile(r"credit balance is too low", re.IGNORECASE),
    re.compile(r"\b429\b.*(?:rate|limit|quota|too many)", re.IGNORECASE),
]


def detect_quota_error(stderr_text: str, returncode: int = 1) -> bool:
    """Return True if provider output looks like a quota/rate-limit error.

    Only inspects stderr — provider CLIs surface auth/quota failures there.
    Stdout is the agent's structured findings, which routinely mention
    "rate limit"/"quota" as topics in the audited code (e.g. a billing audit's
    findings mention rate-limit middleware) and would trigger false positives.

    Also gated on returncode: a successful exit (returncode == 0) means the
    provider served the request, so any text match in stderr is informational
    (e.g. codex CLI echoing the prompt or printing usage stats), not a wall.
    """
    if returncode == 0:
        return False
    return any(pattern.search(stderr_text or "") for pattern in QUOTA_ERROR_PATTERNS)


def build_agent_prompt(context_pack_path: Path, instructions_path: Path, agent_id: str) -> str:
    missing = [
        str(path)
        for path in (context_pack_path, instructions_path)
        if not path.exists()
    ]
    if missing:
        raise FileNotFoundError(f"Missing audit artifact(s): {', '.join(missing)}")

    context_pack = context_pack_path.read_text(encoding="utf-8")
    instructions = instructions_path.read_text(encoding="utf-8")
    return f"""Run the RecCli feature audit described by these local artifacts.

Instructions: {instructions_path}
Context pack: {context_pack_path}
Agent ID: {agent_id}

The full instructions and context pack are included below. Treat this as a read-only audit.
Return only the requested JSON object. Every finding must include a concrete repro_path or
code_reference. Reject vague concerns.

## Instructions

{instructions}

## Context Pack JSON

```json
{context_pack}
```
"""


SESSION_SIGNAL_RE = re.compile(r"<!--\s*session-signal\s*:[^>]*-->")


def _extract_first_json_object(text: str) -> str | None:
    """Return the first balanced top-level JSON object substring, or None.

    Walks from the first `{`, counting braces while respecting JSON string
    literals and backslash escapes. Immune to triple-backtick fences and other
    delimiters that may appear inside string values (e.g. an agent quoting a
    code block in `code_reference`).
    """
    start = text.find("{")
    if start < 0:
        return None
    depth = 0
    in_string = False
    escape = False
    for i in range(start, len(text)):
        ch = text[i]
        if in_string:
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == '"':
                in_string = False
            continue
        if ch == '"':
            in_string = True
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return text[start : i + 1]
    return None


def parse_findings_output(raw: str) -> Dict[str, Any]:
    """Parse an agent response into a findings bundle.

    Accepts plain JSON, a Markdown-fenced JSON block, or JSON surrounded by
    chatter. Strips ``<!--session-signal:...-->`` trailers that hook-aware
    host CLIs may append. If parsing fails, keep the raw response so the run
    remains inspectable.
    """
    text = (raw or "").strip()
    if not text:
        return {
            "findings": [],
            "rejected_notes": [],
            "raw_response": "",
            "parse_status": "empty",
        }

    text = SESSION_SIGNAL_RE.sub("", text).strip()

    candidates: List[tuple[str, str]] = [("valid_json", text)]
    extracted = _extract_first_json_object(text)
    if extracted and extracted != text:
        candidates.append(("extracted_from_fence", extracted))

    for status, candidate in candidates:
        try:
            parsed = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict):
            parsed.setdefault("findings", [])
            parsed.setdefault("rejected_notes", [])
            parsed["parse_status"] = status
            return parsed

    return {
        "findings": [],
        "rejected_notes": [],
        "raw_response": text,
        "parse_status": "parse_failed",
    }


def _run_claude(
    project_root: Path,
    prompt: str,
    timeout_seconds: int,
    model: str | None = None,
) -> subprocess.CompletedProcess:
    if shutil.which("claude") is None:
        raise RuntimeError("claude CLI not found on PATH")
    args = ["claude", "-p", "--output-format", "text", "--tools", ""]
    if model:
        args += ["--model", model]
    return subprocess.run(
        args,
        input=prompt,
        text=True,
        cwd=project_root,
        capture_output=True,
        timeout=timeout_seconds,
        check=False,
    )


def _run_codex(
    project_root: Path,
    prompt: str,
    output_path: Path,
    timeout_seconds: int,
    model: str | None = None,
) -> subprocess.CompletedProcess:
    if shutil.which("codex") is None:
        raise RuntimeError("codex CLI not found on PATH")
    args = [
        "codex",
        "exec",
        "--cd",
        str(project_root),
        "--sandbox",
        "read-only",
    ]
    if model:
        args += ["--model", model]
    args += [
        "--output-last-message",
        str(output_path),
        "-",
    ]
    return subprocess.run(
        args,
        input=prompt,
        text=True,
        cwd=project_root,
        capture_output=True,
        timeout=timeout_seconds,
        check=False,
    )


def run_provider_prompt(
    provider: str,
    project_root: Path,
    prompt: str,
    timeout_seconds: int = 1800,
    output_path: Path | None = None,
    model: str | None = None,
) -> Dict[str, Any]:
    """Dispatch a raw prompt to a provider CLI and return raw outputs.

    Shared primitive for audit and patch dispatch. Returns stdout/stderr/
    returncode plus the raw model output (which for codex comes from
    --output-last-message rather than stdout). Callers parse the raw output
    according to their own contract.

    When ``model`` is provided, it is passed through to the CLI via
    ``--model``. When ``None``, the CLI's compiled default applies.
    """
    provider = (provider or "").strip().lower()
    if provider == "claude":
        proc = _run_claude(project_root, prompt, timeout_seconds, model=model)
        return {
            "stdout": proc.stdout or "",
            "stderr": proc.stderr or "",
            "returncode": proc.returncode,
            "raw_output": proc.stdout or "",
        }
    if provider == "codex":
        if output_path is None:
            raise ValueError("codex provider requires output_path for --output-last-message")
        proc = _run_codex(project_root, prompt, output_path, timeout_seconds, model=model)
        raw_output = (
            output_path.read_text(encoding="utf-8")
            if output_path.exists()
            else (proc.stdout or "")
        )
        return {
            "stdout": proc.stdout or "",
            "stderr": proc.stderr or "",
            "returncode": proc.returncode,
            "raw_output": raw_output,
        }
    raise ValueError(
        f"run_provider_prompt does not support provider '{provider}'. "
        f"Supported: 'claude', 'codex'."
    )


def run_agent_provider(
    provider: str,
    project_root: Path,
    run_dir: Path,
    context_pack_path: Path,
    agent: Dict[str, Any],
    timeout_seconds: int = 1800,
    model: str | None = None,
) -> Dict[str, Any]:
    provider = (provider or "none").strip().lower()
    if provider not in SUPPORTED_PROVIDERS:
        raise ValueError(f"Unsupported provider '{provider}'. Supported providers: {', '.join(sorted(SUPPORTED_PROVIDERS))}")

    agent_id = agent["agent_id"]
    instructions_path = run_dir / f"{agent_id}_instructions.md"
    stdout_path = run_dir / f"{agent_id}_{provider}_stdout.txt"
    stderr_path = run_dir / f"{agent_id}_{provider}_stderr.txt"
    output_path = run_dir / f"{agent_id}_{provider}_output.txt"
    findings_path = run_dir / f"{agent_id}_findings.json"
    report_path = run_dir / f"{agent_id}_report.md"

    if provider == "none":
        return {
            "agent_id": agent_id,
            "provider": provider,
            "status": "prepared",
            "findings_path": str(findings_path),
            "report_path": str(report_path),
        }

    try:
        prompt = build_agent_prompt(context_pack_path, instructions_path, agent_id)
        if provider == "claude":
            proc = _run_claude(project_root, prompt, timeout_seconds, model=model)
            raw_output = proc.stdout
        else:
            proc = _run_codex(project_root, prompt, output_path, timeout_seconds, model=model)
            raw_output = output_path.read_text(encoding="utf-8") if output_path.exists() else proc.stdout
    except Exception as exc:
        error = {
            "agent_id": agent_id,
            "provider": provider,
            "status": "error",
            "error": str(exc),
        }
        findings_path.write_text(json.dumps(error, indent=2) + "\n", encoding="utf-8")
        return error

    stdout_path.write_text(proc.stdout or "", encoding="utf-8")
    stderr_path.write_text(proc.stderr or "", encoding="utf-8")

    quota_error = detect_quota_error(proc.stderr or "", proc.returncode)

    parsed = parse_findings_output(raw_output)
    parsed.update({
        "agent_id": agent_id,
        "provider": provider,
        "model": model,
        "status": "completed" if proc.returncode == 0 else "failed",
        "returncode": proc.returncode,
        "quota_error": quota_error,
    })
    findings_path.write_text(json.dumps(parsed, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    findings = parsed.get("findings", [])
    report_lines = [
        "# Agent Audit Report",
        "",
        f"Agent: `{agent_id}`",
        f"Provider: `{provider}`",
        f"Status: `{parsed['status']}`",
        f"Parse status: `{parsed.get('parse_status', 'unknown')}`",
        f"Findings: {len(findings)}",
        "",
        "## Findings",
        "",
    ]
    if findings:
        for index, finding in enumerate(findings, 1):
            report_lines.append(f"### {index}. {finding.get('title', 'Untitled finding')}")
            report_lines.append("")
            report_lines.append(f"- Severity: `{finding.get('severity', 'unknown')}`")
            report_lines.append(f"- Confidence: `{finding.get('confidence', 'unknown')}`")
            report_lines.append(f"- Description: {finding.get('description', '')}")
            report_lines.append(f"- Code reference: {finding.get('code_reference', '')}")
            report_lines.append(f"- Repro path: {finding.get('repro_path', '')}")
            report_lines.append(f"- Suggested fix: {finding.get('suggested_fix', '')}")
            report_lines.append("")
    else:
        report_lines.append("No findings returned.")
        report_lines.append("")
    if parsed.get("raw_response"):
        report_lines.extend(["## Raw Response", "", "```text", parsed["raw_response"], "```", ""])
    report_path.write_text("\n".join(report_lines), encoding="utf-8")

    return {
        "agent_id": agent_id,
        "provider": provider,
        "model": model,
        "status": parsed["status"],
        "parse_status": parsed.get("parse_status", "unknown"),
        "returncode": proc.returncode,
        "findings": len(findings),
        "findings_path": str(findings_path),
        "report_path": str(report_path),
        "quota_error": quota_error,
    }


def _skipped_agent_result(provider: str, agent: Dict[str, Any], reason: str) -> Dict[str, Any]:
    return {
        "agent_id": agent["agent_id"],
        "provider": provider,
        "status": "skipped",
        "skip_reason": reason,
    }


def run_audit_agents(
    provider: str,
    project_root: Path,
    run_dir: Path,
    context_pack_path: Path,
    agents: List[Dict[str, Any]],
    timeout_seconds: int = 1800,
    max_concurrency: int = 1,
    model: str | None = None,
) -> List[Dict[str, Any]]:
    """Dispatch audit agents.

    Defaults to sequential dispatch (max_concurrency=1) so that a quota error
    on the first agent aborts the rest of the batch instead of blasting every
    remaining agent against the same exhausted provider. Callers that want
    parallel dispatch must pass max_concurrency explicitly.
    """
    provider = (provider or "none").strip().lower()
    worker_count = max(1, min(max_concurrency, len(agents))) if agents else 1

    if provider == "none" or worker_count == 1:
        results: List[Dict[str, Any]] = []
        abort_reason: str = ""
        for agent in agents:
            if abort_reason:
                results.append(_skipped_agent_result(provider, agent, abort_reason))
                continue
            result = run_agent_provider(
                provider, project_root, run_dir, context_pack_path, agent, timeout_seconds, model=model
            )
            results.append(result)
            if result.get("quota_error"):
                abort_reason = (
                    "Provider quota exhausted on a prior agent in this batch; "
                    "remaining agents skipped to preserve quota. Retry later or "
                    "switch provider."
                )
        return sorted(results, key=lambda item: item.get("agent_id", ""))

    results = []
    with ThreadPoolExecutor(max_workers=worker_count) as executor:
        futures = [
            executor.submit(run_agent_provider, provider, project_root, run_dir, context_pack_path, agent, timeout_seconds, model)
            for agent in agents
        ]
        for future in as_completed(futures):
            results.append(future.result())
    return sorted(results, key=lambda item: item.get("agent_id", ""))
