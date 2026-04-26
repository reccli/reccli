"""Consolidate cross-agent audit findings into a deduplicated, ranked set.

Default path is deterministic and free:
- Cluster via the shared similarity helper from ``audit_analysis``.
- Pick the per-cluster representative by severity then confidence.
- Rank by (agent_count, severity, confidence) — agreement is dominant.
- Write the result to ``<run_dir>/consolidated.json``.

Opt-in LLM judge path (``judge_provider="auto"|"claude"|"codex"``):
- Same deterministic pre-clustering.
- A single judge call merges clusters that the heuristic missed.
- Bounded by ``max_judge_clusters`` to cap pathological cost.
- Failure falls back to the deterministic ordering. The function never raises.

Designed as a sibling of ``audit_analysis`` (overlap measurement) and
``propose_patch`` (single-agent dispatch). MMC and consolidation share no
infrastructure: their cost models, lifecycles, and failure modes are
incompatible. See the design notes in the project ADRs.
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from .audit_analysis import (
    SEVERITY_ORDER,
    _findings_similar,
    _load_agent_findings,
)

CONFIDENCE_ORDER = {"low": 0, "medium": 1, "high": 2}

SCHEMA_VERSION = 1
DEFAULT_MAX_JUDGE_CLUSTERS = 50
DEFAULT_TITLE_JACCARD_THRESHOLD = 0.4
DEFAULT_FILE_PAIRED_THRESHOLD = 0.2


# ---------------------------------------------------------------------------
# Clustering and scoring (deterministic path)
# ---------------------------------------------------------------------------


def _flatten(
    findings_by_agent: Dict[str, List[Dict[str, Any]]],
) -> List[Tuple[str, int, Dict[str, Any]]]:
    flat: List[Tuple[str, int, Dict[str, Any]]] = []
    for agent_id, findings in findings_by_agent.items():
        for idx, finding in enumerate(findings):
            flat.append((agent_id, idx, finding))
    return flat


def _cluster(
    flat: List[Tuple[str, int, Dict[str, Any]]],
    title_jaccard_threshold: float,
    file_paired_threshold: float,
) -> List[List[Tuple[str, int, Dict[str, Any]]]]:
    """Union-find clustering. Findings from the same agent never merge."""
    n = len(flat)
    parent = list(range(n))

    def find(x: int) -> int:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a: int, b: int) -> None:
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[ra] = rb

    for i in range(n):
        agent_i, _, finding_i = flat[i]
        for j in range(i + 1, n):
            agent_j, _, finding_j = flat[j]
            if agent_i == agent_j:
                continue
            if _findings_similar(
                finding_i, finding_j, title_jaccard_threshold, file_paired_threshold
            ):
                union(i, j)

    cluster_map: Dict[int, List[Tuple[str, int, Dict[str, Any]]]] = {}
    for i in range(n):
        cluster_map.setdefault(find(i), []).append(flat[i])
    return list(cluster_map.values())


def _severity_value(s: Any) -> int:
    return SEVERITY_ORDER.get(str(s).lower(), 0)


def _confidence_value(c: Any) -> int:
    return CONFIDENCE_ORDER.get(str(c).lower(), 0)


def _pick_representative(
    entries: List[Tuple[str, int, Dict[str, Any]]],
) -> Tuple[str, int, Dict[str, Any]]:
    """Highest severity wins; confidence breaks ties; description length is a final tiebreak."""
    return max(
        entries,
        key=lambda e: (
            _severity_value(e[2].get("severity")),
            _confidence_value(e[2].get("confidence")),
            len(str(e[2].get("description") or "")),
        ),
    )


def _score_cluster(entries: List[Tuple[str, int, Dict[str, Any]]]) -> float:
    """Agreement is the dominant signal. Severity secondary. Confidence tertiary."""
    rep = _pick_representative(entries)[2]
    agents = {e[0] for e in entries}
    return len(agents) * 10 + _severity_value(rep.get("severity")) * 2 + _confidence_value(rep.get("confidence"))


def _build_finding(
    cluster_id: str,
    rank: int,
    entries: List[Tuple[str, int, Dict[str, Any]]],
    score: float,
) -> Dict[str, Any]:
    rep_agent, rep_idx, rep = _pick_representative(entries)
    agents = sorted({e[0] for e in entries})
    return {
        "consolidated_id": cluster_id,
        "rank": rank,
        "score": round(score, 3),
        "agent_count": len(agents),
        "agents": agents,
        "severity": rep.get("severity", "info"),
        "confidence": rep.get("confidence", "low"),
        "title": rep.get("title", "Untitled finding"),
        "description": rep.get("description", ""),
        "files": rep.get("files") or [],
        "suggested_fix": rep.get("suggested_fix", ""),
        "representative": {"agent_id": rep_agent, "finding_index": rep_idx},
        "source_findings": [
            {"agent_id": e[0], "finding_index": e[1]} for e in entries
        ],
    }


# ---------------------------------------------------------------------------
# Optional LLM judge path
# ---------------------------------------------------------------------------


def _build_judge_prompt(scored: List[Tuple[List, float]]) -> str:
    summary = []
    for i, (entries, _score) in enumerate(scored):
        rep = _pick_representative(entries)[2]
        files = []
        for f in (rep.get("files") or [])[:3]:
            if isinstance(f, dict):
                files.append(str(f.get("path", "?")))
            else:
                files.append(str(f))
        summary.append({
            "cluster_index": i,
            "agent_count": len({e[0] for e in entries}),
            "size": len(entries),
            "title": str(rep.get("title", ""))[:200],
            "severity": rep.get("severity", "info"),
            "files": files,
        })
    return (
        "You're reviewing audit-finding clusters from independent code-review "
        "agents. Each cluster groups one or more agent reports that may describe "
        "the same finding. Decide which clusters are duplicates of each other "
        "and should be merged.\n\n"
        "Reply with a JSON array of merge instructions, exactly:\n"
        '[{"merge": [<cluster_index>, <cluster_index>, ...]}, ...]\n\n'
        "Only merge clusters that share BOTH the same root cause AND the same "
        "code location. When in doubt, leave separate. Reply with [] if no "
        "merges are needed. Output JSON only — no prose, no markdown fence.\n\n"
        f"Clusters:\n{json.dumps(summary, indent=2)}"
    )


def _parse_judge_merges(raw: str, n: int) -> List[List[int]]:
    """Extract [{merge:[i,j,...]}, ...] from raw judge output. Validate indices."""
    match = re.search(r"\[[\s\S]*\]", raw)
    if not match:
        raise ValueError("judge output had no JSON array")
    parsed = json.loads(match.group(0))
    if not isinstance(parsed, list):
        raise ValueError("judge output is not a list")
    merges: List[List[int]] = []
    for inst in parsed:
        if not isinstance(inst, dict):
            continue
        indices = inst.get("merge") or []
        if not isinstance(indices, list) or len(indices) < 2:
            continue
        valid = [i for i in indices if isinstance(i, int) and 0 <= i < n]
        if len(valid) >= 2:
            merges.append(valid)
    return merges


def _apply_merges(
    scored: List[Tuple[List, float]],
    merges: List[List[int]],
) -> List[Tuple[List, float]]:
    n = len(scored)
    parent = list(range(n))

    def find(x: int) -> int:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a: int, b: int) -> None:
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[ra] = rb

    for indices in merges:
        anchor = indices[0]
        for k in indices[1:]:
            union(anchor, k)

    grouped: Dict[int, List[Tuple[List, float]]] = {}
    for i in range(n):
        grouped.setdefault(find(i), []).append(scored[i])

    rebuilt: List[Tuple[List, float]] = []
    for group in grouped.values():
        merged_entries: List[Tuple[str, int, Dict[str, Any]]] = []
        for entries, _ in group:
            merged_entries.extend(entries)
        rebuilt.append((merged_entries, _score_cluster(merged_entries)))
    rebuilt.sort(key=lambda x: -x[1])
    return rebuilt


def _project_root_from_run_dir(run_dir: Path) -> Path:
    """Walk up the standard run_dir layout to recover project_root.

    Layout: <project_root>/devsession/agent-audits/<date>/<feature>/<run-id>
    """
    if len(run_dir.parents) >= 4:
        return run_dir.parents[3]
    return run_dir.parent


def _judge_with_llm(
    project_root: Path,
    run_dir: Path,
    scored: List[Tuple[List, float]],
    provider: str,
    model: Optional[str],
    timeout: int,
    max_judge_clusters: int,
) -> Tuple[List[Tuple[List, float]], int]:
    """Dispatch one judge call. Returns (rebuilt_scored, clusters_judged).

    Raises only on dispatch errors; the caller catches and falls back.
    """
    from .agent_providers import run_provider_prompt

    head = scored[:max_judge_clusters]
    tail = scored[max_judge_clusters:]

    prompt = _build_judge_prompt(head)
    output_path = run_dir / ".consolidate_judge_output.txt"

    try:
        dispatch = run_provider_prompt(
            provider=provider,
            project_root=project_root,
            prompt=prompt,
            timeout_seconds=timeout,
            output_path=output_path if str(provider).lower() == "codex" else None,
            model=model,
        )
    finally:
        try:
            output_path.unlink(missing_ok=True)
        except Exception:
            pass

    if dispatch["returncode"] != 0:
        raise RuntimeError(f"judge dispatch returncode={dispatch['returncode']}")

    merges = _parse_judge_merges(dispatch["raw_output"], len(head))
    rebuilt_head = _apply_merges(head, merges) if merges else head
    return rebuilt_head + tail, len(head)


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


def consolidate_audit_run(
    run_dir: Path,
    *,
    project_root: Optional[Path] = None,
    judge_provider: Optional[str] = None,
    judge_model: Optional[str] = None,
    judge_timeout: int = 300,
    max_judge_clusters: int = DEFAULT_MAX_JUDGE_CLUSTERS,
    title_jaccard_threshold: float = DEFAULT_TITLE_JACCARD_THRESHOLD,
    file_paired_threshold: float = DEFAULT_FILE_PAIRED_THRESHOLD,
) -> Dict[str, Any]:
    """Cluster N agents' findings into a deduplicated, ranked set.

    Default path (``judge_provider=None``) is deterministic, free, and
    millisecond-fast. Pass ``judge_provider="auto"|"claude"|"codex"`` to add
    an LLM judge pass that may merge clusters the heuristic missed.

    Writes ``<run_dir>/consolidated.json`` and returns the same payload.
    Never raises — judge failures are reported via ``judge.status`` instead.
    """
    run_dir = Path(run_dir).expanduser().resolve()
    findings_by_agent = _load_agent_findings(run_dir)
    flat = _flatten(findings_by_agent)

    judge: Dict[str, Any] = {
        "used": False,
        "status": "skipped",
        "provider": None,
        "model": None,
        "clusters_judged": 0,
        "reason": "",
    }

    output: Dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "run_id": run_dir.name,
        "feature_id": _extract_feature_id(run_dir),
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "judge": judge,
        "agent_count": len(findings_by_agent),
        "input_finding_count": len(flat),
        "consolidated_count": 0,
        "findings": [],
        "dropped": [],
    }

    if not flat:
        judge["reason"] = "no findings to consolidate"
        _write_consolidated(run_dir, output)
        return output

    clusters = _cluster(flat, title_jaccard_threshold, file_paired_threshold)
    scored: List[Tuple[List, float]] = [
        (entries, _score_cluster(entries)) for entries in clusters
    ]
    scored.sort(key=lambda x: -x[1])

    judge_active = judge_provider and str(judge_provider).strip().lower() not in {"", "none"}
    if judge_active:
        judge["used"] = True
        judge["provider"] = judge_provider
        judge["model"] = judge_model
        try:
            project_root_resolved = project_root or _project_root_from_run_dir(run_dir)
            scored, clusters_judged = _judge_with_llm(
                project_root=project_root_resolved,
                run_dir=run_dir,
                scored=scored,
                provider=judge_provider,
                model=judge_model,
                timeout=judge_timeout,
                max_judge_clusters=max_judge_clusters,
            )
            judge["status"] = "completed"
            judge["clusters_judged"] = clusters_judged
        except Exception as exc:
            judge["status"] = "failed"
            judge["reason"] = str(exc)[:200]
            # Deterministic ordering already in `scored`; carry on.
    else:
        judge["reason"] = "judge_provider not set"

    findings_out: List[Dict[str, Any]] = []
    for rank, (entries, score) in enumerate(scored, start=1):
        cluster_id = f"cf_{rank:03d}"
        findings_out.append(_build_finding(cluster_id, rank, entries, score))

    output["consolidated_count"] = len(findings_out)
    output["findings"] = findings_out
    _write_consolidated(run_dir, output)
    return output


def _extract_feature_id(run_dir: Path) -> Optional[str]:
    cp = run_dir / "context_pack.json"
    if not cp.exists():
        return None
    try:
        data = json.loads(cp.read_text(encoding="utf-8"))
        return (data.get("feature") or {}).get("feature_id")
    except Exception:
        return None


def _write_consolidated(run_dir: Path, output: Dict[str, Any]) -> None:
    try:
        path = run_dir / "consolidated.json"
        path.write_text(
            json.dumps(output, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
    except Exception:
        pass
