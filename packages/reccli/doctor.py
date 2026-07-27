"""
RecCli integrity diagnostics.

Every memory-integrity defect found so far shared one property: it was silent.
Sessions vanished from the index for months while rebuilds reported clean totals;
post-compaction summarization raised NameError that was logged as a routine
warning; a summarizer crash died inside a subprocess whose stderr was DEVNULL;
a stale checksum on an emptied structure was never compared at all.

A memory system whose failures are silent is worse than one that fails loudly,
because the corruption is only discovered at the moment it is relied upon. This
module makes that class of failure visible in one read-only pass.

Nothing here mutates state. Every check reports what it found and why it matters.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Optional

# Severity ordering, worst first.
FAIL = "fail"
WARN = "warn"
OK = "ok"
_SEVERITY_RANK = {FAIL: 0, WARN: 1, OK: 2}

_SUMMARY_CATEGORIES = ("decisions", "code_changes", "problems_solved", "open_issues", "next_steps")


def _finding(target: str, detail: str, hint: str = "") -> Dict[str, str]:
    return {"target": target, "detail": detail, "hint": hint}


def _check(check_id: str, title: str, status: str, findings: List[Dict[str, str]],
           why: str = "", scanned: Optional[int] = None) -> Dict[str, Any]:
    return {
        "id": check_id,
        "title": title,
        "status": status,
        "why": why,
        "scanned": scanned,
        "findings": findings,
    }


def _session_files(sessions_dir: Path) -> List[Path]:
    """Real session files, excluding live snapshots and archived ones.

    pathlib's glob matches dotfiles (unlike shell globbing), so '.live_*' must be
    filtered explicitly rather than assumed absent.
    """
    if not sessions_dir.is_dir():
        return []
    return sorted(p for p in sessions_dir.glob("*.devsession") if not p.name.startswith(".live_"))


# --------------------------------------------------------------------------
# Pass 1: load every session once, derive several checks from the same read.
# --------------------------------------------------------------------------

def _scan_sessions(sessions_dir: Path) -> Dict[str, Any]:
    """Load each session once and collect everything the per-session checks need."""
    from .session.devsession import DevSession, is_stub_overview

    scan: Dict[str, Any] = {
        "files": [],
        "unreadable": [],      # (name, reason) - excluded from the index, invisibly
        "empty": [],           # zero-byte files
        "loaded": {},          # stem -> lightweight record
    }

    for path in _session_files(sessions_dir):
        scan["files"].append(path)
        try:
            if path.stat().st_size == 0:
                scan["empty"].append(path.name)
                continue
        except OSError as exc:
            scan["unreadable"].append((path.name, f"stat failed: {exc}"))
            continue

        # DevSession.load() raises on checksum mismatch. That raise is exactly what
        # made sessions disappear from the index without a word, so treat a failure
        # here as a first-class finding rather than something to skip past.
        try:
            session = DevSession.load(path)
        except Exception as exc:
            reason = str(exc)
            if "Checksum verification failed" in reason:
                reason = "checksum mismatch (file was edited outside DevSession.save)"
            scan["unreadable"].append((path.name, reason))
            continue

        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            raw = {}

        scan["loaded"][path.stem] = {
            "path": path,
            "n_messages": len(session.conversation or []),
            "summary": session.summary,
            "spans": session.spans or [],
            "metadata": session.metadata or {},
            "stored_checksums": raw.get("checksums") or {},
            # Delegate rather than re-deriving "is this summarized". Computing it
            # here as a truthiness test counted "Summarization failed: <api error>"
            # as a completed summary, so doctor and the runtime disagreed about the
            # same file, and doctor erred toward telling the user to archive it.
            "has_summary": not is_stub_overview((session.summary or {}).get("overview", "")),
        }

    return scan


def _check_unreadable(scan: Dict[str, Any]) -> Dict[str, Any]:
    findings = [
        _finding(name, reason, "Repair or archive it; until then its content is not searchable.")
        for name, reason in scan["unreadable"]
    ]
    return _check(
        "sessions.unreadable",
        "Sessions that fail to load",
        FAIL if findings else OK,
        findings,
        why="These are silently excluded from every index rebuild, so search behaves as if they do not exist.",
        scanned=len(scan["files"]),
    )


def _check_empty(scan: Dict[str, Any]) -> Dict[str, Any]:
    findings = [
        _finding(name, "file is 0 bytes", "Nothing to recover; archive it with delete_session.")
        for name in scan["empty"]
    ]
    return _check(
        "sessions.empty",
        "Zero-byte session files",
        FAIL if findings else OK,
        findings,
        why="An empty file cannot be parsed or repaired, and it counts against the store forever.",
        scanned=len(scan["files"]),
    )


def _check_orphan_checksums(scan: Dict[str, Any]) -> Dict[str, Any]:
    """Stored checksums for structures that are now empty are never verified.

    _calculate_checksums() only emits a key when the structure is truthy, and
    verify_checksums() iterates over freshly-computed keys only. So emptying a
    structure leaves its old checksum stranded and permanently unchecked: editing
    data fails loudly, deleting it passes silently.
    """
    structure_for = {
        "conversation": lambda r: r["n_messages"],
        "summary": lambda r: 1 if r["summary"] else 0,
        "spans": lambda r: len(r["spans"]),
    }
    findings = []
    for stem, rec in scan["loaded"].items():
        for key, size_of in structure_for.items():
            if key in rec["stored_checksums"] and not size_of(rec):
                findings.append(_finding(
                    f"{stem}.devsession",
                    f"stored '{key}' checksum but {key} is empty",
                    "The stale hash is never compared, so this structure is unprotected.",
                ))
    return _check(
        "checksums.orphaned",
        "Checksums stranded on emptied structures",
        WARN if findings else OK,
        findings,
        why="Verification only compares keys it recomputes, so data deletion evades detection entirely.",
        scanned=len(scan["loaded"]),
    )


def _check_range_integrity(scan: Dict[str, Any]) -> Dict[str, Any]:
    """Summary and span links must resolve inside the conversation they point at.

    An out-of-bounds end_index means the conversation lost messages while the
    summary survived, so every drill-down from that item silently truncates or
    lands in the wrong place.
    """
    findings = []
    for stem, rec in scan["loaded"].items():
        n = rec["n_messages"]
        bad = 0
        worst = 0
        summary = rec["summary"] or {}
        for category in _SUMMARY_CATEGORIES:
            for item in (summary.get(category) or []):
                if not isinstance(item, dict):
                    continue
                end = (item.get("message_range") or {}).get("end_index")
                if isinstance(end, int) and end > n:
                    bad += 1
                    worst = max(worst, end)
        for span in rec["spans"]:
            if not isinstance(span, dict):
                continue
            end = span.get("end_index")
            if isinstance(end, int) and end > n:
                bad += 1
                worst = max(worst, end)
        if bad:
            findings.append(_finding(
                f"{stem}.devsession",
                f"{bad} link(s) point past the end of the conversation (max end_index {worst}, {n} messages)",
                "Messages were likely removed after summarization; drill-down from these items is unreliable.",
            ))
    return _check(
        "links.out_of_bounds",
        "Summary and span links that do not resolve",
        FAIL if findings else OK,
        findings,
        why="The tri-layer model depends on these ranges; if they do not resolve, compaction is lossy after all.",
        scanned=len(scan["loaded"]),
    )


def is_prefix_superseded(short_path: Path, short_len: int, long_path: Path, long_len: int) -> bool:
    """True if the shorter session is a genuine truncated prefix of the longer one.

    Sharing a claude_session_id and being shorter is NOT sufficient. One Claude
    session legitimately spans several distinct devsessions, and a resumed session
    days later shares the id without sharing any content. Confirming the boundary
    messages line up is what separates a stale partial flush from a real session.

    This is the single definition of "superseded". mcp_server delegates to it so the
    diagnostic and the runtime target filter cannot drift apart: an earlier version
    of this check omitted the prefix test and flagged three real sessions on this
    repo, while telling the user to archive them.
    """
    if long_len <= short_len or short_len == 0:
        return False
    try:
        short_conv = json.loads(short_path.read_text(encoding="utf-8")).get("conversation") or []
        long_conv = json.loads(long_path.read_text(encoding="utf-8")).get("conversation") or []
    except Exception:
        return False
    if len(long_conv) <= len(short_conv) or len(short_conv) == 0:
        return False
    return (
        long_conv[0].get("content") == short_conv[0].get("content")
        and long_conv[len(short_conv) - 1].get("content") == short_conv[-1].get("content")
    )


def _check_superseded(scan: Dict[str, Any]) -> Dict[str, Any]:
    """Partial flushes that a longer, already-summarized sibling supersedes.

    These sit unsummarized forever at the head of the "needs summarizing" queue and
    send agents off to re-summarize content the complete file already covers.

    The prefix confirmation is deliberately strict. The hint tells the user to
    archive the file, so a false positive here costs a real transcript.
    """
    by_csid: Dict[str, List[Any]] = {}
    for stem, rec in scan["loaded"].items():
        csid = rec["metadata"].get("claude_session_id")
        if csid:
            by_csid.setdefault(csid, []).append((stem, rec))

    findings = []
    for csid, group in by_csid.items():
        if len(group) < 2:
            continue
        for stem, rec in group:
            if rec["has_summary"]:
                continue
            for other_stem, other in group:
                if other_stem == stem or not other["has_summary"]:
                    continue
                if not is_prefix_superseded(
                    rec["path"], rec["n_messages"], other["path"], other["n_messages"]
                ):
                    continue
                findings.append(_finding(
                    f"{stem}.devsession",
                    f"{rec['n_messages']} messages, an exact prefix of {other_stem}.devsession "
                    f"({other['n_messages']} messages, summarized), same claude_session_id",
                    "Archive it; otherwise it stays first in line for summarization forever.",
                ))
                break
    return _check(
        "sessions.superseded",
        "Stale partial snapshots",
        WARN if findings else OK,
        findings,
        why="Unsummarized partials are permanent decoys for any tool that picks a target by recency.",
        scanned=len(scan["loaded"]),
    )


# --------------------------------------------------------------------------
# Index checks
# --------------------------------------------------------------------------

def _check_index(sessions_dir: Path, scan: Dict[str, Any]) -> List[Dict[str, Any]]:
    index_path = sessions_dir / "index.json"
    if not index_path.exists():
        # A project with no sessions has nothing to index. Reporting that as a
        # failure made five of six index FAILs pure noise, and a diagnostic that
        # cries wolf stops being read.
        if not scan["files"]:
            return [_check(
                "index.missing", "Vector index present", OK, [],
                why="No sessions recorded yet, so there is nothing to index.",
            )]
        return [_check(
            "index.missing", "Vector index present", FAIL,
            [_finding("index.json", f"not found, but {len(scan['files'])} session(s) exist",
                      "Run rebuild_index.")],
            why="Without an index nothing is searchable.",
        )]

    try:
        index = json.loads(index_path.read_text(encoding="utf-8"))
    except Exception as exc:
        return [_check(
            "index.unreadable", "Vector index readable", FAIL,
            [_finding("index.json", f"could not parse: {exc}", "Run rebuild_index.")],
            why="An unparseable index disables search entirely.",
        )]

    indexed = {e.get("session_id") for e in (index.get("session_manifest") or [])}
    on_disk = {p.stem for p in scan["files"] if p.name not in scan["empty"]}
    missing = sorted(on_disk - indexed)

    # Staleness: an index older than the newest session silently omits recent work.
    stale_findings = []
    index_mtime = 0.0
    try:
        index_mtime = index_path.stat().st_mtime
        newest = max((p.stat().st_mtime for p in scan["files"]), default=0)
        if newest and index_mtime < newest - 1:
            from datetime import datetime
            stale_findings.append(_finding(
                "index.json",
                f"older than the newest session file "
                f"(index {datetime.fromtimestamp(index_mtime):%Y-%m-%d %H:%M}, "
                f"newest session {datetime.fromtimestamp(newest):%Y-%m-%d %H:%M})",
                "Run rebuild_index.",
            ))
    except OSError:
        pass

    # Distinguish cause from symptom. A session absent from the index because the
    # index simply has not been rebuilt is a routine chore; one absent despite a
    # current index failed to load, which is the silent-exclusion failure mode.
    unreadable_stems = {name.rsplit(".devsession", 1)[0] for name, _ in scan["unreadable"]}
    coverage_findings = []
    silent_loss = False
    for stem in missing:
        try:
            newer_than_index = (sessions_dir / f"{stem}.devsession").stat().st_mtime > index_mtime
        except OSError:
            newer_than_index = False
        if stem in unreadable_stems:
            hint = "It fails to load; see sessions.unreadable. Rebuilding will NOT bring it back."
            silent_loss = True
        elif newer_than_index:
            # Routine: a session recorded since the last rebuild. Not a defect, and
            # grading it FAIL made the normal post-session state look broken.
            hint = "Written after the last rebuild. Run rebuild_index."
        else:
            hint = "Predates the last rebuild yet was still dropped. Investigate before rebuilding."
            silent_loss = True
        coverage_findings.append(_finding(f"{stem}.devsession", "on disk but absent from the index", hint))

    coverage = _check(
        "index.coverage",
        "Every readable session is indexed",
        (FAIL if silent_loss else WARN) if coverage_findings else OK,
        coverage_findings,
        why="A session missing from the index is invisible to search while still looking fine on disk.",
        scanned=len(on_disk),
    )

    staleness = _check(
        "index.stale", "Index is current", WARN if stale_findings else OK, stale_findings,
        why="Recent sessions are not retrievable until the index catches up.",
    )
    return [coverage, staleness]


def _check_malformed_summary_items(scan: Dict[str, Any]) -> Dict[str, Any]:
    """Summary entries that are not dicts, or dicts missing their text field.

    The summarizer was hardened against a bare string per item at write time, but
    nothing looks at what is already on disk. These items are skipped silently by
    every reader, so the decision or next step they represent is simply absent from
    the memory without anything saying so.
    """
    text_field = {
        "decisions": "decision", "code_changes": "description",
        "problems_solved": "problem", "open_issues": "issue", "next_steps": "action",
    }
    findings = []
    for stem, rec in scan["loaded"].items():
        summary = rec["summary"] or {}
        bad_type = 0
        missing_text = []
        for category in _SUMMARY_CATEGORIES:
            for item in (summary.get(category) or []):
                if not isinstance(item, dict):
                    bad_type += 1
                elif not str(item.get(text_field.get(category, ""), "") or "").strip():
                    missing_text.append(category)
        if bad_type:
            findings.append(_finding(
                f"{stem}.devsession", f"{bad_type} summary item(s) are not objects",
                "Every reader skips these, so their content is absent from the memory.",
            ))
        if missing_text:
            findings.append(_finding(
                f"{stem}.devsession",
                f"{len(missing_text)} item(s) have an empty text field ({', '.join(sorted(set(missing_text)))})",
                "An item with no text carries no information but still occupies the summary.",
            ))
    return _check(
        "summary.malformed", "Summary items are well-formed",
        WARN if findings else OK, findings,
        why="Malformed items are skipped in silence, so the memory is quietly missing content it appears to have.",
        scanned=len(scan["loaded"]),
    )


def _check_issue_log(sessions_dir: Path) -> Dict[str, Any]:
    """Surface devsession/.issues.jsonl, the codebase's own silent-failure log.

    Hook paths write structured records here instead of raising, which is the right
    call for a recorder that must not break the session. But nothing ever reads it,
    so the log that exists specifically to make silent failures visible was itself
    silent.
    """
    # Background-writer stderr that has not been reaped into the issue log yet.
    # These are live crash reports from the detached summarize/index subprocess.
    unreaped = []
    try:
        for err_file in sorted(sessions_dir.glob(".bg_finalize_*.err")):
            try:
                text = err_file.read_text(encoding="utf-8", errors="ignore").strip()
            except Exception:
                continue
            # The finalize subprocess prints progress to stderr as well, so output
            # alone is not a failure. Reporting it as one made every successful
            # background finalize look like a crash.
            if text and any(m in text for m in
                            ("Traceback (most recent call last)", "Error:", "Exception:",
                             "CRITICAL", "FATAL")):
                unreaped.append(_finding(
                    err_file.name, f"background finalize failed: {text[-200:]}",
                    "That session may have no summary and may not be indexed.",
                ))
    except Exception:
        pass

    log = sessions_dir / ".issues.jsonl"
    if not log.exists():
        return _check("issues.logged", "Recorded internal failures",
                      WARN if unreaped else OK, unreaped,
                      why="These are failures the system caught and then never surfaced.")
    records = []
    try:
        for line in log.read_text(encoding="utf-8").splitlines():
            if line.strip():
                try:
                    records.append(json.loads(line))
                except Exception:
                    continue
    except OSError:
        return _check("issues.logged", "Recorded internal failures", OK, [])

    by_component: Dict[str, int] = {}
    latest: Dict[str, str] = {}
    for rec in records:
        key = f"{rec.get('component', '?')}: {str(rec.get('message', ''))[:70]}"
        by_component[key] = by_component.get(key, 0) + 1
        latest[key] = rec.get("timestamp", "")
    findings = unreaped + [
        _finding(key, f"{count} occurrence(s), most recent {latest.get(key, '')[:19]}",
                 "Recorded by a hook that chose not to raise; nothing else surfaces these.")
        for key, count in sorted(by_component.items(), key=lambda kv: -kv[1])
    ]
    return _check(
        "issues.logged", "Recorded internal failures",
        WARN if findings else OK, findings,
        why="These are failures the system already caught and then never told anyone about.",
        scanned=len(records),
    )


def _check_orphan_wals(sessions_dir: Path) -> Dict[str, Any]:
    """WALs with content but no finalized session mean a session never closed cleanly."""
    findings = []
    if sessions_dir.is_dir():
        for wal in sorted(sessions_dir.glob(".hooks_wal_*.jsonl")):
            try:
                if wal.stat().st_size < 200:
                    continue  # header-only, nothing recorded
                lines = sum(1 for _ in wal.open(encoding="utf-8", errors="ignore"))
            except OSError:
                continue
            if lines > 1:
                findings.append(_finding(
                    wal.name,
                    f"{lines - 1} unflushed record(s)",
                    "Either a live session, or one that never finalized. Recoverable, not lost.",
                ))
    return _check(
        "wal.orphaned", "Write-ahead logs flushed", WARN if findings else OK, findings,
        why="An unflushed WAL holds conversation that exists nowhere else in searchable form.",
    )


# --------------------------------------------------------------------------
# .devproject checks
# --------------------------------------------------------------------------

def _check_devproject(project_root: Path) -> List[Dict[str, Any]]:
    # Resolve exactly the way the runtime does. Globbing "*.devproject" picks the
    # first match alphabetically, which is a legacy bare ".devproject" in projects
    # that have both, so the diagnostic reported on a stale file the runtime never
    # reads.
    try:
        from .project.devproject import resolve_devproject_path
        devproject_path = Path(resolve_devproject_path(project_root))
    except Exception:
        matches = sorted(project_root.glob("*.devproject"))
        devproject_path = matches[0] if matches else project_root / ".devproject"

    if not devproject_path.exists():
        return [_check(
            "devproject.missing", "Project feature map present", WARN,
            [_finding(project_root.name, "no .devproject file",
                      "Run project init to bootstrap the feature map.")],
            why="Without it, agents get no project orientation at session start.",
        )]

    try:
        doc = json.loads(devproject_path.read_text(encoding="utf-8"))
    except Exception as exc:
        return [_check(
            "devproject.unreadable", "Project feature map readable", FAIL,
            [_finding(devproject_path.name, f"could not parse: {exc}")],
            why="A corrupt feature map breaks session-start context injection.",
        )]

    candidates = [devproject_path]

    features = doc.get("features") or []
    checks: List[Dict[str, Any]] = []

    # Boundary ownership. The spec requires a warning when features collide; nothing
    # emitted one, which is how a catch-all feature came to own an entire source tree.
    claims: Dict[str, List[str]] = {}
    for feat in features:
        for boundary in (feat.get("file_boundaries") or []):
            claims.setdefault(boundary, []).append(feat.get("feature_id", "?"))

    overlap = [
        _finding(boundary, f"claimed by {len(owners)} features: {', '.join(owners)}",
                 "Ownership is ambiguous; agent dispatch cannot route safely.")
        for boundary, owners in sorted(claims.items()) if len(owners) > 1
    ]
    for boundary, owners in claims.items():
        if not boundary.endswith("/**"):
            continue
        prefix = boundary[:-2]
        for other, other_owners in claims.items():
            if other != boundary and other.startswith(prefix) and set(other_owners) != set(owners):
                overlap.append(_finding(
                    boundary,
                    f"subsumes {other} (owned by {', '.join(other_owners)})",
                    "A broad glob swallowing another feature's boundary hides real ownership.",
                ))
    checks.append(_check(
        "devproject.boundaries", "Feature boundaries are unambiguous",
        WARN if overlap else OK, overlap,
        why="Overlapping ownership is how session evidence gets attributed to the wrong feature.",
        scanned=len(features),
    ))

    # session_index entries must resolve on disk AND name features that still exist.
    # Deleting or merging a feature does not rewrite the session_index, so entries
    # keep pointing at an id nothing resolves, and feature-level retrieval silently
    # returns nothing for those sessions.
    live_feature_ids = {f.get("feature_id") for f in features}
    dangling = []
    for entry in (doc.get("session_index") or []):
        if not (project_root / str(entry.get("path", ""))).exists():
            dangling.append(_finding(
                entry.get("session_id", "?"), f"path does not exist: {entry.get('path')}",
                "The link from project layer to session layer is broken.",
            ))
        orphan_ids = sorted(
            fid for fid in (entry.get("feature_ids") or []) if fid not in live_feature_ids
        )
        if orphan_ids:
            dangling.append(_finding(
                entry.get("session_id", "?"),
                f"references feature(s) that no longer exist: {', '.join(orphan_ids)}",
                "Left behind by a deleted or merged feature; retrieval for this session finds nothing.",
            ))
    checks.append(_check(
        "devproject.session_links", "Session index resolves to real files",
        FAIL if dangling else OK, dangling,
        why="These links are how feature-level retrieval finds session history.",
        scanned=len(doc.get("session_index") or []),
    ))

    # An unpopulated project layer is structurally valid but functionally inert.
    if features and not any(f.get("session_ids") for f in features):
        inert = [_finding(
            candidates[0].name,
            f"{len(features)} features, none linked to any session",
            "Accept pending proposals to populate the semantic link between layers.",
        )]
    else:
        inert = []
    checks.append(_check(
        "devproject.unlinked", "Feature map is linked to sessions",
        WARN if inert else OK, inert,
        why="An unlinked feature map is a static codebase scan, not cross-session memory.",
    ))

    pending = [p for p in (doc.get("proposals") or []) if p.get("status") == "pending"]
    backlog = []
    if len(pending) >= 5:
        oldest = min((p.get("created_at", "") for p in pending), default="")
        backlog = [_finding(
            candidates[0].name,
            f"{len(pending)} pending proposals, oldest {oldest[:10] or 'unknown'}",
            "Accept or reject them; until then the feature map stops reflecting real work.",
        )]
    checks.append(_check(
        "devproject.proposals", "Proposal backlog is manageable",
        WARN if backlog else OK, backlog,
        why="Proposals are the only mechanism that links new session work into the project map.",
        scanned=len(pending),
    ))

    return checks


# --------------------------------------------------------------------------
# Entry point
# --------------------------------------------------------------------------

def run_diagnostics(project_root: Path) -> Dict[str, Any]:
    """Run every integrity check against a project. Read-only."""
    project_root = Path(project_root).expanduser().resolve()
    # Use the root we were handed. default_devsession_dir() calls discover_project_root
    # and searches UPWARD, so a root with no .git and no .devproject silently resolved
    # to some ancestor's store - or to ~/reccli/devsession - while the report still
    # named the directory the caller asked about. Diagnosing a different store than
    # the one you named is indistinguishable from diagnosing yours and finding it fine.
    # Never created here: a diagnostic must not mutate what it inspects.
    sessions_dir = project_root / "devsession"

    def _safely(check_id: str, title: str, fn, *args):
        """Run one check in isolation.

        Without this, a single malformed entry anywhere in .devproject raised out of
        run_diagnostics and the caller lost ALL twelve checks, including the
        session-level ones that had already succeeded. A diagnostic that fails
        entirely when it meets bad data is useless precisely when it is needed, and
        it fails the same silent way it exists to expose.
        """
        try:
            result = fn(*args)
            return result if isinstance(result, list) else [result]
        except Exception as exc:
            return [_check(
                check_id, title, FAIL,
                [_finding(check_id, f"the check itself raised: {type(exc).__name__}: {exc}",
                          "This is a doctor bug or unexpected data shape; the other checks still ran.")],
                why="A check that cannot run tells you nothing about what it was meant to verify.",
            )]

    checks: List[Dict[str, Any]] = []

    # If the scan itself fails, every downstream check runs against an empty store
    # and passes. Discarding the failure record here made doctor print a clean bill
    # of health for a store it had never read, which is worse than any bug it exists
    # to find: it converts "I could not look" into "I looked and it was fine".
    scan_result = _safely("scan", "Session scan", _scan_sessions, sessions_dir)
    if isinstance(scan_result[0], dict) and "loaded" in scan_result[0]:
        scan = scan_result[0]
    else:
        scan = {"files": [], "unreadable": [], "empty": [], "loaded": {}}
        checks.extend(scan_result)   # keep the FAIL; never silently swallow it

    for check_id, title, fn, args in (
        ("sessions.unreadable", "Sessions that fail to load", _check_unreadable, (scan,)),
        ("sessions.empty", "Zero-byte session files", _check_empty, (scan,)),
        ("links.out_of_bounds", "Summary and span links that do not resolve", _check_range_integrity, (scan,)),
        ("checksums.orphaned", "Checksums stranded on emptied structures", _check_orphan_checksums, (scan,)),
        ("sessions.superseded", "Stale partial snapshots", _check_superseded, (scan,)),
        ("summary.malformed", "Summary items are well-formed", _check_malformed_summary_items, (scan,)),
        ("index", "Vector index", _check_index, (sessions_dir, scan)),
        ("wal.orphaned", "Write-ahead logs flushed", _check_orphan_wals, (sessions_dir,)),
        ("issues.logged", "Recorded internal failures", _check_issue_log, (sessions_dir,)),
        ("devproject", "Project feature map", _check_devproject, (project_root,)),
    ):
        checks.extend(_safely(check_id, title, fn, *args))

    counts = {FAIL: 0, WARN: 0, OK: 0}
    for check in checks:
        counts[check["status"]] += 1

    checks.sort(key=lambda c: (_SEVERITY_RANK[c["status"]], c["id"]))

    return {
        "project_root": str(project_root),
        "sessions_dir": str(sessions_dir),
        "sessions_scanned": len(scan["files"]),
        "checks": checks,
        "counts": counts,
        "healthy": counts[FAIL] == 0 and counts[WARN] == 0,
    }


def format_report(result: Dict[str, Any], verbose: bool = False) -> str:
    """Human-readable report. Findings are capped unless verbose."""
    marks = {FAIL: "FAIL", WARN: "WARN", OK: " ok "}
    lines = [
        f"RecCli doctor — {result['project_root']}",
        f"{result['sessions_scanned']} session(s) scanned",
        "",
    ]
    for check in result["checks"]:
        if check["status"] == OK and not verbose:
            continue
        scanned = f"  ({check['scanned']} checked)" if check.get("scanned") is not None else ""
        lines.append(f"[{marks[check['status']]}] {check['title']}{scanned}")
        if check["status"] != OK and check.get("why"):
            lines.append(f"        {check['why']}")
        shown = check["findings"] if verbose else check["findings"][:5]
        for finding in shown:
            lines.append(f"        - {finding['target']}: {finding['detail']}")
            if finding.get("hint"):
                lines.append(f"          {finding['hint']}")
        remaining = len(check["findings"]) - len(shown)
        if remaining > 0:
            lines.append(f"        ... and {remaining} more (use --verbose)")
        lines.append("")

    counts = result["counts"]
    if result["healthy"]:
        lines.append(f"All {counts[OK]} checks passed.")
    else:
        lines.append(f"{counts[FAIL]} failing, {counts[WARN]} warning, {counts[OK]} passing.")
        if not verbose:
            lines.append("Run with --verbose for every finding.")
    return "\n".join(lines)
