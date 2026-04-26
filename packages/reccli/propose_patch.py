"""Single-finding unified-diff proposal from a completed audit run.

Audit produces prose findings. Turning a finding into a patch is a different
operation: one agent, one finding, generous per-file budget, fresh file reads
(not the audit's cached context pack), and a git apply --check validation pass.

The diff is never applied. propose_patch_for_finding writes artifacts to the
audit run directory and returns the diff text plus an applies_cleanly flag.
The caller decides whether to run `git apply` against the returned patch.
"""

from __future__ import annotations

import json
import re
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from .agent_providers import (
    SESSION_SIGNAL_RE,
    _extract_first_json_object,
    detect_quota_error,
    run_provider_prompt,
)


DEFAULT_FILE_BUDGET = 50_000
DEFAULT_PATCH_TIMEOUT = 600
MAX_FILES_PER_PATCH = 4
MAX_DIFF_LINES = 50

SUPPORTED_PATCH_PROVIDERS = {"claude", "codex"}


def _utc_stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _read_file_for_patch(path: Path, max_chars: int) -> Dict[str, Any]:
    """Read a file fresh from disk with a soft per-file budget.

    Files within budget are returned in full. Files over budget return the
    last `max_chars` bytes truncated to a line boundary, with the starting
    line number so the diff agent's @@ headers reference real line numbers
    in the file on disk.
    """
    if not path.exists():
        return {"exists": False, "error": "missing"}
    try:
        text = path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return {"exists": True, "error": "not-utf8"}
    except OSError as exc:
        return {"exists": True, "error": str(exc)}

    total_lines = text.count("\n") + (0 if text.endswith("\n") else 1)
    if len(text) <= max_chars:
        return {
            "exists": True,
            "content": text,
            "starting_line": 1,
            "ending_line": total_lines,
            "total_lines": total_lines,
            "truncated": False,
            "chars": len(text),
        }
    tail = text[-max_chars:]
    nl = tail.find("\n")
    if nl >= 0:
        tail = tail[nl + 1 :]
    dropped = text[: len(text) - len(tail)]
    starting_line = dropped.count("\n") + 1
    return {
        "exists": True,
        "content": tail,
        "starting_line": starting_line,
        "ending_line": total_lines,
        "total_lines": total_lines,
        "truncated": True,
        "chars": len(text),
    }


def _finding_target_files(finding: Dict[str, Any]) -> List[str]:
    """Extract unique file paths from a finding's `files` array, in order."""
    seen: List[str] = []
    for entry in finding.get("files", []) or []:
        if isinstance(entry, dict):
            path = entry.get("path")
        else:
            path = entry
        if isinstance(path, str) and path.strip() and path not in seen:
            seen.append(path)
    return seen[:MAX_FILES_PER_PATCH]


def _build_patch_prompt(finding: Dict[str, Any], file_contexts: List[Dict[str, Any]]) -> str:
    finding_json = json.dumps(finding, indent=2, ensure_ascii=False)

    file_blocks: List[str] = []
    for ctx in file_contexts:
        path = ctx.get("path", "<unknown>")
        if not ctx.get("exists"):
            file_blocks.append(f"## {path}\n\n[FILE NOT FOUND]\n")
            continue
        if ctx.get("error"):
            file_blocks.append(f"## {path}\n\n[READ ERROR: {ctx['error']}]\n")
            continue
        header = (
            f"## {path} (lines {ctx['starting_line']}-{ctx['ending_line']} "
            f"of {ctx['total_lines']})"
        )
        if ctx.get("truncated"):
            header += " [tail-truncated to fit budget — line numbers above are real]"
        file_blocks.append(f"{header}\n\n```\n{ctx['content']}\n```\n")
    files_section = "\n".join(file_blocks) if file_blocks else "[NO FILES PROVIDED]"

    return f"""You are generating a single unified diff that fixes one specific bug. Output exactly one diff and nothing else, OR a JSON no_diff explanation. Do not include any prose.

# Bug to fix

```json
{finding_json}
```

# Source files (fresh from disk at call time)

{files_section}

# Output contract

Return EXACTLY ONE of these two shapes.

(A) A unified diff in a fenced code block:

```diff
--- a/path/to/file
+++ b/path/to/file
@@ -OLD_LINE,OLD_COUNT +NEW_LINE,NEW_COUNT @@
 unchanged context line
-removed line
+added line
 unchanged context line
```

(B) A JSON object explaining why no diff is appropriate:

```json
{{"no_diff": true, "reason": "Concrete reason — e.g. 'fix requires creating a new file', 'requires API design judgment', 'patch would exceed {MAX_DIFF_LINES} lines', 'finding is architectural, not patchable as a localized diff'"}}
```

# Hard constraints

- Fix only the issue described in the finding. Do not refactor surrounding code.
- Total changed lines across all files must not exceed {MAX_DIFF_LINES}. If the fix needs more, return (B).
- Use REAL line numbers from the file content above. If a file shows "lines 245-800 of 800", every @@ header must use line numbers from that range.
- Include 3 lines of unchanged context above and below each hunk (standard unified diff format).
- Use a/ and b/ path prefixes (`--- a/path` and `+++ b/path`) so `git apply` can locate files from the project root.
- If the fix would require new files, dependency changes, or judgment about API shape, return (B) with a clear reason rather than guessing.
- Do not include commentary, explanation, or anything outside the single fenced block.
"""


_DIFF_FENCE_RE = re.compile(r"```(?:diff|patch)?\s*\n(.*?)```", re.DOTALL)
_DIFF_HEADER_RE = re.compile(r"^(?:diff --git |--- a/|--- /)", re.MULTILINE)


def _looks_like_diff(body: str) -> bool:
    return bool(_DIFF_HEADER_RE.search(body)) or body.lstrip().startswith("--- ")


def _extract_diff_or_no_diff(raw: str) -> Dict[str, Any]:
    """Parse agent output into either a unified diff or a no_diff record."""
    text = (raw or "").strip()
    if not text:
        return {"parse_status": "empty", "diff": None, "no_diff": None}

    text = SESSION_SIGNAL_RE.sub("", text).strip()

    for match in _DIFF_FENCE_RE.finditer(text):
        body = match.group(1).strip()
        if _looks_like_diff(body):
            return {
                "parse_status": "extracted_from_fence",
                "diff": body + ("" if body.endswith("\n") else "\n"),
                "no_diff": None,
            }

    raw_match = _DIFF_HEADER_RE.search(text)
    if raw_match:
        diff_body = text[raw_match.start() :].strip()
        if diff_body.endswith("```"):
            diff_body = diff_body[:-3].rstrip()
        return {
            "parse_status": "extracted_raw",
            "diff": diff_body + ("" if diff_body.endswith("\n") else "\n"),
            "no_diff": None,
        }

    candidate = _extract_first_json_object(text)
    if candidate:
        try:
            parsed = json.loads(candidate)
        except json.JSONDecodeError:
            parsed = None
        if isinstance(parsed, dict) and parsed.get("no_diff") is True:
            return {
                "parse_status": "no_diff_declared",
                "diff": None,
                "no_diff": parsed,
            }

    return {"parse_status": "parse_failed", "diff": None, "no_diff": None}


def _git_apply_check(project_root: Path, diff_text: str) -> Dict[str, Any]:
    """Run `git apply --check` against the proposed diff."""
    if not (project_root / ".git").exists():
        return {"applies_cleanly": False, "skipped": True, "reason": "not_a_git_repo"}
    try:
        proc = subprocess.run(
            ["git", "apply", "--check", "-"],
            input=diff_text,
            text=True,
            cwd=project_root,
            capture_output=True,
            timeout=30,
            check=False,
        )
    except FileNotFoundError:
        return {"applies_cleanly": False, "skipped": True, "reason": "git_not_found"}
    except subprocess.TimeoutExpired:
        return {"applies_cleanly": False, "skipped": True, "reason": "git_apply_timeout"}

    return {
        "applies_cleanly": proc.returncode == 0,
        "skipped": False,
        "returncode": proc.returncode,
        "stdout": proc.stdout or "",
        "stderr": proc.stderr or "",
    }


def propose_patch_for_finding(
    project_root: Path,
    run_dir: Path,
    agent_id: str,
    finding_index: int,
    provider: str,
    file_budget: int = DEFAULT_FILE_BUDGET,
    timeout_seconds: int = DEFAULT_PATCH_TIMEOUT,
    model: str | None = None,
) -> Dict[str, Any]:
    """Generate a unified-diff patch for one finding from a completed audit.

    Reads files fresh from disk (not from the audit's cached context pack)
    so diff line numbers reflect current state. Writes artifacts under
    ``<run_dir>/patches/<agent_id>_finding_<index>_<stamp>/`` and returns a
    summary dict with the diff path and applicability check.

    Does not apply the diff. The caller runs ``git apply`` if desired.
    """
    provider = (provider or "").strip().lower()
    if provider not in SUPPORTED_PATCH_PROVIDERS:
        raise ValueError(
            f"propose_patch requires provider in {sorted(SUPPORTED_PATCH_PROVIDERS)}, "
            f"got '{provider}'."
        )

    project_root = Path(project_root).resolve()
    run_dir = Path(run_dir).resolve()
    if not run_dir.exists():
        raise FileNotFoundError(f"Audit run directory not found: {run_dir}")

    findings_path = run_dir / f"{agent_id}_findings.json"
    if not findings_path.exists():
        raise FileNotFoundError(f"Agent findings not found: {findings_path}")

    findings_doc = json.loads(findings_path.read_text(encoding="utf-8"))
    findings = findings_doc.get("findings") or []
    if not isinstance(findings, list):
        raise ValueError(f"Agent {agent_id} findings.findings is not a list.")
    if finding_index < 0 or finding_index >= len(findings):
        raise IndexError(
            f"finding_index {finding_index} out of range; agent {agent_id} has "
            f"{len(findings)} findings."
        )

    finding = findings[finding_index]
    target_paths = _finding_target_files(finding)
    if not target_paths:
        raise ValueError(
            f"Finding {finding_index} has no `files` entries; cannot scope a patch."
        )

    file_contexts: List[Dict[str, Any]] = []
    for rel in target_paths:
        ctx = _read_file_for_patch(project_root / rel, file_budget)
        ctx["path"] = rel
        file_contexts.append(ctx)

    stamp = _utc_stamp()
    patch_dir = run_dir / "patches" / f"{agent_id}_finding_{finding_index:02d}_{stamp}"
    patch_dir.mkdir(parents=True, exist_ok=True)

    prompt = _build_patch_prompt(finding, file_contexts)
    (patch_dir / "prompt.md").write_text(prompt, encoding="utf-8")

    codex_output_path = patch_dir / "codex_last_message.txt"
    try:
        dispatch = run_provider_prompt(
            provider=provider,
            project_root=project_root,
            prompt=prompt,
            timeout_seconds=timeout_seconds,
            output_path=codex_output_path if provider == "codex" else None,
            model=model,
        )
    except Exception as exc:
        result = {
            "status": "error",
            "error": str(exc),
            "patch_dir": str(patch_dir),
            "provider": provider,
            "model": model,
            "agent_id": agent_id,
            "finding_index": finding_index,
            "finding_title": finding.get("title", ""),
        }
        (patch_dir / "result.json").write_text(
            json.dumps(result, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
        )
        return result

    (patch_dir / "raw_response.txt").write_text(dispatch["raw_output"], encoding="utf-8")
    (patch_dir / "stdout.txt").write_text(dispatch["stdout"], encoding="utf-8")
    (patch_dir / "stderr.txt").write_text(dispatch["stderr"], encoding="utf-8")

    quota_error = detect_quota_error(dispatch["stderr"], dispatch["returncode"])
    parsed = _extract_diff_or_no_diff(dispatch["raw_output"])

    apply_check: Dict[str, Any] = {
        "applies_cleanly": False,
        "skipped": True,
        "reason": "no_diff_to_check",
    }
    diff_path: Optional[Path] = None
    if parsed.get("diff"):
        diff_path = patch_dir / "patch.diff"
        diff_path.write_text(parsed["diff"], encoding="utf-8")
        apply_check = _git_apply_check(project_root, parsed["diff"])

    if dispatch["returncode"] != 0:
        status = "failed"
    elif parsed["parse_status"] == "no_diff_declared":
        status = "no_diff"
    elif parsed["parse_status"] in {"parse_failed", "empty"}:
        status = parsed["parse_status"]
    else:
        status = "completed"

    result = {
        "status": status,
        "provider": provider,
        "model": model,
        "agent_id": agent_id,
        "finding_index": finding_index,
        "finding_title": finding.get("title", ""),
        "finding_severity": finding.get("severity", ""),
        "target_files": target_paths,
        "patch_dir": str(patch_dir),
        "diff_path": str(diff_path) if diff_path else None,
        "parse_status": parsed["parse_status"],
        "applies_cleanly": apply_check.get("applies_cleanly", False),
        "apply_check": apply_check,
        "no_diff_reason": (parsed.get("no_diff") or {}).get("reason"),
        "returncode": dispatch["returncode"],
        "quota_error": quota_error,
        "file_budget_chars": file_budget,
        "files_truncated": [
            ctx["path"] for ctx in file_contexts if ctx.get("truncated")
        ],
    }
    (patch_dir / "result.json").write_text(
        json.dumps(result, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    return result
