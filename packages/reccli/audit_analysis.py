"""Audit overlap analysis.

Measures cross-agent agreement on existing audit runs. Intentionally not
wired into a parent dedup pass: the goal is to first observe what the
natural agreement rate looks like across real audits before designing
machinery to enforce it.

Run interactively:

    python3 -m reccli.audit_analysis <run_dir>
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from typing import Any, Dict, List, Set, Tuple


SEVERITY_ORDER = {"info": 0, "low": 1, "medium": 2, "high": 3, "critical": 4}


def _tokenize(text: str) -> Set[str]:
    return {tok.lower() for tok in re.findall(r"[A-Za-z0-9]+", text or "") if len(tok) >= 3}


def _finding_files(finding: Dict[str, Any]) -> Set[str]:
    files: Set[str] = set()
    for entry in finding.get("files") or []:
        if isinstance(entry, dict) and entry.get("path"):
            files.add(str(entry["path"]))
        elif isinstance(entry, str):
            files.add(entry)
    return files


def _findings_similar(
    a: Dict[str, Any],
    b: Dict[str, Any],
    title_jaccard_threshold: float = 0.4,
    file_paired_threshold: float = 0.2,
) -> bool:
    """Cluster findings with two paths to similarity:

    1. Any shared file path AND non-trivial token overlap in titles.
    2. Strong title overlap on its own (even without a file match).
    """
    files_a = _finding_files(a)
    files_b = _finding_files(b)
    shared_files = files_a & files_b

    title_a = _tokenize(a.get("title", ""))
    title_b = _tokenize(b.get("title", ""))
    if not title_a or not title_b:
        return bool(shared_files)

    union = title_a | title_b
    jaccard = len(title_a & title_b) / len(union)

    if shared_files and jaccard >= file_paired_threshold:
        return True
    if jaccard >= title_jaccard_threshold:
        return True
    return False


def _load_agent_findings(run_dir: Path) -> Dict[str, List[Dict[str, Any]]]:
    findings_by_agent: Dict[str, List[Dict[str, Any]]] = {}
    for path in sorted(run_dir.glob("agent_*_findings.json")):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        agent_id = data.get("agent_id") or path.stem.replace("_findings", "")
        findings_by_agent[agent_id] = data.get("findings") or []
    return findings_by_agent


def measure_audit_overlap(
    run_dir: Path,
    title_jaccard_threshold: float = 0.4,
    file_paired_threshold: float = 0.2,
) -> Dict[str, Any]:
    """Cluster cross-agent findings and return agreement statistics."""
    run_dir = Path(run_dir).expanduser().resolve()
    findings_by_agent = _load_agent_findings(run_dir)
    if not findings_by_agent:
        return {
            "run_dir": str(run_dir),
            "agent_count": 0,
            "total_findings": 0,
            "cluster_count": 0,
            "multi_agent_clusters": 0,
            "agreement_rate": 0.0,
            "clusters": [],
            "agents": [],
            "per_agent_finding_counts": {},
        }

    flat: List[Tuple[str, int, Dict[str, Any]]] = []
    for agent_id, findings in findings_by_agent.items():
        for idx, finding in enumerate(findings):
            flat.append((agent_id, idx, finding))

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
            if _findings_similar(finding_i, finding_j, title_jaccard_threshold, file_paired_threshold):
                union(i, j)

    cluster_map: Dict[int, List[Tuple[str, int, Dict[str, Any]]]] = {}
    for i in range(n):
        cluster_map.setdefault(find(i), []).append(flat[i])

    clusters: List[Dict[str, Any]] = []
    multi_agent_clusters = 0
    for entries in cluster_map.values():
        agents_seen = sorted({entry[0] for entry in entries})
        agent_count = len(agents_seen)
        if agent_count > 1:
            multi_agent_clusters += 1
        severities = [str(entry[2].get("severity", "info")).lower() for entry in entries]
        clusters.append({
            "size": len(entries),
            "agents": agents_seen,
            "agent_count": agent_count,
            "max_severity": max(severities, key=lambda s: SEVERITY_ORDER.get(s, 0)),
            "severities": severities,
            "titles": [entry[2].get("title", "") for entry in entries],
            "files": sorted({f for entry in entries for f in _finding_files(entry[2])}),
        })
    clusters.sort(key=lambda c: (-c["agent_count"], -c["size"]))

    return {
        "run_dir": str(run_dir),
        "agents": sorted(findings_by_agent.keys()),
        "agent_count": len(findings_by_agent),
        "total_findings": n,
        "cluster_count": len(clusters),
        "multi_agent_clusters": multi_agent_clusters,
        "agreement_rate": multi_agent_clusters / max(1, len(clusters)),
        "per_agent_finding_counts": {a: len(f) for a, f in findings_by_agent.items()},
        "clusters": clusters,
    }


def format_overlap_report(report: Dict[str, Any], top_n: int = 10) -> str:
    if report["agent_count"] == 0:
        return f"No agent findings under {report['run_dir']}."

    lines = [
        f"Run: {report['run_dir']}",
        f"Agents: {', '.join(report['agents'])}  (n={report['agent_count']})",
        f"Total findings: {report['total_findings']}",
        f"Clusters: {report['cluster_count']}  (multi-agent: {report['multi_agent_clusters']})",
        f"Agreement rate: {report['agreement_rate']:.0%}",
        f"Per-agent counts: {report['per_agent_finding_counts']}",
        "",
        f"Top {top_n} clusters by agent_count then size:",
    ]
    for cluster in report["clusters"][:top_n]:
        lines.append(
            f"- agents={cluster['agent_count']} size={cluster['size']} "
            f"sev={cluster['max_severity']} | files={cluster['files'][:3]}"
        )
        for title in cluster["titles"][:3]:
            lines.append(f"    - {title}")
    return "\n".join(lines)


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("usage: python3 -m reccli.audit_analysis <run_dir>", file=sys.stderr)
        sys.exit(2)
    report = measure_audit_overlap(Path(sys.argv[1]))
    print(format_overlap_report(report, top_n=20))
