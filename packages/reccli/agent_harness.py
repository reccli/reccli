"""Feature-scoped agent harness run packaging.

The first executable slice is intentionally conservative: build a concrete
context pack and instruction artifacts that an MCP caller can use to run one or
more scoped agents. Dispatching external agents is a later layer.
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from .project.devproject import load_devproject, resolve_devproject_path
from .summarization.redaction import SecretRedactor


DEFAULT_MAX_FILES = 8
DEFAULT_MAX_FILE_CHARS = 12_000
MAX_AGENT_COUNT = 20

GITIGNORE_HEADER = "# RecCli audit artifacts (may contain prior session memory)"
GITIGNORE_ENTRY = "devsession/agent-audits/"
GITIGNORE_ALIASES = {GITIGNORE_ENTRY, "devsession/agent-audits", "devsession/", "devsession/*", "devsession"}
TEXT_EXTENSIONS = {
    ".py", ".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs",
    ".go", ".rs", ".java", ".kt", ".swift", ".rb", ".php",
    ".sh", ".bash", ".zsh", ".c", ".cc", ".cpp", ".h", ".hpp",
    ".md", ".txt", ".rst", ".adoc", ".json", ".yaml", ".yml",
    ".toml", ".ini", ".css", ".scss", ".html",
}
RISK_PATTERNS = [
    (re.compile(r"\bTODO\b|\bFIXME\b|\bHACK\b", re.IGNORECASE), "todo"),
    (re.compile(r"@ts-ignore|eslint-disable|type:\s*ignore", re.IGNORECASE), "ignored-check"),
    (re.compile(r"except\s+Exception|except\s*:", re.IGNORECASE), "broad-exception"),
    (re.compile(r"verify\s*=\s*False|rejectUnauthorized\s*:\s*false", re.IGNORECASE), "disabled-verification"),
    (re.compile(r"api[_-]?key|secret|token|password", re.IGNORECASE), "secret-sensitive"),
]


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _run_stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _slugify(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", (value or "").lower()).strip("-")
    return slug or "target"


def _read_text_file(path: Path, max_chars: int) -> Dict[str, Any]:
    item: Dict[str, Any] = {
        "path": path.as_posix(),
        "exists": path.exists(),
    }
    if not path.exists():
        item["error"] = "missing"
        return item
    if path.suffix.lower() not in TEXT_EXTENSIONS and path.name not in {"Dockerfile", "Makefile"}:
        item["error"] = "unsupported-extension"
        return item
    try:
        text = path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        item["error"] = "not-utf8"
        return item
    except OSError as exc:
        item["error"] = str(exc)
        return item

    total_lines = text.count("\n") + (0 if text.endswith("\n") or not text else 1)
    content = text[:max_chars]
    truncated = len(text) > max_chars
    # ending_line is the last line number for which the agent has content. When
    # truncated, this may be a partial line — the agent should treat any cited
    # line greater than ending_line as outside the visible slice.
    ending_line = content.count("\n") + (0 if content.endswith("\n") or not content else 1)

    item["chars"] = len(text)
    item["truncated"] = truncated
    item["content"] = content
    item["total_lines"] = total_lines
    item["starting_line"] = 1 if content else 0
    item["ending_line"] = ending_line
    return item


def _rel_path(project_root: Path, path: Path) -> str:
    try:
        return path.resolve().relative_to(project_root.resolve()).as_posix()
    except ValueError:
        return path.as_posix()


def _feature_key(feature: Dict[str, Any]) -> str:
    return _slugify(
        " ".join([
            str(feature.get("feature_id", "")),
            str(feature.get("title", "")),
        ])
    )


def _find_feature(document: Dict[str, Any], feature_id: str) -> Optional[Dict[str, Any]]:
    needle = (feature_id or "").strip()
    if not needle:
        return None

    needle_lower = needle.lower()
    needle_slug = _slugify(needle)
    for feature in document.get("features", []):
        fid = str(feature.get("feature_id", ""))
        title = str(feature.get("title", ""))
        if needle == fid:
            return feature
        if needle_lower in {fid.lower(), title.lower()}:
            return feature
        if needle_slug in {_slugify(fid), _slugify(title), _feature_key(feature)}:
            return feature
    return None


def _feature_choices(document: Dict[str, Any], limit: int = 20) -> str:
    lines = []
    for feature in document.get("features", [])[:limit]:
        lines.append(f"- {feature.get('feature_id')}: {feature.get('title', 'Untitled')}")
    remaining = len(document.get("features", [])) - limit
    if remaining > 0:
        lines.append(f"... and {remaining} more")
    return "\n".join(lines) if lines else "No features found in .devproject."


def _collect_risk_signals(project_root: Path, file_paths: List[str], limit: int = 40) -> List[Dict[str, Any]]:
    signals: List[Dict[str, Any]] = []
    for rel in file_paths:
        path = project_root / rel
        if not path.exists() or not path.is_file():
            continue
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except (UnicodeDecodeError, OSError):
            continue
        for line_no, line in enumerate(lines, 1):
            for pattern, kind in RISK_PATTERNS:
                if pattern.search(line):
                    signals.append({
                        "kind": kind,
                        "path": rel,
                        "line": line_no,
                        "text": line.strip()[:240],
                    })
                    break
            if len(signals) >= limit:
                return signals
    return signals


def _collect_session_context(project_root: Path, feature: Dict[str, Any], limit: int = 5) -> List[Dict[str, Any]]:
    """Collect lightweight summaries connected to the feature.

    Session-level summaries can carry PII or secrets pulled from prior
    conversations (customer emails, tokens, etc.). Apply secret redaction at
    the assembly seam so PII never enters the audit context pack, prompt, or
    any provider stderr that echoes input.
    """
    sessions_dir = project_root / "devsession"
    if not sessions_dir.exists():
        return []

    wanted = {str(sid) for sid in feature.get("session_ids", []) if sid}
    summaries: List[Dict[str, Any]] = []
    try:
        from .session.devsession import DevSession
    except Exception:
        return []

    session_files = sorted(
        sessions_dir.glob("*.devsession"),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    feature_terms = {
        str(feature.get("feature_id", "")).lower(),
        str(feature.get("title", "")).lower(),
    }
    feature_terms |= {Path(p).name.lower() for p in feature.get("files_touched", [])[:20]}
    feature_terms = {term for term in feature_terms if term}

    redactor = SecretRedactor(redact_emails=True)

    def _scrub(value: Any) -> Any:
        if isinstance(value, str):
            return redactor.redact_text(value)[0]
        return value

    for session_file in session_files[:30]:
        if session_file.name.startswith(".live_"):
            continue
        try:
            session = DevSession.load(session_file, verify_checksums=False)
        except Exception:
            continue
        summary = getattr(session, "summary", None) or {}
        if not summary:
            continue

        body = json.dumps(summary, ensure_ascii=False).lower()
        session_id = getattr(session, "session_id", session_file.stem)
        if wanted and session_id not in wanted and session_file.stem not in wanted:
            if not any(term in body for term in feature_terms):
                continue
        elif not wanted and not any(term in body for term in feature_terms):
            continue

        summaries.append({
            "session_id": session_id,
            "path": _rel_path(project_root, session_file),
            "overview": _scrub(summary.get("overview", "")),
            "decisions": [
                _scrub(item.get("decision") or str(item))
                for item in summary.get("decisions", [])[:3]
            ],
            "open_issues": [
                _scrub(item.get("issue") or str(item))
                for item in summary.get("open_issues", [])[:3]
            ],
            "next_steps": [
                _scrub(item.get("action") or str(item))
                for item in summary.get("next_steps", [])[:3]
            ],
            "redacted": True,
        })
        if len(summaries) >= limit:
            break
    return summaries


def _ensure_audit_gitignore(project_root: Path) -> Dict[str, Any]:
    """Add `devsession/agent-audits/` to .gitignore if missing.

    Audit artifacts can contain redacted-but-still-sensitive material from
    prior sessions and target-codebase contents. Defense in depth: even if
    the redactor misses something, the artifacts never reach git.

    Returns a small status dict so callers can record what happened.
    """
    if not (project_root / ".git").exists():
        return {"status": "skipped", "reason": "not_a_git_repo"}

    gitignore = project_root / ".gitignore"
    block = f"\n{GITIGNORE_HEADER}\n{GITIGNORE_ENTRY}\n"

    try:
        if not gitignore.exists():
            gitignore.write_text(block.lstrip(), encoding="utf-8")
            return {"status": "created", "path": str(gitignore)}

        content = gitignore.read_text(encoding="utf-8")
        for raw_line in content.splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#"):
                continue
            if line in GITIGNORE_ALIASES:
                return {"status": "already_present", "path": str(gitignore)}

        if not content.endswith("\n"):
            content += "\n"
        gitignore.write_text(content + block, encoding="utf-8")
        return {"status": "appended", "path": str(gitignore)}
    except OSError as exc:
        return {"status": "error", "reason": str(exc)}


def _expand_scope(
    project_root: Path,
    files: Optional[List[str]],
    globs: Optional[List[str]],
) -> List[str]:
    """Expand explicit files + globs into a deduped, ordered list of relative paths.

    - ``files`` entries are taken as-is (after stripping) and kept in input order.
    - ``globs`` are expanded against ``project_root`` via ``Path.glob`` (recursive
      patterns like ``src/**/*.ts`` work natively). Only regular files inside
      project_root that exist on disk are included.
    - Duplicates are dropped while preserving first-occurrence order.
    - Returns a list of POSIX-style relative paths.
    """
    seen: List[str] = []
    seen_set = set()

    def _add(rel: str) -> None:
        if rel and rel not in seen_set:
            seen.append(rel)
            seen_set.add(rel)

    for raw in files or []:
        if not isinstance(raw, str):
            continue
        rel = raw.strip().lstrip("/")
        if not rel:
            continue
        candidate = (project_root / rel).resolve()
        try:
            candidate.relative_to(project_root)
        except ValueError:
            continue
        if candidate.is_file():
            _add(candidate.relative_to(project_root).as_posix())

    for pattern in globs or []:
        if not isinstance(pattern, str):
            continue
        pat = pattern.strip().lstrip("/")
        if not pat:
            continue
        try:
            matches = sorted(project_root.glob(pat))
        except (OSError, ValueError):
            continue
        for match in matches:
            try:
                resolved = match.resolve()
                resolved.relative_to(project_root)
            except ValueError:
                continue
            if resolved.is_file():
                _add(resolved.relative_to(project_root).as_posix())

    return seen


def _agent_assignments(file_paths: List[str], agent_count: int) -> List[Dict[str, Any]]:
    agent_count = max(1, min(agent_count, MAX_AGENT_COUNT))
    return [
        {"agent_id": f"agent_{i + 1:02d}", "assigned_files": list(file_paths)}
        for i in range(agent_count)
    ]


def _mode_task(mode: str) -> str:
    if mode == "audit":
        return (
            "Inspect the assigned feature for concrete bugs, regressions, missing tests, "
            "security risks, data-loss risks, and contract mismatches. Return only findings "
            "with specific evidence."
        )
    return "Run a read-only feature audit and return structured findings."


def _empty_findings(agent: Dict[str, Any], context_pack: Dict[str, Any]) -> Dict[str, Any]:
    feature = context_pack["feature"]
    return {
        "run_id": context_pack["run_id"],
        "agent_id": agent["agent_id"],
        "mode": context_pack["mode"],
        "feature_id": feature.get("feature_id"),
        "feature_title": feature.get("title"),
        "assigned_files": agent["assigned_files"],
        "findings": [],
        "rejected_notes": [],
    }


def _agent_report_markdown(agent: Dict[str, Any], context_pack: Dict[str, Any]) -> str:
    feature = context_pack["feature"]
    assigned = "\n".join(f"- `{path}`" for path in agent["assigned_files"]) or "- No files assigned"
    return f"""# Agent Audit Report

Run ID: `{context_pack['run_id']}`
Agent: `{agent['agent_id']}`
Feature: `{feature.get('feature_id')}` - {feature.get('title', 'Untitled')}

## Assigned Files

{assigned}

## Findings

No findings recorded yet.

## Rejected Notes

None recorded yet.
"""


def _instructions_markdown(context_pack: Dict[str, Any]) -> str:
    mode = context_pack["mode"]
    feature = context_pack["feature"]
    focus = context_pack.get("focus", "").strip()
    focus_section = f"\n## Focus\n\n{focus}\n" if focus else ""
    return f"""# Agent Harness Run

Mode: `{mode}`
Feature: `{feature.get('feature_id')}` - {feature.get('title', 'Untitled')}

## Task

{_mode_task(mode)}
{focus_section}

## Constraints

- Treat this as read-only unless the caller explicitly asks for patch mode.
- Ground every finding in specific files, lines, tests, or session context.
- Use only these severity values: `info`, `low`, `medium`, `high`, `critical`.
- Reject vague concerns that do not have a concrete failure path.
- Do not create GitHub issues, comments, commits, or remote changes from this run package alone.

## Calibration

Reject vague findings. Example rejection:

```json
{{
  "note": "The webhook handler may have race conditions if Stripe sends duplicate events.",
  "reason": "Rejected because it speculates about a possible issue without identifying the specific code path where the race occurs or the input conditions that trigger it."
}}
```

Confidence anchors:

- Use `high` only when specific code demonstrates the issue and you can explain the input or event sequence that triggers it.
- Use `medium` when the code pattern is suspicious and grounded in a real reference, but you cannot fully construct the failing input from the available context.
- Use `low` when the concern is worth human review but the evidence is incomplete.

## Output Contract

Return JSON or Markdown with this structure:

```json
{{
  "findings": [
    {{
      "severity": "info|low|medium|high|critical",
      "title": "Short finding title",
      "description": "Concise description of the issue",
      "files": [{{"path": "path/to/file", "line": 1}}],
      "repro_path": "How to reproduce or trace the problem",
      "code_reference": "Specific code evidence that grounds the finding",
      "suggested_fix": "Bounded fix",
      "confidence": "low|medium|high",
      "verification": ["command or manual check"]
    }}
  ],
  "rejected_notes": [
    {{"note": "Concern that was too vague or unsupported", "reason": "Why rejected"}}
  ]
}}
```

## Context Pack

Use `context_pack.json` in this run directory as the source of truth for project, feature, files, risk signals, and session context.

Each entry in `files` includes `starting_line`, `ending_line`, and `total_lines`. When a file is `truncated`, you only have content from `starting_line` through `ending_line` of `total_lines` — any finding citing a line greater than `ending_line` is outside the slice you actually saw and must be flagged as "verified visually below the truncation horizon" or rejected.
"""


def _report_markdown(context_pack: Dict[str, Any]) -> str:
    feature = context_pack["feature"]
    return f"""# Agent Harness Report

Run ID: `{context_pack['run_id']}`
Mode: `{context_pack['mode']}`
Feature: `{feature.get('feature_id')}` - {feature.get('title', 'Untitled')}
Created: {context_pack['created_at']}

## Summary

Pending agent execution.

## Findings

No findings recorded yet.

## Rejected Notes

None recorded yet.

## Context

- Context pack: `context_pack.json`
- Instructions: `instructions.md`
"""


_SEVERITY_ORDER = {"critical": 0, "high": 1, "medium": 2, "low": 3, "info": 4}


def _format_files(files: Any) -> str:
    parts = []
    for entry in files or []:
        if isinstance(entry, dict):
            path = entry.get("path", "?")
            line_no = entry.get("line")
            parts.append(f"`{path}`" + (f":{line_no}" if line_no else ""))
        else:
            parts.append(f"`{entry}`")
    return ", ".join(parts)


def write_merged_report(
    run_dir: Path,
    agent_results: List[Dict[str, Any]],
    bundle_status: str = "",
    bundle_status_reason: str = "",
) -> None:
    """Aggregate per-agent findings into the run-level report.md.

    V1 is a flat aggregation: every finding from every agent is listed with
    agent_id attribution, sorted by severity. Cross-agent overlap analysis is
    intentionally separate (see `reccli.audit_analysis`).
    """
    context_pack_path = run_dir / "context_pack.json"
    if not context_pack_path.exists():
        return
    try:
        context_pack = json.loads(context_pack_path.read_text(encoding="utf-8"))
    except Exception:
        return

    feature = context_pack.get("feature", {})

    findings: List[Dict[str, Any]] = []
    rejected: List[Dict[str, Any]] = []
    for result in agent_results:
        agent_id = result.get("agent_id", "?")
        findings_path = result.get("findings_path")
        if not findings_path:
            continue
        try:
            data = json.loads(Path(findings_path).read_text(encoding="utf-8"))
        except Exception:
            continue
        for f in data.get("findings", []) or []:
            entry = dict(f)
            entry["_agent_id"] = agent_id
            findings.append(entry)
        for n in data.get("rejected_notes", []) or []:
            entry = dict(n) if isinstance(n, dict) else {"note": str(n)}
            entry["_agent_id"] = agent_id
            rejected.append(entry)

    findings.sort(
        key=lambda f: (
            _SEVERITY_ORDER.get(str(f.get("severity", "info")).lower(), 99),
            f.get("_agent_id", ""),
        )
    )

    sev_counts: Dict[str, int] = {}
    for f in findings:
        sev = str(f.get("severity", "info")).lower()
        sev_counts[sev] = sev_counts.get(sev, 0) + 1
    sev_tally = ", ".join(
        f"{sev_counts[s]} {s}"
        for s in sorted(sev_counts, key=lambda s: _SEVERITY_ORDER.get(s, 99))
    )

    completed = sum(1 for r in agent_results if r.get("status") == "completed")

    lines: List[str] = [
        "# Agent Harness Report",
        "",
        f"Run ID: `{context_pack.get('run_id', run_dir.name)}`",
        f"Mode: `{context_pack.get('mode', 'audit')}`",
        f"Feature: `{feature.get('feature_id')}` - {feature.get('title', 'Untitled')}",
        f"Created: {context_pack.get('created_at', '')}",
    ]
    if bundle_status:
        lines.append(f"Status: `{bundle_status}`")
    if bundle_status_reason:
        lines.append(f"Status reason: {bundle_status_reason}")
    lines.append("")

    lines += [
        "## Summary",
        "",
        f"- Agents: {len(agent_results)} ({completed} completed)",
        f"- Findings: {len(findings)}" + (f" ({sev_tally})" if sev_tally else ""),
        f"- Rejected notes: {len(rejected)}",
        "",
    ]

    lines.append("## Findings")
    lines.append("")
    if not findings:
        lines.append("No findings recorded.")
        lines.append("")
    else:
        for f in findings:
            sev = str(f.get("severity", "info")).upper()
            agent = f.get("_agent_id", "?")
            title = f.get("title", "Untitled")
            lines.append(f"### [{sev}] [{agent}] {title}")
            lines.append("")
            files_line = _format_files(f.get("files"))
            if files_line:
                lines.append(f"**Files:** {files_line}")
            if f.get("confidence"):
                lines.append(f"**Confidence:** {f['confidence']}")
            lines.append("")
            if f.get("description"):
                lines.append(str(f["description"]))
                lines.append("")
            if f.get("repro_path"):
                lines.append(f"**Repro:** {f['repro_path']}")
                lines.append("")
            if f.get("code_reference"):
                lines.append(f"**Code reference:** {f['code_reference']}")
                lines.append("")
            if f.get("suggested_fix"):
                lines.append(f"**Suggested fix:** {f['suggested_fix']}")
                lines.append("")
            ver = f.get("verification") or []
            if ver:
                lines.append("**Verification:**")
                for v in ver:
                    lines.append(f"- {v}")
                lines.append("")
            lines.append("---")
            lines.append("")

    lines.append("## Rejected Notes")
    lines.append("")
    if not rejected:
        lines.append("None recorded.")
        lines.append("")
    else:
        for n in rejected:
            agent = n.get("_agent_id", "?")
            note = n.get("note", "")
            reason = n.get("reason", "")
            lines.append(f"- **[{agent}]** {note}")
            if reason:
                lines.append(f"  - *Reason:* {reason}")
        lines.append("")

    lines += [
        "## Context",
        "",
        "- Context pack: `context_pack.json`",
        "- Instructions: `instructions.md`",
    ]
    agent_files = [
        f"`{r.get('agent_id')}_findings.json`"
        for r in agent_results
        if r.get("agent_id")
    ]
    if agent_files:
        lines.append(f"- Per-agent findings: {', '.join(sorted(agent_files))}")
    lines.append("")

    (run_dir / "report.md").write_text("\n".join(lines), encoding="utf-8")


def create_agent_harness_run(
    project_root: Path,
    feature_id: str,
    mode: str = "audit",
    agent_count: int = 1,
    focus: str = "",
    max_files: int = DEFAULT_MAX_FILES,
    max_file_chars: int = DEFAULT_MAX_FILE_CHARS,
    files: Optional[List[str]] = None,
    globs: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """Create a feature-scoped agent harness run package.

    The feature is always resolved from ``.devproject`` for description,
    docs, and session linkage. Audit *scope* defaults to the feature's
    ``files_touched`` array; pass explicit ``files`` and/or ``globs`` to
    override that scope. This lets callers audit a product capability
    that crosses feature boundaries or compensate for a stale feature map
    without mutating ``.devproject``.
    """
    project_root = Path(project_root).expanduser().resolve()
    devproject_path = resolve_devproject_path(project_root)
    if not devproject_path.exists():
        raise FileNotFoundError(f"No .devproject found for {project_root}")

    mode = (mode or "audit").strip().lower()
    if mode != "audit":
        raise ValueError("v1 only supports read-only audit mode")

    document = load_devproject(devproject_path)
    feature = _find_feature(document, feature_id)
    if feature is None:
        raise ValueError(
            f"Feature '{feature_id}' not found. Available features:\n{_feature_choices(document)}"
        )

    max_files = max(1, max_files)
    max_file_chars = max(1000, max_file_chars)
    agent_count = max(1, min(agent_count, MAX_AGENT_COUNT))

    feature_files = [
        path for path in feature.get("files_touched", [])
        if isinstance(path, str) and path.strip()
    ]
    docs = [
        doc.get("path")
        for doc in feature.get("docs", [])
        if isinstance(doc, dict) and doc.get("path")
    ]

    override_files = _expand_scope(project_root, files, globs)
    if override_files:
        scope_source = "override"
        scope_files = override_files
    else:
        scope_source = "feature"
        scope_files = feature_files

    selected_files = scope_files[:max_files]
    selected_docs = [path for path in docs[:5] if path not in selected_files]

    file_context = []
    for rel in selected_files:
        item = _read_text_file(project_root / rel, max_file_chars)
        item["path"] = rel
        file_context.append(item)

    doc_context = []
    for rel in selected_docs:
        item = _read_text_file(project_root / rel, max_file_chars // 2)
        item["path"] = rel
        doc_context.append(item)

    feature_slug = _slugify(str(feature.get("feature_id") or feature.get("title")))
    created_at = _utc_now()
    run_id = f"{_run_stamp()}_{mode}_{feature_slug}"
    run_date = created_at[:10]
    run_dir = project_root / "devsession" / "agent-audits" / run_date / feature_slug / run_id
    gitignore_status = _ensure_audit_gitignore(project_root)
    run_dir.mkdir(parents=True, exist_ok=True)

    context_pack: Dict[str, Any] = {
        "run_id": run_id,
        "created_at": created_at,
        "mode": mode,
        "focus": focus,
        "project_root": str(project_root),
        "devproject_path": _rel_path(project_root, devproject_path),
        "project": document.get("project", {}),
        "feature": {
            "feature_id": feature.get("feature_id"),
            "title": feature.get("title"),
            "description": feature.get("description"),
            "status": feature.get("status"),
            "file_boundaries": feature.get("file_boundaries", []),
            "files_touched": feature_files,
            "docs": feature.get("docs", []),
        },
        "agent_count": agent_count,
        "agents": _agent_assignments(selected_files, agent_count),
        "files": file_context,
        "docs": doc_context,
        "scope": {
            "source": scope_source,
            "files_input": list(files or []),
            "globs_input": list(globs or []),
            "resolved_files": list(selected_files),
            "feature_files_touched": list(feature_files),
        },
        "risk_signals": _collect_risk_signals(project_root, selected_files),
        "session_context": _collect_session_context(project_root, feature),
        "output_contract": {
            "findings_fields": [
                "severity",
                "title",
                "description",
                "files",
                "repro_path",
                "code_reference",
                "suggested_fix",
                "confidence",
                "verification",
            ],
        },
    }

    (run_dir / "context_pack.json").write_text(
        json.dumps(context_pack, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    (run_dir / "instructions.md").write_text(_instructions_markdown(context_pack), encoding="utf-8")
    (run_dir / "report.md").write_text(_report_markdown(context_pack), encoding="utf-8")

    for agent in context_pack["agents"]:
        agent_context = dict(context_pack)
        agent_context["agent"] = agent
        agent_body = _instructions_markdown(agent_context)
        assigned = "\n".join(f"- {path}" for path in agent["assigned_files"]) or "- No files assigned"
        agent_body += f"\n## Assigned Files\n\n{assigned}\n"
        (run_dir / f"{agent['agent_id']}_instructions.md").write_text(agent_body, encoding="utf-8")
        (run_dir / f"{agent['agent_id']}_findings.json").write_text(
            json.dumps(_empty_findings(agent, context_pack), indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        (run_dir / f"{agent['agent_id']}_report.md").write_text(
            _agent_report_markdown(agent, context_pack),
            encoding="utf-8",
        )

    return {
        "run_id": run_id,
        "run_dir": str(run_dir),
        "context_pack_path": str(run_dir / "context_pack.json"),
        "instructions_path": str(run_dir / "instructions.md"),
        "report_path": str(run_dir / "report.md"),
        "feature": context_pack["feature"],
        "agent_count": agent_count,
        "files_included": len(file_context),
        "docs_included": len(doc_context),
        "risk_signal_count": len(context_pack["risk_signals"]),
        "session_context_count": len(context_pack["session_context"]),
        "agents": context_pack["agents"],
        "gitignore": gitignore_status,
        "scope": context_pack["scope"],
    }


