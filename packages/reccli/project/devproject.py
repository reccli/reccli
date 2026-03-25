"""
DevProject - .devproject project dashboard manager.
"""

from __future__ import annotations

import ast
import importlib
import json
import math
import re
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple


FORMAT_VERSION = "2.1.0"
MAX_UPDATE_HISTORY = 25
CODE_EXTENSIONS = {
    ".py", ".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs",
    ".go", ".rs", ".java", ".kt", ".swift",
    ".rb", ".php", ".sh", ".bash", ".zsh",
    ".c", ".cc", ".cpp", ".h", ".hpp",
}
DOC_EXTENSIONS = {".md", ".txt", ".rst", ".adoc"}
IGNORED_DIRS = {
    ".git", ".hg", ".svn", ".idea", ".vscode",
    "node_modules", "dist", "build", ".next", ".turbo",
    "__pycache__", ".pytest_cache", ".mypy_cache",
    ".venv", "venv", "coverage", ".coverage",
}
JS_LIKE_EXTENSIONS = {".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs"}
PYTHON_EXTENSIONS = {".py"}
KEY_FILE_NAMES = {
    "main.py", "main.ts", "main.tsx", "main.js",
    "app.py", "app.ts", "app.tsx", "app.js",
    "server.py", "server.ts", "server.js",
    "cli.py", "cli.ts", "cli.js",
    "index.ts", "index.tsx", "index.js", "index.py",
    "routes.py", "routes.ts", "routes.js",
}
NON_FEATURE_TOP_LEVEL_DIRS = {"archive", "examples"}
NON_FEATURE_FILENAMES = {"install.sh", "uninstall.sh"}
COMMON_FEATURE_TERMS = {
    "auth", "authentication", "login", "signup", "session", "user", "users", "profile",
    "checkout", "cart", "payment", "payments", "billing", "invoice", "subscription",
    "subscriptions", "orders", "order", "shipping", "tax", "catalog", "inventory",
    "search", "notifications", "notification", "email", "message", "messaging",
    "dashboard", "admin", "settings", "reporting", "analytics", "webhook", "webhooks",
    "api", "frontend", "backend", "website", "landing", "cms", "blog", "upload", "uploads",
}
GENERIC_CLUSTER_TERMS = {
    "src", "packages", "apps", "tests", "test", "core", "lib", "libs", "module",
    "engine", "runtime", "system", "service", "services", "config", "models",
    "model", "middleware", "utils", "shared", "common",
}
# Universal structural patterns detectable across any codebase.
# Project-specific domains should be discovered by the LLM from
# tree-sitter symbols, semantic metadata, and project context —
# not hardcoded here.
FEATURE_DOMAIN_RULES = [
    {
        "tag": "api_routes",
        "title": "API Routes",
        "artifact_kinds": {"route", "webhook", "middleware"},
        "terms": {"route", "routes", "endpoint", "endpoints", "handler", "handlers", "webhook", "webhooks"},
    },
    {
        "tag": "jobs_and_workers",
        "title": "Jobs And Workers",
        "artifact_kinds": {"job"},
        "terms": {"job", "jobs", "queue", "queues", "scheduler", "cron", "worker", "workers", "task", "tasks"},
    },
    {
        "tag": "testing",
        "title": "Testing",
        "artifact_kinds": {"test_target"},
        "terms": {"test", "tests", "testing", "benchmark", "benchmarks", "fixture", "fixtures"},
    },
]
CODE_NOISE_TERMS = {
    "class", "def", "return", "pass", "true", "false", "null", "none", "const",
    "let", "var", "function", "export", "import", "async", "await", "default",
}
TEXT_STOP_TERMS = {
    "a", "an", "and", "are", "as", "at", "be", "been", "by", "for", "from",
    "has", "have", "if", "in", "into", "is", "it", "its", "of", "on", "or",
    "that", "the", "their", "this", "to", "via", "with", "without", "what",
    "which", "while", "who", "why", "will", "would",
}
DOC_NOISE_TERMS = {
    "doc", "docs", "document", "documents", "documentation",
    "spec", "specs", "format", "formats", "guide", "guides",
    "reference", "references", "overview", "design", "architecture",
    "plan", "plans", "readme", "project",
    "repository", "repo",
    "current", "status", "updated", "version", "file", "files",
    "specification", "implementation", "implemented", "feature", "features",
}
PROJECT_SCOPE_DOC_STEMS = {
    "readme", "contributing", "changelog", "roadmap", "vision",
    "overview", "architecture", "project_plan", "plan",
}
PROJECT_SCOPE_DOC_PHRASES = {
    "project_plan", "repository_plan", "repo_plan", "development_plan",
    "implementation_plan", "project_overview", "repository_overview",
    "architecture_overview", "system_overview",
}
ROLE_SCHEMA_TERMS = {
    "schema", "schemas", "model", "models", "type", "types", "typing",
    "validator", "validation", "contract", "contracts",
}
ROLE_SHARED_INFRA_TERMS = {
    "config", "configs", "token", "tokens", "util", "utils", "shared", "common",
    "base", "client", "clients", "logger", "logging", "middleware",
}
LEGACY_TERMS = {
    "legacy", "deprecated", "obsolete", "old", "backup", "bak", "archive",
    "archived", "v1", "experimental", "prototype", "proto",
}
SUPPORT_TERMS = {
    "test", "tests", "benchmark", "benchmarks", "bench", "fixture", "fixtures",
    "mock", "mocks", "stub", "stubs", "seed", "seeds", "migration", "migrations",
}
HUB_CANDIDATE_FILENAMES = {
    "main.py", "main.ts", "main.tsx", "main.js",
    "app.py", "app.ts", "app.tsx", "app.js",
    "cli.py", "cli.ts", "cli.js",
}


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _normalize_text(value: str) -> str:
    return " ".join((value or "").strip().lower().split())


def _slugify(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "_", _normalize_text(value))
    return slug.strip("_") or "feature"


def generate_compact_tree(project_root: Path, max_depth: int = 6, collapse_threshold: int = 5) -> str:
    """Generate a compact folder tree for session context injection.

    Shows all directories. Shows individual files only in sparse directories
    (fewer than collapse_threshold files). Dense directories are collapsed
    to 'dirname/ (N files)'.

    Excludes common noise directories (node_modules, .git, build artifacts, etc.).
    """
    root = Path(project_root).resolve()
    lines: List[str] = []

    def _walk(directory: Path, prefix: str, depth: int):
        if depth > max_depth:
            return
        try:
            entries = sorted(directory.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower()))
        except PermissionError:
            return

        dirs = [e for e in entries if e.is_dir() and e.name not in IGNORED_DIRS and not e.name.startswith(".")]
        files = [e for e in entries if e.is_file() and not e.name.startswith(".")]

        for d in dirs:
            child_files = []
            try:
                child_files = [c for c in d.iterdir() if c.is_file() and not c.name.startswith(".")]
            except PermissionError:
                pass
            child_dirs = []
            try:
                child_dirs = [c for c in d.iterdir() if c.is_dir() and c.name not in IGNORED_DIRS and not c.name.startswith(".")]
            except PermissionError:
                pass

            if not child_files and not child_dirs:
                lines.append(f"{prefix}{d.name}/")
            elif len(child_files) > collapse_threshold and not child_dirs:
                lines.append(f"{prefix}{d.name}/ ({len(child_files)} files)")
            else:
                lines.append(f"{prefix}{d.name}/")
                _walk(d, prefix + "  ", depth + 1)

        if len(files) <= collapse_threshold:
            for f in files:
                suffix = f.suffix.lower()
                if suffix in CODE_EXTENSIONS or suffix in DOC_EXTENSIONS or f.name in {"package.json", "pyproject.toml", "tsconfig.json", "requirements.txt", ".gitignore"}:
                    lines.append(f"{prefix}{f.name}")
        elif files:
            lines.append(f"{prefix}({len(files)} files)")

    lines.append(f"{root.name}/")
    _walk(root, "  ", 0)
    return "\n".join(lines)


def canonical_devproject_path(project_root: Path) -> Path:
    root = Path(project_root).resolve()
    return root / f"{root.name}.devproject"


def resolve_devproject_path(path_or_root: Path) -> Path:
    path_or_root = Path(path_or_root)
    if path_or_root.name == ".devproject" or path_or_root.suffix == ".devproject":
        return path_or_root

    root = path_or_root.resolve()
    canonical = canonical_devproject_path(root)
    if canonical.exists():
        return canonical

    matches = sorted(candidate for candidate in root.glob("*.devproject") if candidate.is_file())
    if matches:
        return matches[0]

    legacy = root / ".devproject"
    if legacy.exists():
        return legacy

    return canonical


def default_devsession_dir(start: Optional[Path] = None) -> Path:
    base = Path(start or Path.cwd()).resolve()
    project_root = discover_project_root(base)
    if project_root is not None:
        return project_root / "devsession"
    return Path.home() / "reccli" / "devsession"


def default_devsession_path(
    start: Optional[Path] = None,
    timestamp: Optional[datetime] = None,
) -> Path:
    dt = timestamp or datetime.now()
    filename = f"{dt.strftime('%m%d%Y_%H%M')}.devsession"
    return default_devsession_dir(start) / filename


def discover_project_root(start: Optional[Path] = None, max_levels: int = 10) -> Optional[Path]:
    """Search upward for a repository root or existing .devproject."""
    current = (start or Path.cwd()).resolve()
    if current.is_file():
        current = current.parent

    levels = 0
    while True:
        if resolve_devproject_path(current).exists() or (current / ".git").exists():
            return current
        parent = current.parent
        if parent == current or levels >= max_levels:
            return None
        current = parent
        levels += 1


def initialize_session_project_metadata(session, base_dir: Optional[Path] = None) -> Optional[Path]:
    """Attach working-directory and project-root hints to a session."""
    working_directory = (base_dir or Path.cwd()).resolve()
    session.metadata.setdefault("working_directory", str(working_directory))

    project_root = discover_project_root(working_directory)
    if project_root is not None:
        session.metadata["project_root"] = str(project_root)
    return project_root


def resolve_session_project_root(session, fallback: Optional[Path] = None) -> Optional[Path]:
    """Resolve a project root using session metadata first, then fallback discovery."""
    metadata = getattr(session, "metadata", {}) or {}

    project_root = metadata.get("project_root")
    if project_root:
        candidate = Path(project_root).expanduser()
        if candidate.exists():
            return candidate.resolve()

    working_directory = metadata.get("working_directory")
    if working_directory:
        discovered = discover_project_root(Path(working_directory).expanduser())
        if discovered is not None:
            session.metadata["project_root"] = str(discovered)
            return discovered

    if fallback is not None:
        discovered = discover_project_root(fallback)
        if discovered is not None:
            session.metadata["project_root"] = str(discovered)
            return discovered

    return None


def _coerce_project_path(path_or_root: Path) -> Path:
    return resolve_devproject_path(path_or_root)


def _relative_or_absolute(path: Path, root: Path) -> str:
    try:
        return path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        return str(path.resolve())


def create_devproject(project_root: Path) -> Dict[str, Any]:
    return {
        "format": "devproject",
        "version": FORMAT_VERSION,
        "project_root": str(project_root.resolve()),
        "updated_at": _utc_now(),
        "last_updated_session": None,
        "project": {
            "name": project_root.name,
            "description": f"Project dashboard for {project_root.name}",
            "status": "active",
            "source": "auto",
        },
        "features": [],
        "hub_files": [],
        "shared_infrastructure": [],
        "unassigned": [],
        "project_docs": [],
        "session_index": [],
        "proposals": [],
        "update_history": [],
    }


def load_devproject(path_or_root: Path) -> Dict[str, Any]:
    path = _coerce_project_path(path_or_root)
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)

    data.setdefault("features", [])
    data.setdefault("hub_files", [])
    data.setdefault("shared_infrastructure", [])
    data.setdefault("unassigned", [])
    data.setdefault("project_docs", [])
    data.setdefault("session_index", [])
    data.setdefault("proposals", [])
    data.setdefault("update_history", [])
    data.setdefault("project", {})
    return data


def save_devproject(document: Dict[str, Any], path_or_root: Path) -> Path:
    path = _coerce_project_path(path_or_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    document["updated_at"] = _utc_now()
    with open(path, "w", encoding="utf-8") as f:
        json.dump(document, f, indent=2, ensure_ascii=False)
        f.write("\n")
    return path


def create_llm_client_for_model(model: str):
    """Create an Anthropic or OpenAI client for project clustering."""
    from ..runtime.config import Config

    config = Config()
    if model.startswith("claude"):
        try:
            import anthropic
        except ImportError as e:
            raise RuntimeError("anthropic package not installed") from e
        api_key = config.get_api_key("anthropic")
        if not api_key:
            raise RuntimeError("Anthropic API key not configured")
        return anthropic.Anthropic(api_key=api_key)

    if model.startswith("gpt"):
        try:
            from openai import OpenAI
        except ImportError as e:
            raise RuntimeError("openai package not installed") from e
        api_key = config.get_api_key("openai")
        if not api_key:
            raise RuntimeError("OpenAI API key not configured")
        return OpenAI(api_key=api_key)

    raise RuntimeError(f"Unsupported model for .devproject clustering: {model}")


class DevProjectManager:
    """Manage .devproject documents and proposal lifecycle."""

    def __init__(self, project_root: Path):
        self.project_root = Path(project_root).resolve()
        self.path = canonical_devproject_path(self.project_root)
        self._tree_sitter_parser_cache: Dict[str, Any] = {}

    def load_or_create(self) -> Dict[str, Any]:
        existing_path = resolve_devproject_path(self.project_root)
        if existing_path.exists():
            return load_devproject(existing_path)
        return create_devproject(self.project_root)

    def save(self, document: Dict[str, Any]) -> Path:
        return save_devproject(document, self.path)

    def initialize_from_codebase(
        self,
        force: bool = False,
        use_llm: bool = True,
        llm_client = None,
        model: Optional[str] = None,
        project_context: Optional[str] = None,
        missing_feature_hints: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        had_existing_document = resolve_devproject_path(self.project_root).exists()
        if had_existing_document and not force:
            raise ValueError(f".devproject already exists at {resolve_devproject_path(self.project_root)}")

        document = self._build_document_from_codebase(
            use_llm=use_llm,
            llm_client=llm_client,
            model=model,
            project_context=project_context,
            missing_feature_hints=missing_feature_hints,
        )
        self.save(document)
        _, proposal = self.generate_sync_proposal_from_codebase()
        if proposal is not None:
            document, _ = self.apply_proposal(proposal["proposal_id"])
        return document

    def validate_and_fix_file_paths(self) -> Dict[str, Any]:
        """Lightweight file path validation against the live codebase.

        Checks every files_touched path in each feature. If a path no longer
        exists on disk, attempts to find a same-named file elsewhere in the
        project (rename/move detection). Returns a summary of what was fixed
        and what was flagged.

        This is fast (filesystem only, no LLM) and safe to run inline during
        compaction.
        """
        document = self.load_or_create()
        features = document.get("features", [])
        fixed = []
        missing = []
        changed = False

        # Build a filename-to-paths index for move detection
        name_index: Dict[str, List[str]] = {}
        for path in self.project_root.rglob("*"):
            if not path.is_file():
                continue
            if any(part in IGNORED_DIRS for part in path.relative_to(self.project_root).parts):
                continue
            rel = path.relative_to(self.project_root).as_posix()
            name_index.setdefault(path.name, []).append(rel)

        for feature in features:
            updated_files: List[str] = []
            for file_path in feature.get("files_touched", []):
                full_path = self.project_root / file_path
                if full_path.exists():
                    updated_files.append(file_path)
                    continue

                # Try to find the file by name elsewhere
                filename = Path(file_path).name
                candidates = name_index.get(filename, [])
                if len(candidates) == 1:
                    new_path = candidates[0]
                    updated_files.append(new_path)
                    fixed.append({
                        "feature": feature.get("title", ""),
                        "old_path": file_path,
                        "new_path": new_path,
                    })
                    changed = True
                else:
                    missing.append({
                        "feature": feature.get("title", ""),
                        "path": file_path,
                        "candidates": candidates[:5],
                    })
                    changed = True

            feature["files_touched"] = sorted(set(updated_files))

        if changed:
            self.save(document)

        return {
            "fixed": fixed,
            "missing": missing,
            "changed": changed,
        }

    def generate_sync_proposal_from_codebase(self) -> Tuple[Dict[str, Any], Optional[Dict[str, Any]]]:
        document = self.load_or_create()
        diff: List[Dict[str, Any]] = []
        features = document.get("features", [])
        inventory = self._build_codebase_inventory()
        excluded_paths = (
            set(document.get("hub_files", []))
            | set(document.get("shared_infrastructure", []))
            | set(document.get("unassigned", []))
        )
        additions_by_feature: Dict[str, List[str]] = {}
        uncovered_files: List[Dict[str, Any]] = []

        known_inventory_paths = {item["path"] for item in inventory.get("files", [])}

        # --- Staleness computation ---
        self._compute_feature_staleness(features, known_inventory_paths, inventory)

        for file_info in inventory.get("files", []):
            path = file_info["path"]
            if path in excluded_paths:
                continue
            if not self._should_expose_file_in_feature(path) and not self._should_backfill_uncategorized(path):
                continue
            owner = self._find_covering_feature(features, path)
            if owner is None:
                support_owner = self._find_support_feature_match(file_info, features)
                if support_owner is not None and self._is_support_like_file(file_info):
                    additions_by_feature.setdefault(support_owner["feature_id"], []).append(path)
                    continue
                if not self._should_backfill_uncategorized(path):
                    continue
                uncovered_files.append(file_info)
                continue

            if path not in owner.get("files_touched", []):
                additions_by_feature.setdefault(owner["feature_id"], []).append(path)

        for feature in features:
            files_add = sorted(set(additions_by_feature.get(feature.get("feature_id"), [])))
            if not files_add:
                continue
            diff.append({
                "op": "update_feature",
                "feature_id": feature["feature_id"],
                "changes": {
                    "files_touched_add": files_add,
                    "updated_at": _utc_now(),
                },
            })

        if uncovered_files:
            fallback_inventory = {
                "files": uncovered_files,
            }
            for candidate in self._scan_codebase_features(fallback_inventory):
                match = self._match_feature(features, candidate)
                if match is None:
                    diff.append({"op": "add_feature", "feature": candidate})
                    continue

                files_add = sorted(set(candidate.get("files_touched", [])) - set(match.get("files_touched", [])))
                if not files_add:
                    continue
                diff.append({
                    "op": "update_feature",
                    "feature_id": match["feature_id"],
                    "changes": {
                        "files_touched_add": files_add,
                        "updated_at": _utc_now(),
                    },
                })

        relinked = deepcopy(document)
        self._link_documents_to_document(relinked, inventory, use_embeddings=False)
        for feature in features:
            relinked_feature = next(
                (item for item in relinked.get("features", []) if item.get("feature_id") == feature.get("feature_id")),
                None,
            )
            if relinked_feature is None:
                continue
            docs_add = self._doc_links_additions(feature.get("docs", []), relinked_feature.get("docs", []))
            if docs_add:
                diff.append({
                    "op": "update_feature",
                    "feature_id": feature["feature_id"],
                    "changes": {
                        "docs_add": docs_add,
                        "updated_at": _utc_now(),
                    },
                })

        project_docs_add = self._doc_links_additions(document.get("project_docs", []), relinked.get("project_docs", []))
        if project_docs_add:
            diff.append({
                "op": "update_project_docs",
                "docs_add": project_docs_add,
            })

        diff = [op for op in diff if not self._diff_op_is_noop(op)]
        if not diff:
            self._drop_pending_for_session(document, "codebase_sync")
            self.save(document)
            return document, None

        proposal = {
            "proposal_id": f"projupd_{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')}",
            "status": "pending",
            "created_at": _utc_now(),
            "source_session_id": "codebase_sync",
            "diff": diff,
        }

        self._drop_pending_for_session(document, "codebase_sync")
        document.setdefault("proposals", []).append(proposal)
        self.save(document)
        return document, proposal

    def generate_proposal_for_session(
        self,
        session,
        session_path: Path,
        ensure_summary: bool = True,
    ) -> Tuple[Dict[str, Any], Optional[Dict[str, Any]]]:
        document = self.load_or_create()

        if ensure_summary and not getattr(session, "summary", None):
            from ..summarization.summarizer import SessionSummarizer
            from ..summarization.summary_schema import ensure_summary_span_links

            summarizer = SessionSummarizer(llm_client=None)
            session.summary = summarizer.summarize_session(
                conversation=session.conversation,
                redact_secrets=False,
            )
            session.spans = ensure_summary_span_links(session.summary, getattr(session, "spans", []))

        summary = getattr(session, "summary", None) or {}
        session_id = session.session_id
        candidates = self._extract_feature_candidates(session, summary)

        diff: List[Dict[str, Any]] = []
        linked_feature_ids: List[str] = []

        for candidate in candidates:
            match = self._match_feature(document.get("features", []), candidate)
            if match is None:
                diff.append({"op": "add_feature", "feature": candidate})
                linked_feature_ids.append(candidate["feature_id"])
                continue

            linked_feature_ids.append(match["feature_id"])
            changes: Dict[str, Any] = {}
            new_files = sorted(set(candidate["files_touched"]) - set(match.get("files_touched", [])))
            if new_files:
                changes["files_touched_add"] = new_files
            if session_id not in match.get("session_ids", []):
                changes["session_ids_add"] = [session_id]
            changes["last_updated_session"] = session_id
            changes["updated_at"] = _utc_now()
            diff.append({
                "op": "update_feature",
                "feature_id": match["feature_id"],
                "changes": changes,
            })

        session_entry = self._build_session_index_entry(session, session_path, linked_feature_ids)
        existing_entry = self._find_session_entry(document, session_id)
        if existing_entry is None:
            diff.append({"op": "link_session", "session": session_entry})
        else:
            missing_feature_ids = sorted(set(linked_feature_ids) - set(existing_entry.get("feature_ids", [])))
            if missing_feature_ids:
                diff.append({
                    "op": "link_session",
                    "session": {
                        "session_id": session_id,
                        "feature_ids_add": missing_feature_ids,
                    },
                })

        diff = [op for op in diff if not self._diff_op_is_noop(op)]
        if not diff:
            self._drop_pending_for_session(document, session_id)
            self.save(document)
            return document, None

        proposal = {
            "proposal_id": f"projupd_{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')}",
            "status": "pending",
            "created_at": _utc_now(),
            "source_session_id": session_id,
            "diff": diff,
        }

        self._drop_pending_for_session(document, session_id)
        document.setdefault("proposals", []).append(proposal)
        document["last_updated_session"] = session_id
        self.save(document)
        return document, proposal

    def apply_proposal(self, proposal_id: str) -> Tuple[Dict[str, Any], Dict[str, Any]]:
        document = self.load_or_create()
        proposal = next((p for p in document.get("proposals", []) if p.get("proposal_id") == proposal_id), None)
        if proposal is None:
            raise ValueError(f"Proposal not found: {proposal_id}")

        for op in proposal.get("diff", []):
            self._apply_diff_op(document, op)

        # Detect boundary overlaps after applying and attach as warnings
        overlaps = self.detect_boundary_overlaps(document)
        if overlaps:
            proposal.setdefault("warnings", []).append({
                "kind": "boundary_overlaps",
                "count": len(overlaps),
                "details": overlaps[:10],  # Cap detail output
            })

        proposal["status"] = "accepted"
        document["last_updated_session"] = proposal.get("source_session_id")
        self._archive_proposal(document, proposal)
        self.save(document)
        return document, proposal

    def reject_proposal(self, proposal_id: str, reason: Optional[str] = None) -> Tuple[Dict[str, Any], Dict[str, Any]]:
        document = self.load_or_create()
        proposal = next((p for p in document.get("proposals", []) if p.get("proposal_id") == proposal_id), None)
        if proposal is None:
            raise ValueError(f"Proposal not found: {proposal_id}")

        proposal["status"] = "rejected"
        if reason:
            proposal["reason"] = reason
        self._archive_proposal(document, proposal)
        self.save(document)
        return document, proposal

    def _build_session_index_entry(self, session, session_path: Path, feature_ids: List[str]) -> Dict[str, Any]:
        return {
            "session_id": session.session_id,
            "path": _relative_or_absolute(session_path, self.project_root),
            "started_at": session.metadata.get("created_at") or getattr(session, "created", _utc_now()),
            "ended_at": getattr(session, "updated", None) or _utc_now(),
            "feature_ids": sorted(set(feature_ids)),
        }

    def _build_document_from_codebase(
        self,
        use_llm: bool = False,
        llm_client = None,
        model: Optional[str] = None,
        project_context: Optional[str] = None,
        missing_feature_hints: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        document = create_devproject(self.project_root)
        inventory = self._build_codebase_inventory()
        existing_document = load_devproject(self.path) if self.path.exists() else None
        manual_features = self._extract_manual_features(existing_document, inventory)
        document["project"] = self._scan_project_metadata(document["project"])
        document["project"]["source"] = "auto"
        resolved_project_context = (project_context or inventory.get("project_description") or "").strip() or None
        if resolved_project_context:
            document["project"]["description"] = resolved_project_context

        if use_llm:
            clustered = self._cluster_inventory_with_llm(
                inventory,
                llm_client=llm_client,
                model=model,
                project_context=resolved_project_context,
                missing_feature_hints=missing_feature_hints,
            )
            document["project"]["description"] = clustered["project"].get("description") or document["project"]["description"]
            document["features"] = clustered["features"]
            document["hub_files"] = clustered.get("hub_files", [])
            document["shared_infrastructure"] = clustered.get("shared_infrastructure", [])
            document["unassigned"] = clustered.get("unassigned", [])
        else:
            document["features"] = self._scan_codebase_features(inventory)
            document["hub_files"] = []
            document["shared_infrastructure"] = []
            document["unassigned"] = []
        if manual_features:
            document["features"] = self._preserve_manual_features(document["features"], manual_features)
        self._link_documents_to_document(
            document,
            inventory,
            use_embeddings=use_llm,
        )
        return document

    def _extract_manual_features(
        self,
        existing_document: Optional[Dict[str, Any]],
        inventory: Dict[str, Any],
    ) -> List[Dict[str, Any]]:
        if not existing_document:
            return []

        known_files = {
            item["path"]
            for item in inventory.get("files", [])
            if self._should_expose_file_in_feature(item["path"])
        }
        manual_features: List[Dict[str, Any]] = []

        for feature in existing_document.get("features", []):
            if feature.get("source") != "manual":
                continue

            preserved = deepcopy(feature)
            preserved["files_touched"] = sorted(
                {
                    path
                    for path in preserved.get("files_touched", [])
                    if path in known_files
                }
            )
            if not preserved.get("file_boundaries"):
                preserved["file_boundaries"] = self._candidate_boundaries(preserved["files_touched"])
            preserved["docs"] = []
            manual_features.append(preserved)

        return manual_features

    def _preserve_manual_features(
        self,
        auto_features: List[Dict[str, Any]],
        manual_features: List[Dict[str, Any]],
    ) -> List[Dict[str, Any]]:
        manual_files = {
            path
            for feature in manual_features
            for path in feature.get("files_touched", [])
        }
        preserved_auto: List[Dict[str, Any]] = []

        for feature in auto_features:
            candidate = deepcopy(feature)
            if manual_files:
                candidate["files_touched"] = sorted(
                    path for path in candidate.get("files_touched", [])
                    if path not in manual_files
                )
                candidate["file_boundaries"] = sorted(
                    boundary
                    for boundary in candidate.get("file_boundaries", [])
                    if not self._boundary_claims_manual_files(boundary, manual_files)
                )
            if candidate.get("files_touched"):
                preserved_auto.append(candidate)

        return manual_features + preserved_auto

    def _boundary_claims_manual_files(self, boundary: str, manual_files: set[str]) -> bool:
        if not boundary:
            return False
        if boundary.endswith("/**"):
            prefix = boundary[:-3]
            return any(path.startswith(prefix) for path in manual_files)
        return boundary in manual_files

    def _scan_project_metadata(self, base: Dict[str, Any]) -> Dict[str, Any]:
        project = deepcopy(base)
        project["name"] = self.project_root.name
        description = self._read_project_description()
        if description:
            project["description"] = description
        project["source"] = "auto"
        return project

    def suggest_init_project_context(self) -> Optional[str]:
        return self._read_project_description()

    def _read_readme_for_clustering(self, max_chars: int = 3000) -> Optional[str]:
        """Read a larger README excerpt for use as the primary clustering context."""
        readme_candidates = [
            self.project_root / "README.md",
            self.project_root / "README",
            self.project_root / "readme.md",
            self.project_root / "README.rst",
            self.project_root / "README.txt",
        ]
        for path in readme_candidates:
            if not path.exists():
                continue
            try:
                text = path.read_text(encoding="utf-8", errors="ignore")
            except Exception:
                continue
            if text.strip():
                return text[:max_chars].rstrip()
        return None

    def _read_project_description(self) -> Optional[str]:
        readme_candidates = [
            self.project_root / "README.md",
            self.project_root / "README",
            self.project_root / "readme.md",
        ]
        for path in readme_candidates:
            if not path.exists():
                continue
            try:
                text = path.read_text(encoding="utf-8", errors="ignore")
            except Exception:
                continue
            description = self._extract_description_from_readme(text)
            if description:
                return description

        package_json = self.project_root / "package.json"
        if package_json.exists():
            try:
                data = json.loads(package_json.read_text(encoding="utf-8"))
                description = (data.get("description") or "").strip()
                if description:
                    return description
            except Exception:
                pass

        pyproject = self.project_root / "pyproject.toml"
        if pyproject.exists():
            try:
                text = pyproject.read_text(encoding="utf-8", errors="ignore")
            except Exception:
                text = ""
            match = re.search(r'description\s*=\s*"([^"]+)"', text)
            if match:
                return match.group(1).strip()

        return None

    def _extract_description_from_readme(self, text: str) -> Optional[str]:
        lines = [line.strip() for line in text.splitlines()]
        paragraph: List[str] = []
        for line in lines:
            if not line or line.startswith("#") or line.startswith("```"):
                if paragraph:
                    break
                continue
            paragraph.append(line)
            if len(" ".join(paragraph)) > 240:
                break
        if not paragraph:
            return None
        return " ".join(paragraph)[:240].rstrip()

    def _build_codebase_inventory(self) -> Dict[str, Any]:
        files: List[Dict[str, Any]] = []
        documents: List[Dict[str, Any]] = []
        path_map: Dict[str, Dict[str, Any]] = {}
        python_module_map: Dict[str, str] = {}

        for path in self.project_root.rglob("*"):
            if not self._should_include_inventory_file(path):
                continue

            rel = path.relative_to(self.project_root).as_posix()
            kind = self._classify_inventory_kind(path)
            if kind == "code":
                structural_symbols = self._extract_structural_symbols(path)
                semantic_metadata = self._extract_file_semantic_metadata(path)
                file_info = {
                    "path": rel,
                    "kind": "code",
                    "language": self._detect_language(path),
                    "group_key": self._feature_group_key(Path(rel)),
                    "imports": [],
                    "imported_by": [],
                    "is_entrypoint": self._is_key_file(path),
                    "is_test": self._is_test_file(path),
                    "is_config": self._is_config_file(path),
                    "snippet": semantic_metadata.get("docstring_excerpt") or semantic_metadata.get("comment_excerpt") or self._read_key_file_snippet(path),
                    "top_identifiers": self._extract_top_identifiers(path, structural_symbols=structural_symbols),
                    "structural_symbols": structural_symbols,
                    "signatures": semantic_metadata.get("signatures", []),
                    "exported_symbols": semantic_metadata.get("exported_symbols", []),
                    "decorators": semantic_metadata.get("decorators", []),
                    "route_metadata": semantic_metadata.get("route_metadata", []),
                    "docstring_excerpt": semantic_metadata.get("docstring_excerpt", ""),
                    "comment_excerpt": semantic_metadata.get("comment_excerpt", ""),
                }
                files.append(file_info)
                path_map[rel] = file_info

                for module_name in self._python_module_aliases(rel):
                    python_module_map[module_name] = rel
            elif kind == "doc":
                doc_info = self._build_document_item(path, rel)
                documents.append(doc_info)

        for file_info in files:
            path = self.project_root / file_info["path"]
            imports = self._extract_local_imports(path, file_info["path"], path_map, python_module_map)
            file_info["imports"] = imports

        for file_info in files:
            for dep in file_info["imports"]:
                target = path_map.get(dep)
                if target is not None:
                    target["imported_by"].append(file_info["path"])

        for file_info in files:
            file_info["imports"] = sorted(set(file_info["imports"]))
            file_info["imported_by"] = sorted(set(file_info["imported_by"]))

        for file_info in files:
            file_info["semantic_tags"] = self._extract_file_semantic_tags(file_info)

        for file_info in files:
            hub_metrics = self._compute_hub_metrics(file_info, path_map)
            file_info.update(hub_metrics)
            file_info["legacy_score"] = self._compute_legacy_score(file_info)
            file_info["support_score"] = self._compute_support_score(file_info)
            file_info["role_hint"] = self._infer_role_hint(file_info)
            file_info["artifacts"] = self._extract_file_artifacts(file_info)

        known_paths = sorted(path_map.keys(), key=len, reverse=True)
        for doc in documents:
            doc["referenced_paths"] = self._extract_document_code_references(doc.get("content", ""), known_paths)

        artifact_candidates = self._derive_artifact_feature_candidates(files)
        return {
            "project_root": str(self.project_root),
            "project_name": self.project_root.name,
            "project_description": self._read_project_description(),
            "files": sorted(files, key=lambda item: item["path"]),
            "documents": sorted(documents, key=lambda item: item["path"]),
            "import_clusters": self._build_import_clusters(files),
            "artifact_candidates": artifact_candidates,
        }

    def _build_import_clusters(self, files: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        file_map = {item["path"]: item for item in files}
        adjacency: Dict[str, set[str]] = {item["path"]: set() for item in files}

        for item in files:
            path = item["path"]
            for neighbor in item.get("imports", []):
                if neighbor in adjacency:
                    adjacency[path].add(neighbor)
                    adjacency[neighbor].add(path)
            for neighbor in item.get("imported_by", []):
                if neighbor in adjacency:
                    adjacency[path].add(neighbor)
                    adjacency[neighbor].add(path)

        visited: set[str] = set()
        clusters: List[Dict[str, Any]] = []

        for path in sorted(adjacency):
            if path in visited:
                continue
            stack = [path]
            component: List[str] = []
            while stack:
                current = stack.pop()
                if current in visited:
                    continue
                visited.add(current)
                component.append(current)
                stack.extend(sorted(adjacency[current] - visited))

            if len(component) <= 1:
                continue

            component_files = sorted(component)
            component_items = [file_map[item_path] for item_path in component_files]
            group_keys = sorted({item["group_key"] for item in component_items if item.get("group_key")})
            entrypoints = sorted(item["path"] for item in component_items if item.get("is_entrypoint"))
            analysis = self._analyze_cluster_granularity(component_items, adjacency)
            subclusters = self._build_cluster_subcommunities(component_files, adjacency, file_map)
            clusters.append({
                "cluster_id": f"imp_{len(clusters) + 1:03d}",
                "size": len(component_files),
                "files": component_files,
                "group_keys": group_keys,
                "entrypoints": entrypoints,
                "granularity_hint": analysis["granularity_hint"],
                "granularity_reason": analysis["reason"],
                "scores": analysis["scores"],
                "cluster_terms": analysis["cluster_terms"],
                "subclusters": subclusters,
            })

        clusters.sort(key=lambda item: (-item["size"], item["cluster_id"]))
        return clusters

    def _build_cluster_subcommunities(
        self,
        component_files: List[str],
        adjacency: Dict[str, set[str]],
        file_map: Dict[str, Dict[str, Any]],
    ) -> List[Dict[str, Any]]:
        if len(component_files) < 8:
            return []

        communities = self._partition_component_recursively(component_files, adjacency)
        if len(communities) <= 1:
            return []

        subclusters: List[Dict[str, Any]] = []
        for idx, community in enumerate(communities, start=1):
            items = [file_map[path] for path in community]
            analysis = self._analyze_cluster_granularity(items, adjacency)
            group_keys = sorted({item["group_key"] for item in items if item.get("group_key")})
            entrypoints = sorted(item["path"] for item in items if item.get("is_entrypoint"))
            subclusters.append({
                "subcluster_id": f"sub_{idx:02d}",
                "size": len(community),
                "files": community,
                "group_keys": group_keys,
                "entrypoints": entrypoints,
                "granularity_hint": analysis["granularity_hint"],
                "granularity_reason": analysis["reason"],
                "scores": analysis["scores"],
                "cluster_terms": analysis["cluster_terms"],
            })

        return subclusters

    def _partition_component_recursively(
        self,
        component_files: List[str],
        adjacency: Dict[str, set[str]],
        min_size: int = 3,
        split_threshold: float = 0.06,
    ) -> List[List[str]]:
        def recurse(nodes: List[str]) -> List[List[str]]:
            if len(nodes) < max(6, min_size * 2):
                return [sorted(nodes)]

            split = self._best_component_split(nodes, adjacency, min_size=min_size)
            if split is None:
                return [sorted(nodes)]

            partition, modularity_gain = split
            if modularity_gain < split_threshold:
                return [sorted(nodes)]

            communities: List[List[str]] = []
            for community in partition:
                if len(community) >= max(6, min_size * 2):
                    communities.extend(recurse(sorted(community)))
                else:
                    communities.append(sorted(community))
            return communities

        communities = recurse(sorted(component_files))
        communities.sort(key=lambda group: (-len(group), group[0]))
        return communities

    def _best_component_split(
        self,
        component_files: List[str],
        adjacency: Dict[str, set[str]],
        min_size: int = 3,
    ) -> Optional[Tuple[List[List[str]], float]]:
        node_set = set(component_files)
        working = {
            node: set(adjacency.get(node, set())) & node_set
            for node in component_files
        }
        original = {
            node: set(neighbors)
            for node, neighbors in working.items()
        }
        best_partition: Optional[List[List[str]]] = None
        best_modularity = 0.0

        while True:
            components = self._connected_components(working)
            valid_components = [component for component in components if len(component) >= min_size]
            if len(valid_components) > 1 and sum(len(component) for component in valid_components) == len(component_files):
                modularity = self._partition_modularity(valid_components, original)
                if modularity > best_modularity:
                    best_modularity = modularity
                    best_partition = [sorted(component) for component in valid_components]

            betweenness = self._edge_betweenness(working)
            if not betweenness:
                break

            max_score = max(betweenness.values())
            if max_score <= 0:
                break

            removed_any = False
            for edge, score in betweenness.items():
                if score != max_score:
                    continue
                left, right = edge
                if right in working[left]:
                    working[left].remove(right)
                    removed_any = True
                if left in working[right]:
                    working[right].remove(left)
                    removed_any = True
            if not removed_any:
                break

        if best_partition is None:
            return None

        return best_partition, best_modularity

    def _connected_components(self, adjacency: Dict[str, set[str]]) -> List[List[str]]:
        visited: set[str] = set()
        components: List[List[str]] = []

        for node in sorted(adjacency):
            if node in visited:
                continue
            stack = [node]
            component: List[str] = []
            while stack:
                current = stack.pop()
                if current in visited:
                    continue
                visited.add(current)
                component.append(current)
                stack.extend(sorted(adjacency[current] - visited))
            components.append(sorted(component))

        return components

    def _edge_betweenness(self, adjacency: Dict[str, set[str]]) -> Dict[Tuple[str, str], float]:
        betweenness: Dict[Tuple[str, str], float] = {}
        nodes = sorted(adjacency)

        for source in nodes:
            stack: List[str] = []
            predecessors = {node: [] for node in nodes}
            sigma = {node: 0.0 for node in nodes}
            sigma[source] = 1.0
            distance = {source: 0}
            queue = [source]

            while queue:
                vertex = queue.pop(0)
                stack.append(vertex)
                for neighbor in adjacency[vertex]:
                    if neighbor not in distance:
                        queue.append(neighbor)
                        distance[neighbor] = distance[vertex] + 1
                    if distance[neighbor] == distance[vertex] + 1:
                        sigma[neighbor] += sigma[vertex]
                        predecessors[neighbor].append(vertex)

            dependency = {node: 0.0 for node in nodes}
            while stack:
                vertex = stack.pop()
                for predecessor in predecessors[vertex]:
                    if sigma[vertex] == 0:
                        continue
                    contribution = (sigma[predecessor] / sigma[vertex]) * (1.0 + dependency[vertex])
                    edge = tuple(sorted((predecessor, vertex)))
                    betweenness[edge] = betweenness.get(edge, 0.0) + contribution
                    dependency[predecessor] += contribution

        for edge in list(betweenness):
            betweenness[edge] /= 2.0
        return betweenness

    def _partition_modularity(
        self,
        partition: List[List[str]],
        adjacency: Dict[str, set[str]],
    ) -> float:
        degrees = {node: len(neighbors) for node, neighbors in adjacency.items()}
        edge_count = sum(degrees.values()) / 2.0
        if edge_count == 0:
            return 0.0

        modularity = 0.0
        for community in partition:
            community_set = set(community)
            internal_edges = 0.0
            degree_sum = 0.0
            for node in community:
                degree_sum += degrees.get(node, 0)
                internal_edges += sum(1 for neighbor in adjacency.get(node, set()) if neighbor in community_set)
            internal_edges /= 2.0
            modularity += (internal_edges / edge_count) - (degree_sum / (2.0 * edge_count)) ** 2
        return modularity

    def _file_text_profile(self, file_info: Dict[str, Any]) -> str:
        parts = [
            file_info.get("path", ""),
            file_info.get("group_key", ""),
            file_info.get("snippet", ""),
            file_info.get("docstring_excerpt", ""),
            file_info.get("comment_excerpt", ""),
            file_info.get("role_hint", ""),
            " ".join(file_info.get("top_identifiers", [])),
            " ".join(file_info.get("structural_symbols", [])),
            " ".join(file_info.get("signatures", [])),
            " ".join(file_info.get("exported_symbols", [])),
            " ".join(file_info.get("decorators", [])),
            " ".join(file_info.get("route_metadata", [])),
            " ".join(file_info.get("semantic_tags", [])),
        ]
        parts.extend(file_info.get("imports", []))
        parts.extend(file_info.get("imported_by", []))
        return "\n".join(part for part in parts if part)

    def _analyze_cluster_granularity(
        self,
        component_items: List[Dict[str, Any]],
        adjacency: Dict[str, set[str]],
    ) -> Dict[str, Any]:
        files = [item["path"] for item in component_items]
        entrypoint_count = sum(1 for item in component_items if item.get("is_entrypoint"))
        group_key_count = len({item["group_key"] for item in component_items if item.get("group_key")})
        dir_count = len({str(Path(path).parent) for path in files})
        edge_count = sum(len(adjacency.get(path, set()) & set(files)) for path in files) // 2
        max_edges = max(1, (len(files) * (len(files) - 1)) // 2)
        density = edge_count / max_edges

        cluster_tokens = self._semantic_terms("\n".join(self._file_text_profile(item) for item in component_items))
        unique_cluster_tokens = sorted({
            token
            for token in cluster_tokens
            if token not in {"packages", "src", "apps", "tests"} and token not in CODE_NOISE_TERMS
        })
        score_tokens = [
            token for token in unique_cluster_tokens
            if not any(sep in token for sep in ("/", ".", "_", "-"))
        ]
        common_hits = sum(1 for token in score_tokens if token in COMMON_FEATURE_TERMS)
        commonality_score = min(1.0, common_hits / 4.0)

        distinctive_tokens = [
            token for token in score_tokens
            if token not in COMMON_FEATURE_TERMS and token not in GENERIC_CLUSTER_TERMS
        ]
        novelty_score = min(1.0, len(distinctive_tokens) / 6.0)

        size_score = min(1.0, len(files) / 8.0)
        heterogeneity_signal = 0.0
        if group_key_count > 1:
            heterogeneity_signal += min(0.5, 0.15 * (group_key_count - 1))
        if dir_count > 2:
            heterogeneity_signal += min(0.3, 0.08 * (dir_count - 2))
        if entrypoint_count > 1:
            heterogeneity_signal += min(0.3, 0.15 * (entrypoint_count - 1))
        if density < 0.45:
            heterogeneity_signal += min(0.4, (0.45 - density))
        heterogeneity_score = min(1.0, heterogeneity_signal)

        split_score = round((size_score * 0.35) + (heterogeneity_score * 0.35) + (novelty_score * 0.35) - (commonality_score * 0.25), 4)
        if split_score >= 0.8 or (
            novelty_score >= 0.6 and heterogeneity_score >= 0.35 and commonality_score < 0.6
        ):
            granularity_hint = "fine"
        elif split_score >= 0.35:
            granularity_hint = "moderate"
        else:
            granularity_hint = "coarse"

        reasons: List[str] = []
        if novelty_score >= 0.45:
            reasons.append("repo-specific vocabulary suggests a novel subsystem")
        if heterogeneity_score >= 0.35:
            reasons.append("multiple directories or weak cohesion suggest independently workable parts")
        if commonality_score >= 0.5:
            reasons.append("common product vocabulary suggests a standard feature")
        if entrypoint_count > 1:
            reasons.append("multiple entrypoints increase subsystem breadth")
        if not reasons:
            reasons.append("cluster looks cohesive and semantically obvious")

        return {
            "granularity_hint": granularity_hint,
            "reason": "; ".join(reasons[:2]),
            "scores": {
                "size_score": round(size_score, 4),
                "heterogeneity_score": round(heterogeneity_score, 4),
                "novelty_score": round(novelty_score, 4),
                "commonality_score": round(commonality_score, 4),
                "split_score": split_score,
                "density": round(density, 4),
            },
            "cluster_terms": unique_cluster_tokens[:12],
        }

    def _scan_codebase_features(self, inventory: Dict[str, Any]) -> List[Dict[str, Any]]:
        groups: Dict[str, List[str]] = {}
        for file_info in inventory.get("files", []):
            groups.setdefault(file_info["group_key"], []).append(file_info["path"])

        features: List[Dict[str, Any]] = []
        for key, files in sorted(groups.items()):
            files = sorted(files)
            if not files:
                continue
            features.append(self._build_feature_record(
                feature_id=f"feat_{_slugify(key)}",
                title=self._feature_title_from_key(key),
                description=self._feature_description_from_group(key, files),
                files=files,
                source="auto",
            ))
        return features

    def _should_include_code_file(self, path: Path) -> bool:
        if not path.is_file():
            return False
        if any(part in IGNORED_DIRS for part in path.parts):
            return False
        if path.name.startswith(".") and path.suffix not in CODE_EXTENSIONS:
            return False
        return path.suffix.lower() in CODE_EXTENSIONS

    def _should_include_inventory_file(self, path: Path) -> bool:
        if not path.is_file():
            return False
        if any(part in IGNORED_DIRS for part in path.parts):
            return False
        suffix = path.suffix.lower()
        if path.name.startswith(".") and suffix not in CODE_EXTENSIONS and suffix not in DOC_EXTENSIONS:
            return False
        try:
            rel_path = path.relative_to(self.project_root)
        except ValueError:
            rel_path = path
        if suffix in CODE_EXTENSIONS:
            if rel_path.parts and rel_path.parts[0] in NON_FEATURE_TOP_LEVEL_DIRS:
                return False
            if rel_path.name in NON_FEATURE_FILENAMES:
                return False
        return suffix in CODE_EXTENSIONS or suffix in DOC_EXTENSIONS

    def _classify_inventory_kind(self, path: Path) -> str:
        suffix = path.suffix.lower()
        if suffix in CODE_EXTENSIONS:
            return "code"
        if suffix in DOC_EXTENSIONS:
            return "doc"
        return "other"

    def _detect_language(self, path: Path) -> str:
        suffix = path.suffix.lower()
        if suffix in {".ts", ".tsx"}:
            return "typescript"
        if suffix in {".js", ".jsx", ".mjs", ".cjs"}:
            return "javascript"
        if suffix == ".py":
            return "python"
        return suffix.lstrip(".") or "unknown"

    def _is_key_file(self, path: Path) -> bool:
        return path.name in KEY_FILE_NAMES

    def _is_test_file(self, path: Path) -> bool:
        parts = {part.lower() for part in path.parts}
        name = path.name.lower()
        return "tests" in parts or name.startswith("test_") or name.endswith(".test.ts") or name.endswith(".spec.ts")

    def _is_config_file(self, path: Path) -> bool:
        name = path.name.lower()
        return name in {"package.json", "pyproject.toml", "tsconfig.json"} or "config" in name or "settings" in name

    def _read_key_file_snippet(self, path: Path, max_lines: int = 24, max_chars: int = 800) -> str:
        if not self._is_key_file(path) and not self._is_config_file(path):
            return ""
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except Exception:
            return ""
        lines = []
        for raw_line in text.splitlines():
            line = raw_line.rstrip()
            if not line:
                continue
            lines.append(line)
            if len(lines) >= max_lines:
                break
        snippet = "\n".join(lines)
        return snippet[:max_chars]

    def _extract_file_semantic_metadata(self, path: Path) -> Dict[str, Any]:
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except Exception:
            return {
                "signatures": [],
                "exported_symbols": [],
                "decorators": [],
                "route_metadata": [],
                "docstring_excerpt": "",
                "comment_excerpt": "",
            }

        suffix = path.suffix.lower()
        if suffix in PYTHON_EXTENSIONS:
            return self._extract_python_semantic_metadata(text)
        if suffix in JS_LIKE_EXTENSIONS:
            metadata = self._extract_js_like_semantic_metadata(text)
            # Augment with tree-sitter decorators if available
            ts_decorators = self._extract_tree_sitter_decorators(text, self._detect_language(path))
            if ts_decorators:
                existing = set(metadata.get("decorators", []))
                for dec in ts_decorators:
                    if dec not in existing:
                        metadata["decorators"].append(dec)
                        existing.add(dec)
                metadata["decorators"] = metadata["decorators"][:12]
            return metadata
        # For other languages with tree-sitter support, extract what we can
        language = self._detect_language(path)
        metadata = {
            "signatures": [],
            "exported_symbols": [],
            "decorators": self._extract_tree_sitter_decorators(text, language),
            "route_metadata": [],
            "docstring_excerpt": self._extract_comment_excerpt(text, comment_prefixes=("#", "//")),
            "comment_excerpt": self._extract_comment_excerpt(text, comment_prefixes=("#", "//")),
        }
        return metadata

    def _extract_python_semantic_metadata(self, text: str, max_items: int = 12) -> Dict[str, Any]:
        metadata = {
            "signatures": [],
            "exported_symbols": [],
            "decorators": [],
            "route_metadata": [],
            "docstring_excerpt": "",
            "comment_excerpt": self._extract_comment_excerpt(text),
        }
        try:
            tree = ast.parse(text)
        except SyntaxError:
            return metadata

        metadata["docstring_excerpt"] = (ast.get_docstring(tree) or "")[:240]
        exported: List[str] = []
        explicit_exports: Optional[List[str]] = None

        for node in tree.body:
            if isinstance(node, ast.ClassDef):
                bases = [self._ast_node_name(base) for base in node.bases if self._ast_node_name(base)]
                base_suffix = f"({', '.join(bases)})" if bases else ""
                metadata["signatures"].append(f"class {node.name}{base_suffix}")
                if not node.name.startswith("_"):
                    exported.append(node.name)
                for decorator in node.decorator_list:
                    name = self._ast_node_name(decorator)
                    if name:
                        metadata["decorators"].append(name)
            elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                prefix = "async def" if isinstance(node, ast.AsyncFunctionDef) else "def"
                args = [arg.arg for arg in node.args.args]
                if node.args.vararg is not None:
                    args.append(f"*{node.args.vararg.arg}")
                if node.args.kwarg is not None:
                    args.append(f"**{node.args.kwarg.arg}")
                metadata["signatures"].append(f"{prefix} {node.name}({', '.join(args)})")
                if not node.name.startswith("_"):
                    exported.append(node.name)
                for decorator in node.decorator_list:
                    decorator_name = self._ast_node_name(decorator)
                    if decorator_name:
                        metadata["decorators"].append(decorator_name)
                    route_meta = self._python_route_metadata_from_decorator(decorator)
                    if route_meta:
                        metadata["route_metadata"].append(route_meta)
            elif isinstance(node, ast.Assign):
                for target in node.targets:
                    if isinstance(target, ast.Name) and target.id == "__all__":
                        explicit_exports = self._extract_python_string_list(node.value)
                    elif isinstance(target, ast.Name) and not target.id.startswith("_"):
                        exported.append(target.id)
            elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name) and not node.target.id.startswith("_"):
                exported.append(node.target.id)
            if len(metadata["signatures"]) >= max_items:
                break

        metadata["exported_symbols"] = (explicit_exports or exported)[:max_items]
        metadata["decorators"] = list(dict.fromkeys(metadata["decorators"]))[:max_items]
        metadata["route_metadata"] = list(dict.fromkeys(metadata["route_metadata"]))[:max_items]
        return metadata

    def _extract_js_like_semantic_metadata(self, text: str, max_items: int = 12) -> Dict[str, Any]:
        metadata = {
            "signatures": [],
            "exported_symbols": [],
            "decorators": [],
            "route_metadata": [],
            "docstring_excerpt": self._extract_js_docblock_excerpt(text),
            "comment_excerpt": self._extract_comment_excerpt(text, comment_prefixes=("//",)),
        }

        signature_patterns = [
            r"export\s+(?:default\s+)?(?:async\s+)?function\s+([A-Za-z_][A-Za-z0-9_]*)\s*\(([^)]*)\)",
            r"(?:async\s+)?function\s+([A-Za-z_][A-Za-z0-9_]*)\s*\(([^)]*)\)",
            r"export\s+class\s+([A-Za-z_][A-Za-z0-9_]*)(?:\s+extends\s+([A-Za-z_][A-Za-z0-9_]*))?",
            r"export\s+const\s+([A-Za-z_][A-Za-z0-9_]*)\s*=\s*\(([^)]*)\)\s*=>",
        ]
        for pattern in signature_patterns:
            for match in re.findall(pattern, text):
                if not isinstance(match, tuple):
                    match = (match,)
                name = match[0]
                extra = match[1] if len(match) > 1 else ""
                if "class" in pattern:
                    signature = f"class {name}" + (f" extends {extra}" if extra else "")
                else:
                    signature = f"function {name}({extra})"
                if signature not in metadata["signatures"]:
                    metadata["signatures"].append(signature)
                if len(metadata["signatures"]) >= max_items:
                    break
            if len(metadata["signatures"]) >= max_items:
                break

        export_patterns = [
            r"export\s+(?:default\s+)?(?:async\s+)?function\s+([A-Za-z_][A-Za-z0-9_]*)",
            r"export\s+class\s+([A-Za-z_][A-Za-z0-9_]*)",
            r"export\s+const\s+([A-Za-z_][A-Za-z0-9_]*)",
            r"export\s+\{\s*([^}]+)\s*\}",
        ]
        for pattern in export_patterns:
            for match in re.findall(pattern, text):
                if "," in match:
                    for item in match.split(","):
                        name = item.strip().split(" as ", 1)[0].strip()
                        if name and name not in metadata["exported_symbols"]:
                            metadata["exported_symbols"].append(name)
                elif match and match not in metadata["exported_symbols"]:
                    metadata["exported_symbols"].append(match)
                if len(metadata["exported_symbols"]) >= max_items:
                    break
            if len(metadata["exported_symbols"]) >= max_items:
                break

        for match in re.findall(r"@([A-Za-z_][A-Za-z0-9_]*)", text):
            if match not in metadata["decorators"]:
                metadata["decorators"].append(match)
            if len(metadata["decorators"]) >= max_items:
                break

        route_patterns = [
            (r"export\s+(?:async\s+)?function\s+(GET|POST|PUT|PATCH|DELETE)\b", "next"),
            (r"@\w+\.(get|post|put|patch|delete)\(\s*['\"]([^'\"]+)['\"]", "decorator"),
            (r"\w+\.(get|post|put|patch|delete)\(\s*['\"]([^'\"]+)['\"]", "router"),
            (r"fetch\(\s*['\"]([^'\"]+)['\"]", "fetch"),
        ]
        for pattern, kind in route_patterns:
            for match in re.findall(pattern, text, flags=re.IGNORECASE):
                if kind == "next":
                    method = match[0] if isinstance(match, tuple) else match
                    route_meta = f"{str(method).upper()} [next-route]"
                elif isinstance(match, tuple):
                    route_meta = f"{match[0].upper()} {match[1]}" if len(match) > 1 else str(match[0])
                else:
                    route_meta = f"FETCH {match}" if kind == "fetch" else str(match)
                if route_meta not in metadata["route_metadata"]:
                    metadata["route_metadata"].append(route_meta)
                if len(metadata["route_metadata"]) >= max_items:
                    break
            if len(metadata["route_metadata"]) >= max_items:
                break

        metadata["signatures"] = metadata["signatures"][:max_items]
        metadata["exported_symbols"] = metadata["exported_symbols"][:max_items]
        metadata["decorators"] = metadata["decorators"][:max_items]
        metadata["route_metadata"] = metadata["route_metadata"][:max_items]
        return metadata

    def _extract_comment_excerpt(
        self,
        text: str,
        max_chars: int = 240,
        comment_prefixes: Tuple[str, ...] = ("#", "//"),
    ) -> str:
        lines: List[str] = []
        for raw_line in text.splitlines():
            line = raw_line.strip()
            if any(line.startswith(prefix) for prefix in comment_prefixes):
                for prefix in comment_prefixes:
                    if line.startswith(prefix):
                        line = line[len(prefix):].strip()
                        break
                if line:
                    lines.append(line)
            elif lines:
                break
            if len(" ".join(lines)) >= max_chars:
                break
        return " ".join(lines)[:max_chars]

    def _extract_js_docblock_excerpt(self, text: str, max_chars: int = 240) -> str:
        match = re.search(r"/\*\*(.*?)\*/", text, flags=re.DOTALL)
        if not match:
            return ""
        lines = []
        for raw_line in match.group(1).splitlines():
            line = raw_line.strip().lstrip("*").strip()
            if line:
                lines.append(line)
            if len(" ".join(lines)) >= max_chars:
                break
        return " ".join(lines)[:max_chars]

    def _ast_node_name(self, node: ast.AST) -> str:
        if isinstance(node, ast.Name):
            return node.id
        if isinstance(node, ast.Attribute):
            parent = self._ast_node_name(node.value)
            return f"{parent}.{node.attr}" if parent else node.attr
        if isinstance(node, ast.Call):
            return self._ast_node_name(node.func)
        return ""

    def _extract_python_string_list(self, node: ast.AST) -> List[str]:
        if not isinstance(node, (ast.List, ast.Tuple)):
            return []
        items: List[str] = []
        for elt in node.elts:
            if isinstance(elt, ast.Constant) and isinstance(elt.value, str):
                items.append(elt.value)
        return items

    def _python_route_metadata_from_decorator(self, decorator: ast.AST) -> str:
        if not isinstance(decorator, ast.Call):
            return ""
        name = self._ast_node_name(decorator.func).lower()
        methods = {
            "get": "GET",
            "post": "POST",
            "put": "PUT",
            "patch": "PATCH",
            "delete": "DELETE",
            "route": "ROUTE",
        }
        method = next((verb for key, verb in methods.items() if name.endswith(f".{key}") or name == key), "")
        if not method:
            return ""
        if not decorator.args:
            return method
        first_arg = decorator.args[0]
        if isinstance(first_arg, ast.Constant) and isinstance(first_arg.value, str):
            return f"{method} {first_arg.value}"
        return method

    def _extract_top_identifiers(
        self,
        path: Path,
        max_items: int = 12,
        structural_symbols: Optional[List[str]] = None,
    ) -> List[str]:
        if structural_symbols:
            identifiers: List[str] = []
            for symbol in structural_symbols:
                name = symbol.split(":", 1)[1] if ":" in symbol else symbol
                name = re.sub(r"\(.*\)$", "", name).strip()
                if name and name not in identifiers:
                    identifiers.append(name)
                if len(identifiers) >= max_items:
                    return identifiers[:max_items]
            if identifiers:
                return identifiers[:max_items]

        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except Exception:
            return []

        suffix = path.suffix.lower()
        if suffix in PYTHON_EXTENSIONS:
            return self._extract_python_identifiers(text, max_items=max_items)
        if suffix in JS_LIKE_EXTENSIONS:
            return self._extract_js_like_identifiers(text, max_items=max_items)
        return []

    def _extract_structural_symbols(self, path: Path, max_items: int = 16) -> List[str]:
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except Exception:
            return []

        suffix = path.suffix.lower()
        if suffix in PYTHON_EXTENSIONS:
            return self._extract_python_structural_symbols(text, max_items=max_items)

        language = self._detect_language(path)
        symbols = self._extract_tree_sitter_symbols(text, language, max_items=max_items)
        if symbols:
            return symbols

        if suffix in JS_LIKE_EXTENSIONS:
            return self._extract_js_like_structural_symbols(text, max_items=max_items)
        return []

    def _get_tree_sitter_parser(self, language: str):
        if language in self._tree_sitter_parser_cache:
            cached = self._tree_sitter_parser_cache[language]
            return None if cached is False else cached

        parser = False
        for module_name in ("tree_sitter_language_pack", "tree_sitter_languages"):
            try:
                module = importlib.import_module(module_name)
            except Exception:
                continue

            get_parser = getattr(module, "get_parser", None)
            if callable(get_parser):
                try:
                    parser = get_parser(language)
                    break
                except Exception:
                    continue

            get_language = getattr(module, "get_language", None)
            if callable(get_language):
                try:
                    language_obj = get_language(language)
                    tree_sitter = importlib.import_module("tree_sitter")
                    parser_obj = tree_sitter.Parser()
                    parser_obj.set_language(language_obj)
                    parser = parser_obj
                    break
                except Exception:
                    continue

        self._tree_sitter_parser_cache[language] = parser
        return None if parser is False else parser

    def _extract_tree_sitter_symbols(self, text: str, language: str, max_items: int = 16) -> List[str]:
        parser = self._get_tree_sitter_parser(language)
        if parser is None:
            return []

        try:
            tree = parser.parse(text.encode("utf-8"))
        except Exception:
            return []

        symbol_kinds = {
            "function_definition": "function",
            "function_declaration": "function",
            "method_definition": "method",
            "class_definition": "class",
            "class_declaration": "class",
            "interface_declaration": "interface",
            "type_alias_declaration": "type",
            "variable_declarator": "variable",
            "lexical_declaration": "variable",
            "assignment": "variable",
            "assignment_expression": "variable",
            "enum_declaration": "enum",
            "struct_definition": "struct",
            "struct_item": "struct",
            "impl_item": "impl",
        }

        # Node types that carry decorators/annotations
        decorator_parent_kinds = {
            "function_definition", "function_declaration", "method_definition",
            "class_definition", "class_declaration",
        }

        symbols: List[str] = []
        seen: set[str] = set()
        stack: List[Tuple[Any, Optional[str]]] = [(tree.root_node, None)]

        while stack and len(symbols) < max_items:
            node, parent_class = stack.pop()
            kind = symbol_kinds.get(node.type)

            if kind:
                name_node = node.child_by_field_name("name")
                if name_node is None and kind == "variable":
                    for child in node.children:
                        candidate = child.child_by_field_name("name") if hasattr(child, "child_by_field_name") else None
                        if candidate is not None:
                            name_node = candidate
                            break
                        if child.type == "identifier":
                            name_node = child
                            break

                if name_node is not None:
                    try:
                        name = text[name_node.start_byte:name_node.end_byte].strip()
                    except Exception:
                        name = ""

                    if name and re.match(r"^[A-Za-z_][A-Za-z0-9_]*$", name):
                        # Build richer symbol with parameters when available
                        symbol_label = kind
                        if kind == "method" and parent_class:
                            symbol_label = f"method"
                            symbol_prefix = f"{parent_class}."
                        else:
                            symbol_prefix = ""

                        # Extract parameters for functions/methods
                        params_node = node.child_by_field_name("parameters")
                        param_str = ""
                        if params_node is not None:
                            try:
                                raw_params = text[params_node.start_byte:params_node.end_byte].strip()
                                # Truncate overly long param lists
                                if len(raw_params) > 80:
                                    raw_params = raw_params[:77] + "..."
                                param_str = raw_params
                            except Exception:
                                pass

                        # Extract return type annotation if present
                        return_type_node = node.child_by_field_name("return_type")
                        return_str = ""
                        if return_type_node is not None:
                            try:
                                return_str = text[return_type_node.start_byte:return_type_node.end_byte].strip()
                                if len(return_str) > 40:
                                    return_str = return_str[:37] + "..."
                            except Exception:
                                pass

                        # Build the symbol string
                        if param_str and kind in ("function", "method"):
                            symbol = f"{symbol_label}:{symbol_prefix}{name}{param_str}"
                            if return_str:
                                symbol += f" -> {return_str}"
                        elif kind == "class":
                            # Check for superclass
                            superclass_node = node.child_by_field_name("superclass") or node.child_by_field_name("superclasses")
                            if superclass_node is None:
                                # Try heritage for JS/TS
                                for child in node.children:
                                    if child.type in ("class_heritage", "superclass", "argument_list"):
                                        superclass_node = child
                                        break
                            super_str = ""
                            if superclass_node is not None:
                                try:
                                    raw_super = text[superclass_node.start_byte:superclass_node.end_byte].strip()
                                    if len(raw_super) > 60:
                                        raw_super = raw_super[:57] + "..."
                                    super_str = raw_super
                                except Exception:
                                    pass
                            symbol = f"{symbol_label}:{name}"
                            if super_str:
                                symbol += f"({super_str})"
                        else:
                            symbol = f"{symbol_label}:{symbol_prefix}{name}"

                        if symbol not in seen:
                            seen.add(symbol)
                            symbols.append(symbol)

                        # Track class name for method hierarchy
                        if kind == "class":
                            parent_class = name

            # Descend into children, passing parent class context
            current_parent = parent_class
            if node.type in ("class_definition", "class_declaration", "class_body"):
                # Keep parent_class for children of class nodes
                name_child = node.child_by_field_name("name")
                if name_child is not None:
                    try:
                        current_parent = text[name_child.start_byte:name_child.end_byte].strip()
                    except Exception:
                        pass

            stack.extend((child, current_parent) for child in reversed(list(node.children)))

        return symbols

    def _extract_tree_sitter_decorators(self, text: str, language: str, max_items: int = 12) -> List[str]:
        """Extract decorator/annotation names using tree-sitter for any supported language."""
        parser = self._get_tree_sitter_parser(language)
        if parser is None:
            return []

        try:
            tree = parser.parse(text.encode("utf-8"))
        except Exception:
            return []

        decorator_node_types = {
            "decorator", "annotation", "attribute",
            "decorator_expression", "attribute_item",
        }
        decorators: List[str] = []
        seen: set[str] = set()
        stack = [tree.root_node]

        while stack and len(decorators) < max_items:
            node = stack.pop()
            if node.type in decorator_node_types:
                try:
                    raw = text[node.start_byte:node.end_byte].strip()
                    # Clean up: remove leading @ or #[ or similar
                    name = raw.lstrip("@#[").rstrip("]")
                    # Extract just the name part before any arguments
                    paren_idx = name.find("(")
                    if paren_idx > 0:
                        name = name[:paren_idx]
                    name = name.strip()
                    if name and len(name) < 80 and name not in seen:
                        seen.add(name)
                        decorators.append(name)
                except Exception:
                    pass
            stack.extend(reversed(list(node.children)))

        return decorators

    def _extract_python_structural_symbols(self, text: str, max_items: int = 16) -> List[str]:
        symbols: List[str] = []
        try:
            tree = ast.parse(text)
        except SyntaxError:
            return symbols

        for node in tree.body:
            if isinstance(node, ast.ClassDef):
                symbols.append(f"class:{node.name}")
            elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                symbols.append(f"function:{node.name}")
            elif isinstance(node, ast.Assign):
                for target in node.targets:
                    if isinstance(target, ast.Name):
                        symbols.append(f"variable:{target.id}")
            elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
                symbols.append(f"variable:{node.target.id}")
            if len(symbols) >= max_items:
                break
        return list(dict.fromkeys(symbols))[:max_items]

    def _extract_js_like_structural_symbols(self, text: str, max_items: int = 16) -> List[str]:
        symbols: List[str] = []
        patterns = [
            ("class", r"\bclass\s+([A-Za-z_][A-Za-z0-9_]*)"),
            ("function", r"\bfunction\s+([A-Za-z_][A-Za-z0-9_]*)"),
            ("function", r"\bexport\s+(?:async\s+)?function\s+([A-Za-z_][A-Za-z0-9_]*)"),
            ("class", r"\bexport\s+class\s+([A-Za-z_][A-Za-z0-9_]*)"),
            ("variable", r"\b(?:const|let|var)\s+([A-Za-z_][A-Za-z0-9_]*)\s*="),
        ]
        for kind, pattern in patterns:
            for match in re.findall(pattern, text):
                symbol = f"{kind}:{match}"
                if symbol not in symbols:
                    symbols.append(symbol)
                if len(symbols) >= max_items:
                    return symbols[:max_items]
        return symbols[:max_items]

    def _extract_python_identifiers(self, text: str, max_items: int = 12) -> List[str]:
        identifiers: List[str] = []
        try:
            tree = ast.parse(text)
        except SyntaxError:
            return identifiers

        for node in tree.body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                identifiers.append(node.name)
            elif isinstance(node, ast.Assign):
                for target in node.targets:
                    if isinstance(target, ast.Name) and re.match(r"[A-Za-z_][A-Za-z0-9_]*$", target.id):
                        identifiers.append(target.id)
            elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
                identifiers.append(node.target.id)

            if len(identifiers) >= max_items:
                break

        return identifiers[:max_items]

    def _extract_js_like_identifiers(self, text: str, max_items: int = 12) -> List[str]:
        identifiers: List[str] = []
        patterns = [
            r"\bclass\s+([A-Za-z_][A-Za-z0-9_]*)",
            r"\bfunction\s+([A-Za-z_][A-Za-z0-9_]*)",
            r"\b(?:const|let|var)\s+([A-Za-z_][A-Za-z0-9_]*)\s*=",
            r"\bexport\s+(?:async\s+)?function\s+([A-Za-z_][A-Za-z0-9_]*)",
            r"\bexport\s+class\s+([A-Za-z_][A-Za-z0-9_]*)",
        ]
        for pattern in patterns:
            for match in re.findall(pattern, text):
                if match not in identifiers:
                    identifiers.append(match)
                if len(identifiers) >= max_items:
                    return identifiers[:max_items]
        return identifiers[:max_items]

    def _extract_file_semantic_tags(self, file_info: Dict[str, Any], max_items: int = 8) -> List[str]:
        raw_terms = self._semantic_terms("\n".join([
            file_info.get("path", ""),
            file_info.get("snippet", ""),
            file_info.get("docstring_excerpt", ""),
            file_info.get("comment_excerpt", ""),
            " ".join(file_info.get("top_identifiers", [])),
            " ".join(file_info.get("structural_symbols", [])),
            " ".join(file_info.get("signatures", [])),
            " ".join(file_info.get("exported_symbols", [])),
            " ".join(file_info.get("decorators", [])),
            " ".join(file_info.get("route_metadata", [])),
        ]))
        tags: List[str] = []
        seen: set[str] = set()
        for term in raw_terms:
            if term in seen:
                continue
            if term in GENERIC_CLUSTER_TERMS or term in CODE_NOISE_TERMS or term in {"py", "ts", "tsx", "js", "jsx"}:
                continue
            if len(term) <= 2 or any(sep in term for sep in ("/", ".")):
                continue
            seen.add(term)
            tags.append(term)
            if len(tags) >= max_items:
                break
        return tags

    def _compute_hub_metrics(
        self,
        file_info: Dict[str, Any],
        path_map: Dict[str, Dict[str, Any]],
    ) -> Dict[str, Any]:
        neighbors = sorted(set(file_info.get("imports", [])) | set(file_info.get("imported_by", [])))
        neighbor_groups = {
            path_map[path]["group_key"]
            for path in neighbors
            if path in path_map and path_map[path].get("group_key")
        }
        cross_domain_import_count = sum(
            1
            for path in neighbors
            if path in path_map and path_map[path].get("group_key") != file_info.get("group_key")
        )
        directory_span = len({str(Path(path).parent) for path in neighbors})
        degree_score = min(1.0, len(neighbors) / 8.0)
        group_span_score = min(1.0, max(0, len(neighbor_groups) - 1) / 4.0)
        directory_span_score = min(1.0, max(0, directory_span - 1) / 5.0)
        entrypoint_boost = 0.18 if file_info.get("is_entrypoint") else 0.0
        filename_boost = 0.12 if Path(file_info.get("path", "")).name.lower() in HUB_CANDIDATE_FILENAMES else 0.0
        hub_score = min(
            1.0,
            (degree_score * 0.42)
            + (group_span_score * 0.28)
            + (directory_span_score * 0.18)
            + entrypoint_boost
            + filename_boost,
        )
        return {
            "hub_score": round(hub_score, 4),
            "cross_domain_import_count": cross_domain_import_count,
            "directory_span": directory_span,
            "is_entrypoint_candidate": bool(file_info.get("is_entrypoint")) or Path(file_info.get("path", "")).name.lower() in HUB_CANDIDATE_FILENAMES,
        }

    def _compute_legacy_score(self, file_info: Dict[str, Any]) -> float:
        terms = set(self._semantic_terms("\n".join([
            file_info.get("path", ""),
            " ".join(file_info.get("top_identifiers", [])),
        ])))
        score = 0.0
        if terms & LEGACY_TERMS:
            score += 0.75
        filename = Path(file_info.get("path", "")).name.lower()
        if any(filename.startswith(f"{term}_") for term in ("old", "legacy", "deprecated", "backup")):
            score += 0.2
        return round(min(1.0, score), 4)

    def _compute_support_score(self, file_info: Dict[str, Any]) -> float:
        score = 0.0
        if file_info.get("is_test"):
            score += 0.85
        if file_info.get("is_config"):
            score += 0.65
        terms = set(self._semantic_terms("\n".join([
            file_info.get("path", ""),
            " ".join(file_info.get("top_identifiers", [])),
        ])))
        if terms & SUPPORT_TERMS:
            score += 0.35
        return round(min(1.0, score), 4)

    def _infer_role_hint(self, file_info: Dict[str, Any]) -> str:
        terms = set(self._semantic_terms("\n".join([
            file_info.get("path", ""),
            " ".join(file_info.get("top_identifiers", [])),
            " ".join(file_info.get("semantic_tags", [])),
        ])))
        if file_info.get("legacy_score", 0.0) >= 0.6:
            return "legacy"
        if file_info.get("is_test"):
            return "test"
        if file_info.get("is_config"):
            return "config"
        if file_info.get("hub_score", 0.0) >= 0.72:
            return "hub"
        if terms & ROLE_SCHEMA_TERMS:
            return "schema"
        if (terms & ROLE_SHARED_INFRA_TERMS) and (
            file_info.get("cross_domain_import_count", 0) >= 2 or file_info.get("hub_score", 0.0) >= 0.45
        ):
            return "shared_infra"
        if file_info.get("is_entrypoint"):
            return "entrypoint"
        return "domain_logic"

    def _extract_file_artifacts(self, file_info: Dict[str, Any], max_items: int = 8) -> List[Dict[str, str]]:
        path = file_info.get("path", "")
        rel_path = Path(path)
        parts = rel_path.parts
        artifacts: List[Dict[str, str]] = []
        seen: set[Tuple[str, str]] = set()

        def add(kind: str, label: str):
            key = (kind, label)
            if not label or key in seen or len(artifacts) >= max_items:
                return
            seen.add(key)
            artifacts.append({"kind": kind, "label": label})

        if file_info.get("is_entrypoint"):
            add("entrypoint", rel_path.stem)

        if "pages" in parts:
            try:
                pages_idx = parts.index("pages")
            except ValueError:
                pages_idx = -1
            if pages_idx >= 0 and pages_idx + 1 < len(parts):
                if parts[pages_idx + 1] == "api":
                    route_label = "/".join(parts[pages_idx + 2:]).rsplit(".", 1)[0]
                    add("route", route_label or rel_path.stem)
                else:
                    page_label = "/".join(parts[pages_idx + 1:]).rsplit(".", 1)[0]
                    add("page", page_label or rel_path.stem)

        if "app" in parts:
            try:
                app_idx = parts.index("app")
            except ValueError:
                app_idx = -1
            if app_idx >= 0 and app_idx + 1 < len(parts):
                remainder = list(parts[app_idx + 1:])
                filename = remainder[-1] if remainder else rel_path.name
                stem = Path(filename).stem.lower()
                if remainder and remainder[0] == "api":
                    route_parts = remainder[1:-1]
                    add("route", "/".join(route_parts) or rel_path.stem)
                    if "webhook" in rel_path.as_posix().lower():
                        add("webhook", "/".join(route_parts) or rel_path.stem)
                elif stem in {"page", "layout"}:
                    page_parts = remainder[:-1]
                    add("page", "/".join(page_parts) or rel_path.parent.name or rel_path.stem)

        if "components" in parts or rel_path.suffix.lower() in {".tsx", ".jsx"}:
            for symbol in file_info.get("structural_symbols", []):
                if symbol.startswith("class:") or symbol.startswith("function:"):
                    name = symbol.split(":", 1)[1]
                    if re.match(r"^[A-Z][A-Za-z0-9_]*$", name):
                        add("component", name)

        for symbol in file_info.get("structural_symbols", []):
            kind, _, name = symbol.partition(":")
            lowered = name.lower()
            if kind in {"class", "interface", "type"} and ("schema" in lowered or "model" in lowered):
                add("schema", name)
            if kind == "class" and (
                lowered.endswith("model")
                or lowered.endswith("settings")
                or lowered.endswith("config")
            ):
                add("model", name)

        if file_info.get("is_test"):
            imports = file_info.get("imports", [])
            if imports:
                add("test_target", Path(imports[0]).stem)
            else:
                add("test_target", rel_path.stem)

        if rel_path.name == "cli.py":
            add("cli_surface", "cli")

        text = ""
        try:
            text = (self.project_root / path).read_text(encoding="utf-8", errors="ignore")
        except Exception:
            text = ""

        lowered_path = rel_path.as_posix().lower()
        lowered_text = text.lower()

        if "middleware" in parts or rel_path.stem.lower() == "middleware":
            add("middleware", rel_path.parent.name if rel_path.parent.name != "." else rel_path.stem)
        if re.search(r"\bexport\s+function\s+middleware\b|\bdef\s+middleware\b", text):
            add("middleware", rel_path.stem)

        if "webhook" in lowered_path:
            add("webhook", rel_path.stem)

        for match in re.findall(r"add_parser\(\s*['\"]([^'\"]+)['\"]", text):
            add("cli_command", match)

        for match in re.findall(r"@\w+\.(?:get|post|put|patch|delete)\(\s*['\"]([^'\"]+)['\"]", text):
            add("route", match)
            if "webhook" in match.lower():
                add("webhook", match)
        for match in re.findall(r"@\w+\.route\(\s*['\"]([^'\"]+)['\"]", text):
            add("route", match)
            if "webhook" in match.lower():
                add("webhook", match)
        for match in re.findall(r"\w+\.(?:get|post|put|patch|delete)\(\s*['\"]([^'\"]+)['\"]", text):
            add("route", match)
            if "webhook" in match.lower():
                add("webhook", match)
        for match in re.findall(r"\w+\.route\(\s*['\"]([^'\"]+)['\"]", text):
            add("route", match)
            if "webhook" in match.lower():
                add("webhook", match)
        for match in re.findall(r"app\.(?:use|middleware)\(\s*['\"]([^'\"]+)['\"]", text):
            add("middleware", match)

        if any(pattern in lowered_text for pattern in ("@app.task", "@shared_task", "schedule.every(", "add_job(", "repeat_every", "crontab(")):
            add("job", rel_path.stem)

        if any(pattern in lowered_text for pattern in ("basemodel", "db.model", "models.model", "sequelize.define(", "new schema(", "schema = z.object", "z.object(")):
            add("schema", rel_path.stem)
        if any(pattern in lowered_text for pattern in ("basesettings", "settingsconfigdict", "configdict", "envschema", "schema = z.object", "z.object(")):
            add("config_schema", rel_path.stem)

        return artifacts

    def _file_domain_seed(self, file_info: Dict[str, Any]) -> Optional[Tuple[str, str]]:
        path = file_info.get("path", "")
        artifact_kinds = {
            artifact.get("kind", "")
            for artifact in file_info.get("artifacts", [])
            if isinstance(artifact, dict) and artifact.get("kind")
        }
        terms = set(self._semantic_terms("\n".join([
            path,
            " ".join(file_info.get("top_identifiers", [])),
            " ".join(file_info.get("semantic_tags", [])),
            " ".join(file_info.get("signatures", [])),
            " ".join(file_info.get("exported_symbols", [])),
            " ".join(file_info.get("route_metadata", [])),
            " ".join(
                f"{artifact.get('kind', '')} {artifact.get('label', '')}"
                for artifact in file_info.get("artifacts", [])
                if isinstance(artifact, dict)
            ),
        ])))
        if file_info.get("is_test"):
            return "testing", "Testing"

        scored: List[Tuple[float, str, str]] = []
        for rule in FEATURE_DOMAIN_RULES:
            artifact_hits = len(artifact_kinds & rule["artifact_kinds"])
            term_hits = len(terms & rule["terms"])
            score = (artifact_hits * 2.5) + term_hits
            if score >= 2.0:
                scored.append((score, rule["tag"], rule["title"]))

        if not scored:
            return None
        scored.sort(key=lambda item: (-item[0], item[1]))
        _, tag, title = scored[0]
        return tag, title

    def _derive_artifact_feature_candidates(self, files: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        grouped: Dict[str, Dict[str, Any]] = {}
        for file_info in files:
            if file_info.get("role_hint") in {"hub", "shared_infra", "config"}:
                continue
            seed = self._file_domain_seed(file_info)
            if seed is None:
                continue
            tag, title = seed
            group = grouped.setdefault(tag, {
                "domain_tag": tag,
                "title": title,
                "files": [],
                "artifact_kinds": set(),
                "signals": set(),
            })
            group["files"].append(file_info["path"])
            group["artifact_kinds"].update(
                artifact.get("kind")
                for artifact in file_info.get("artifacts", [])
                if isinstance(artifact, dict) and artifact.get("kind")
            )
            group["signals"].update(file_info.get("semantic_tags", [])[:6])

        candidates: List[Dict[str, Any]] = []
        for group in grouped.values():
            files_sorted = sorted(set(group["files"]))
            if (
                len(files_sorted) < 2
                and group["domain_tag"] == "testing"
                and not any(kind in {"test_target", "route", "page", "cli_command"} for kind in group["artifact_kinds"])
            ):
                continue
            candidates.append({
                "domain_tag": group["domain_tag"],
                "title": group["title"],
                "files": files_sorted,
                "artifact_kinds": sorted(kind for kind in group["artifact_kinds"] if kind),
                "signals": sorted(group["signals"])[:10],
                "suggested_file_boundaries": self._candidate_boundaries(files_sorted),
            })

        candidates.sort(key=lambda item: (-len(item["files"]), item["title"]))
        return candidates

    def _build_document_item(self, path: Path, rel: str) -> Dict[str, Any]:
        content = self._read_document_excerpt(path)
        title = self._extract_document_title(content, Path(rel).stem.replace("_", " ").replace("-", " ").title())
        doc_kind = self._classify_document_kind(rel, title)
        title_terms = self._filtered_semantic_terms(
            "\n".join([rel, title]),
            extra_noise=DOC_NOISE_TERMS,
        )
        semantic_terms = self._filtered_semantic_terms(
            "\n".join([title, content[:2000]]),
            extra_noise=DOC_NOISE_TERMS,
            drop_generic_cluster_terms=True,
        )
        return {
            "path": rel,
            "kind": "doc",
            "language": path.suffix.lower().lstrip(".") or "text",
            "group_key": self._feature_group_key(Path(rel)),
            "title": title,
            "doc_kind": doc_kind,
            "content": content,
            "snippet": content[:800],
            "token_count": len(content.split()),
            "referenced_paths": [],
            "title_terms": title_terms,
            "semantic_terms": semantic_terms,
            "project_scope_signals": self._project_scope_doc_signals(rel, title),
        }

    def _read_document_excerpt(self, path: Path, max_chars: int = 3000) -> str:
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except Exception:
            return ""
        return text[:max_chars]

    def _extract_document_title(self, text: str, fallback: str) -> str:
        for line in text.splitlines():
            stripped = line.strip()
            if not stripped:
                continue
            if stripped.startswith("#"):
                return stripped.lstrip("#").strip() or fallback
            return stripped[:120]
        return fallback

    def _extract_document_code_references(self, text: str, known_paths: List[str]) -> List[str]:
        references: List[str] = []
        if not text:
            return references
        for candidate in known_paths:
            if candidate in text:
                references.append(candidate)
        return references

    def _classify_document_kind(self, rel_path: str, title: str) -> str:
        slug = _slugify(f"{rel_path} {title}")
        if "format" in slug:
            return "format"
        if "spec" in slug or "contract" in slug:
            return "spec"
        if "roadmap" in slug or "plan" in slug or "milestone" in slug:
            return "plan"
        if "vision" in slug:
            return "vision"
        if "readme" in slug:
            return "readme"
        if "guide" in slug or "how_to" in slug:
            return "guide"
        if "architecture" in slug or "design" in slug:
            return "architecture"
        return "doc"

    def _project_scope_doc_signals(self, rel_path: str, title: str) -> List[str]:
        path = Path(rel_path)
        stem_slug = _slugify(path.stem)
        title_slug = _slugify(title)
        signals: List[str] = []
        is_root_doc = len(path.parts) <= 2

        if is_root_doc and (stem_slug in PROJECT_SCOPE_DOC_STEMS or title_slug in PROJECT_SCOPE_DOC_STEMS):
            signals.append("project_scope_doc_name")
        if any(phrase in stem_slug or phrase in title_slug for phrase in PROJECT_SCOPE_DOC_PHRASES):
            signals.append("project_scope_doc_phrase")
        if is_root_doc and stem_slug in {"architecture", "overview"}:
            signals.append("project_scope_root_overview")
        return signals

    def _python_module_aliases(self, rel_path: str) -> List[str]:
        path = Path(rel_path)
        if path.suffix.lower() != ".py":
            return []

        stem_parts = list(path.with_suffix("").parts)
        aliases = {".".join(stem_parts)}
        if path.name == "__init__.py":
            aliases.add(".".join(path.parent.parts))
        for idx, part in enumerate(stem_parts):
            if part in {"src", "packages", "apps", "backend"} and idx + 1 < len(stem_parts):
                aliases.add(".".join(stem_parts[idx + 1:]))
        return [alias for alias in aliases if alias]

    def _extract_local_imports(
        self,
        path: Path,
        rel_path: str,
        path_map: Dict[str, Dict[str, Any]],
        python_module_map: Dict[str, str],
    ) -> List[str]:
        try:
            content = path.read_text(encoding="utf-8", errors="ignore")
        except Exception:
            return []

        if path.suffix.lower() in PYTHON_EXTENSIONS:
            return self._extract_python_imports(content, rel_path, python_module_map)
        if path.suffix.lower() in JS_LIKE_EXTENSIONS:
            return self._extract_js_like_imports(content, rel_path, path_map)
        return []

    def _extract_python_imports(self, content: str, rel_path: str, python_module_map: Dict[str, str]) -> List[str]:
        imports: List[str] = []
        current_parts = list(Path(rel_path).with_suffix("").parts)
        if current_parts and current_parts[-1] == "__init__":
            current_parts = current_parts[:-1]

        for spec in re.findall(r"^\s*from\s+([.\w]+)\s+import\s+", content, re.MULTILINE):
            resolved = self._resolve_python_module(spec, current_parts, python_module_map)
            if resolved:
                imports.append(resolved)
        for spec in re.findall(r"^\s*import\s+([\w\.]+)", content, re.MULTILINE):
            module_name = spec.split(",")[0].strip()
            resolved = self._resolve_python_module(module_name, current_parts, python_module_map)
            if resolved:
                imports.append(resolved)
        return sorted(set(imports))

    def _resolve_python_module(
        self,
        spec: str,
        current_parts: List[str],
        python_module_map: Dict[str, str],
    ) -> Optional[str]:
        if spec.startswith("."):
            dots = len(spec) - len(spec.lstrip("."))
            remainder = spec.lstrip(".")
            base = current_parts[:-dots] if dots <= len(current_parts) else []
            module_name = ".".join(base + ([remainder] if remainder else []))
        else:
            module_name = spec
        return python_module_map.get(module_name)

    def _extract_js_like_imports(self, content: str, rel_path: str, path_map: Dict[str, Dict[str, Any]]) -> List[str]:
        imports: List[str] = []
        patterns = [
            r"""import\s+(?:.+?\s+from\s+)?["']([^"']+)["']""",
            r"""export\s+.+?\s+from\s+["']([^"']+)["']""",
            r"""require\(\s*["']([^"']+)["']\s*\)""",
            r"""import\(\s*["']([^"']+)["']\s*\)""",
        ]
        for pattern in patterns:
            for spec in re.findall(pattern, content):
                if not spec.startswith("."):
                    continue
                resolved = self._resolve_js_import(spec, rel_path, path_map)
                if resolved:
                    imports.append(resolved)
        return sorted(set(imports))

    def _resolve_js_import(self, spec: str, rel_path: str, path_map: Dict[str, Dict[str, Any]]) -> Optional[str]:
        base = (self.project_root / rel_path).parent
        candidate = (base / spec).resolve()
        suffixes = ["", ".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs", ".py"]

        for suffix in suffixes:
            path = Path(str(candidate) + suffix)
            try:
                rel = path.relative_to(self.project_root).as_posix()
            except ValueError:
                rel = None
            if rel and rel in path_map:
                return rel

        for suffix in [".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs"]:
            path = candidate / f"index{suffix}"
            try:
                rel = path.relative_to(self.project_root).as_posix()
            except ValueError:
                rel = None
            if rel and rel in path_map:
                return rel
        return None

    def _feature_group_key(self, rel_path: Path) -> str:
        parts = rel_path.parts
        if len(parts) >= 2 and parts[0] in {"packages", "apps", "services", "libs"}:
            return "/".join(parts[:2])
        if len(parts) >= 2 and parts[0] in {"src", "tests", "backend"}:
            return "/".join(parts[:2])
        return parts[0] if parts else "root"

    def _feature_title_from_key(self, key: str) -> str:
        return key.replace("/", " ").replace("_", " ").replace("-", " ").title()

    def _feature_description_from_group(self, key: str, files: List[str]) -> str:
        file_count = len(files)
        sample = ", ".join(Path(path).name for path in files[:3])
        noun = "file" if file_count == 1 else "files"
        return f"Code grouped under {key} with {file_count} {noun}, including {sample}."

    def _cluster_inventory_with_llm(
        self,
        inventory: Dict[str, Any],
        llm_client = None,
        model: Optional[str] = None,
        project_context: Optional[str] = None,
        missing_feature_hints: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        from ..runtime.config import Config
        from ..summarization.summarizer import SessionSummarizer

        cfg = Config()
        resolved_model = model or cfg.get_default_model()
        client = llm_client or create_llm_client_for_model(resolved_model)
        summarizer = SessionSummarizer(llm_client=client, model=resolved_model)

        readme_content = self._read_readme_for_clustering()

        system_prompt = """You are initializing a `.devproject` file for an existing repository.

Identify the repository's canonical project features for `.devproject` initialization.

A `.devproject` feature is a stable, durable project work area, not just a connected graph cluster.

Optimize for:
- stable feature identities
- future session-to-feature matching by file overlap
- clean file ownership boundaries
- preserving real internal project domains when they exist

## Domain discovery priority

1. **README content is the primary domain guide.** If a `readme_content` field is provided, read it carefully. The README describes what the project does and often names its main subsystems, capabilities, or architectural layers. Use those as your preferred feature candidates and map codebase files to them when the inventory supports it.

2. **Tree-sitter evidence discovers domains the README does not mention.** The scanned inventory includes structural symbols, signatures, exported symbols, decorators, route metadata, semantic tags, role hints, and import relationships. Use these to identify additional features that the README omits — such as legacy code, testing infrastructure, UI surfaces, export utilities, or internal tooling.

3. **If no README is provided or it is too thin to identify domains,** fall back entirely to the codebase inventory and tree-sitter evidence for domain discovery.

The balance is: README sets the domain vocabulary, tree-sitter evidence does the file assignment and fills gaps.

Use the scanned inventory, including paths, imports, entrypoints, top identifiers, structural symbols, signatures, exported symbols, decorators, route metadata, short docstrings/comments, semantic tags, role hints, hub scores, legacy/support signals, import clusters/subclusters, artifact_candidates, and documents (with titles, excerpts, classification, and referenced code paths).

Treat artifact_candidates as strong seed groups. Prefer refining, merging, or lightly splitting them over inventing unrelated top-level features from scratch.

Rules:
- Coupling is not identity. Import edges indicate dependency, not feature membership.
- Consumption is not collaboration. If one area uses another area's API, they are often different features.
- Hub/orchestrator files should reduce false grouping, not glue features together.
- Shared infrastructure should usually stay out of feature membership unless it is itself a durable project work area.
- Treat files marked as legacy or support/secondary surfaces as weaker feature candidates unless the inventory clearly shows they are recurring standalone work areas.
- Prefer features whose files can be summarized by compact boundaries such as directories, globs, or small exact-file sets.
- Avoid generic catch-all titles such as Core, Runtime, Engine, Backend, System, Misc, Utilities, Source, or Helpers unless the inventory truly supports no cleaner split.
- When repository documents such as format specifications, design docs, or architecture guides describe a subsystem, prefer that document's vocabulary and naming when titling the feature whose files it references.

Anti-collapse rules:
- Do not merge a large runtime/core/backend area into one feature when the inventory shows multiple distinct internal domains.
- If a broad area contains different domain vocabularies, schemas, responsibilities, entrypoints, or likely co-change patterns, split it into separate features even when tightly connected by imports.
- A dense connected component is not a valid reason to keep a feature broad.
- Do not collapse distinct internal domains merely because they share a package, orchestrator files, or runtime.
- If artifact_candidates already isolate distinct groups such as routes, UI surfaces, or testing, preserve those boundaries unless they are clearly unstable.

Granularity:
- Your default task is to find real internal feature boundaries, not to minimize the number of features.
- Prefer multiple coherent features inside dense runtime/core areas when they could plausibly be worked on independently.
- Keep areas merged only when splitting would create arbitrary, unstable, or heavily overlapping boundaries.

Return valid JSON only:
{
  "project": {
    "name": "string",
    "description": "1-3 sentence high-level summary of what the repository is and does"
  },
  "features": [
    {
      "title": "short stable project feature name",
      "description": "1-2 sentences describing this durable work area",
      "files": ["path/a.py", "path/b.ts"],
      "suggested_file_boundaries": ["src/area/**", "packages/x/y.py"],
      "hub_files_excluded": ["path/to/hub.py"],
      "shared_infra_dependencies": ["path/to/shared.py"],
      "rationale": "why this is a stable canonical feature and why these files belong together"
    }
  ],
  "hub_files": ["path/to/orchestrator.py"],
  "shared_infrastructure": ["path/to/shared.py"],
  "unassigned": ["path/to/ambiguous.py"],
  "notes": ["optional short notes"]
}
"""

        llm_input = {
            "readme_content": readme_content or "",
            "project_context": project_context or inventory.get("project_description") or "",
            "missing_feature_hints": [hint for hint in (missing_feature_hints or []) if isinstance(hint, str) and hint.strip()],
            "inventory": self._truncate_inventory_for_llm(inventory),
        }
        serialized = json.dumps(llm_input, indent=2, ensure_ascii=False)

        # If still too large, reduce further with compact formatting
        if len(serialized) > 180_000:
            llm_input["inventory"] = self._truncate_inventory_for_llm(inventory, aggressive=True)
            serialized = json.dumps(llm_input, ensure_ascii=False)

        response = summarizer._call_json_llm(
            system_prompt,
            serialized,
            max_tokens=self._max_output_tokens_for_model(resolved_model),
        )
        normalized = self._normalize_llm_cluster_output(response, inventory)
        normalized["features"] = self._refine_features_with_artifact_candidates(
            normalized.get("features", []),
            inventory,
        )
        return normalized

    def _build_file_classification_input(self, inventory: Dict[str, Any]) -> Dict[str, Any]:
        cluster_refs: Dict[str, Dict[str, str]] = {}
        for cluster in inventory.get("import_clusters", []):
            cluster_id = cluster.get("cluster_id", "")
            for path in cluster.get("files", []):
                cluster_refs.setdefault(path, {})["cluster_id"] = cluster_id
            for subcluster in cluster.get("subclusters", []):
                subcluster_id = subcluster.get("subcluster_id", "")
                for path in subcluster.get("files", []):
                    cluster_refs.setdefault(path, {})["subcluster_id"] = subcluster_id

        files = []
        for item in inventory.get("files", []):
            refs = cluster_refs.get(item["path"], {})
            files.append({
                "path": item["path"],
                "language": item.get("language"),
                "group_key": item.get("group_key"),
                "imports": item.get("imports", []),
                "imported_by": item.get("imported_by", []),
                "is_entrypoint": item.get("is_entrypoint", False),
                "is_test": item.get("is_test", False),
                "is_config": item.get("is_config", False),
                "top_identifiers": item.get("top_identifiers", []),
                "semantic_tags": item.get("semantic_tags", []),
                "role_hint": item.get("role_hint"),
                "hub_score": item.get("hub_score", 0.0),
                "cross_domain_import_count": item.get("cross_domain_import_count", 0),
                "directory_span": item.get("directory_span", 0),
                "support_score": item.get("support_score", 0.0),
                "legacy_score": item.get("legacy_score", 0.0),
                "cluster_id": refs.get("cluster_id"),
                "subcluster_id": refs.get("subcluster_id"),
            })
        return {
            "project_name": inventory.get("project_name"),
            "project_description": inventory.get("project_description"),
            "files": files,
            "import_clusters": inventory.get("import_clusters", []),
        }

    def _default_bucket_for_file(self, file_info: Dict[str, Any]) -> str:
        role_hint = file_info.get("role_hint")
        if role_hint == "hub":
            return "hub"
        if role_hint in {"shared_infra", "config"}:
            return "shared_infrastructure"
        if role_hint == "legacy":
            return "legacy"
        if role_hint == "test" or file_info.get("support_score", 0.0) >= 0.75:
            return "support"
        return "feature"

    def _truncate_inventory_for_llm(
        self,
        inventory: Dict[str, Any],
        aggressive: bool = False,
        max_files: int = 400,
    ) -> Dict[str, Any]:
        """Produce a lighter inventory for LLM consumption.

        Prioritizes entrypoints, high hub-score files, and files with rich
        semantic signals.  Drops low-signal fields when *aggressive* is set.
        """
        files = list(inventory.get("files", []))

        # Score each file by information value to the LLM
        def file_priority(f: Dict[str, Any]) -> float:
            score = 0.0
            if f.get("is_entrypoint"):
                score += 5.0
            score += f.get("hub_score", 0.0) * 3.0
            score += len(f.get("structural_symbols", [])) * 0.3
            score += len(f.get("route_metadata", [])) * 0.5
            score += len(f.get("artifacts", [])) * 0.4
            if f.get("snippet"):
                score += 0.5
            if f.get("role_hint") == "domain_logic":
                score += 0.3
            if f.get("is_test"):
                score -= 0.5
            if f.get("legacy_score", 0.0) >= 0.5:
                score -= 0.5
            return score

        if len(files) > max_files:
            files.sort(key=file_priority, reverse=True)
            files = files[:max_files]

        # Strip heavy fields to reduce token count
        truncated_files = []
        for item in files:
            entry: Dict[str, Any] = {
                "path": item["path"],
                "language": item.get("language"),
                "group_key": item.get("group_key"),
                "is_entrypoint": item.get("is_entrypoint", False),
                "is_test": item.get("is_test", False),
                "role_hint": item.get("role_hint"),
                "hub_score": item.get("hub_score", 0.0),
                "semantic_tags": item.get("semantic_tags", [])[:6],
                "top_identifiers": item.get("top_identifiers", [])[:8],
                "structural_symbols": item.get("structural_symbols", [])[:10],
                "imports": item.get("imports", []),
                "imported_by": item.get("imported_by", []),
            }
            if not aggressive:
                entry["signatures"] = item.get("signatures", [])[:8]
                entry["exported_symbols"] = item.get("exported_symbols", [])[:8]
                entry["decorators"] = item.get("decorators", [])[:6]
                entry["route_metadata"] = item.get("route_metadata", [])[:6]
                entry["snippet"] = (item.get("snippet") or "")[:400]
                entry["docstring_excerpt"] = (item.get("docstring_excerpt") or "")[:160]
                entry["artifacts"] = item.get("artifacts", [])[:6]
                entry["support_score"] = item.get("support_score", 0.0)
                entry["legacy_score"] = item.get("legacy_score", 0.0)
            truncated_files.append(entry)

        # Truncate cluster data too
        clusters = inventory.get("import_clusters", [])
        truncated_clusters = []
        for cluster in clusters:
            c: Dict[str, Any] = {
                "cluster_id": cluster.get("cluster_id"),
                "size": cluster.get("size"),
                "files": cluster.get("files", []),
                "group_keys": cluster.get("group_keys", []),
                "entrypoints": cluster.get("entrypoints", []),
                "granularity_hint": cluster.get("granularity_hint"),
                "cluster_terms": cluster.get("cluster_terms", [])[:8],
            }
            if not aggressive:
                c["subclusters"] = [
                    {
                        "subcluster_id": sc.get("subcluster_id"),
                        "size": sc.get("size"),
                        "files": sc.get("files", []),
                        "group_keys": sc.get("group_keys", []),
                        "granularity_hint": sc.get("granularity_hint"),
                        "cluster_terms": sc.get("cluster_terms", [])[:6],
                    }
                    for sc in cluster.get("subclusters", [])
                ]
            truncated_clusters.append(c)

        # Include feature-scope documents so the LLM can use spec/format
        # vocabulary when naming features.
        doc_kind_priority = {"format": 0, "spec": 0, "architecture": 1}
        sorted_docs = sorted(
            inventory.get("documents", []),
            key=lambda d: (doc_kind_priority.get(d.get("doc_kind", "doc"), 2), d.get("path", "")),
        )
        excerpt_limit = 200 if aggressive else 400
        max_docs = 20 if aggressive else 40
        truncated_docs: List[Dict[str, Any]] = []
        for doc in sorted_docs:
            if doc.get("project_scope_signals"):
                continue
            content = (doc.get("content", "") or "").strip()
            if not content:
                continue
            truncated_docs.append({
                "path": doc["path"],
                "title": doc.get("title", ""),
                "doc_kind": doc.get("doc_kind", "doc"),
                "excerpt": content[:excerpt_limit],
                "referenced_paths": doc.get("referenced_paths", [])[:10],
            })
            if len(truncated_docs) >= max_docs:
                break

        return {
            "project_root": inventory.get("project_root"),
            "project_name": inventory.get("project_name"),
            "project_description": inventory.get("project_description"),
            "files": sorted(truncated_files, key=lambda f: f["path"]),
            "documents": truncated_docs,
            "import_clusters": truncated_clusters,
            "artifact_candidates": inventory.get("artifact_candidates", []),
            "total_files_in_repo": len(inventory.get("files", [])),
            "files_truncated": len(files) < len(inventory.get("files", [])),
        }

    @staticmethod
    def _max_output_tokens_for_model(model: str) -> int:
        """Return the max output token limit for a given model.

        Defaults conservatively when the model is unrecognized so the API
        itself becomes the ceiling rather than a hardcoded guess.
        """
        model_lower = model.lower() if model else ""

        # --- Anthropic models ---
        if "opus" in model_lower:
            # Opus 4.6: 128k output, Opus 4.5 and earlier: 32k
            if any(tag in model_lower for tag in ("4.6", "4-6")):
                return 128000
            return 32000
        if "sonnet" in model_lower:
            # Sonnet 4.6 / 4.5 / 4: 64k output
            if any(tag in model_lower for tag in ("4.6", "4-6", "4.5", "4-5")):
                return 64000
            # Sonnet 4 base (e.g. claude-sonnet-4-20250514)
            if "sonnet-4" in model_lower:
                return 64000
            # Sonnet 3.7: 16k output
            if any(tag in model_lower for tag in ("3.7", "3-7")):
                return 16000
            # Fallback for future Sonnet versions
            return 64000
        if "haiku" in model_lower:
            # Haiku 4.5: 8k
            return 8192

        # --- OpenAI models ---
        # GPT-5.x family (5, 5.2, 5.3, 5.4, mini, nano, pro): 128k output
        if "gpt-5" in model_lower:
            return 128000
        # GPT-4.1: 32k output
        if "gpt-4.1" in model_lower or "gpt-4-1" in model_lower:
            return 32768
        # GPT-4o: 16k output
        if "gpt-4o" in model_lower:
            return 16384
        # GPT-4 / GPT-4 Turbo: 4k output
        if "gpt-4" in model_lower:
            return 4096
        # o-series reasoning models (o1, o3, o4): 100k output
        if model_lower.startswith("o1") or model_lower.startswith("o3") or model_lower.startswith("o4"):
            return 100000

        # --- Moonshot / Kimi models ---
        # Kimi K2 / K2.5: 128k context, practical output cap ~16k
        if "kimi" in model_lower or "moonshot" in model_lower:
            return 16000

        # --- DeepSeek models ---
        if "deepseek" in model_lower:
            return 16000

        # --- Google Gemini models ---
        if "gemini" in model_lower:
            # Gemini 3.x (3.1 Pro, 3.0 Pro, Flash-Lite): 65k output
            if "3" in model_lower:
                return 65536
            # Gemini 2.5 Pro/Flash: 65k output
            if "2.5" in model_lower or "2-5" in model_lower:
                return 65536
            return 8192

        # Unknown model — set high and let the API enforce its own limit
        return 32000

    def _default_domain_tag_for_file(self, file_info: Dict[str, Any], bucket: str) -> str:
        path = file_info.get("path", "")
        group_key = file_info.get("group_key", "")
        tags = file_info.get("semantic_tags", [])
        if bucket == "support":
            if file_info.get("is_test"):
                return "testing_benchmarks"
            if "benchmark" in tags or "benchmarks" in tags:
                return "testing_benchmarks"
        if bucket == "legacy":
            return _slugify(group_key or Path(path).parent.as_posix() or Path(path).stem)
        return _slugify(group_key or Path(path).parent.as_posix() or Path(path).stem)

    def _group_file_classification_labels(self, payload: Dict[str, Any], inventory: Dict[str, Any]) -> Dict[str, Any]:
        files_by_path = {item["path"]: item for item in inventory.get("files", [])}
        labels_by_path = {
            item.get("path"): item
            for item in payload.get("labels", []) or []
            if isinstance(item, dict) and isinstance(item.get("path"), str)
        }
        hub_files = {
            path for path in payload.get("hub_files", []) or []
            if isinstance(path, str) and path in files_by_path
        }
        shared_infrastructure = {
            path for path in payload.get("shared_infrastructure", []) or []
            if isinstance(path, str) and path in files_by_path
        }
        unassigned = {
            path for path in payload.get("unassigned", []) or []
            if isinstance(path, str) and path in files_by_path
        }

        candidate_groups: Dict[Tuple[str, str], Dict[str, Any]] = {}
        for path, file_info in files_by_path.items():
            label = labels_by_path.get(path, {})
            bucket = label.get("bucket")
            if bucket not in {"feature", "shared_infrastructure", "hub", "support", "legacy", "unassigned"}:
                bucket = self._default_bucket_for_file(file_info)

            if path in hub_files:
                bucket = "hub"
            elif path in shared_infrastructure:
                bucket = "shared_infrastructure"
            elif path in unassigned:
                bucket = "unassigned"

            if bucket == "hub":
                hub_files.add(path)
                continue
            if bucket == "shared_infrastructure":
                shared_infrastructure.add(path)
                continue
            if bucket == "unassigned":
                unassigned.add(path)
                continue

            domain_tag = _slugify(label.get("domain_tag") or self._default_domain_tag_for_file(file_info, bucket))
            domain_title = (label.get("domain_title") or self._feature_title_from_key(domain_tag)).strip()
            key = (bucket, domain_tag)
            group = candidate_groups.setdefault(key, {
                "candidate_id": f"cand_{bucket}_{domain_tag}",
                "candidate_type": bucket,
                "domain_tag": domain_tag,
                "domain_title": domain_title,
                "files": [],
                "group_keys": set(),
                "role_hints": set(),
                "semantic_tags": set(),
                "top_identifiers": set(),
                "boundary_candidates": set(),
                "average_hub_score": 0.0,
                "average_support_score": 0.0,
                "average_legacy_score": 0.0,
            })
            group["files"].append(path)
            group["group_keys"].add(file_info.get("group_key"))
            if file_info.get("role_hint"):
                group["role_hints"].add(file_info.get("role_hint"))
            group["semantic_tags"].update(file_info.get("semantic_tags", []))
            group["top_identifiers"].update(file_info.get("top_identifiers", []))
            group["boundary_candidates"].update(self._candidate_boundaries([path]))
            group["average_hub_score"] += file_info.get("hub_score", 0.0)
            group["average_support_score"] += file_info.get("support_score", 0.0)
            group["average_legacy_score"] += file_info.get("legacy_score", 0.0)

        normalized_groups: List[Dict[str, Any]] = []
        for group in candidate_groups.values():
            file_count = max(1, len(group["files"]))
            normalized_groups.append({
                "candidate_id": group["candidate_id"],
                "candidate_type": group["candidate_type"],
                "domain_tag": group["domain_tag"],
                "domain_title": group["domain_title"],
                "files": sorted(set(group["files"])),
                "group_keys": sorted(value for value in group["group_keys"] if value),
                "role_hints": sorted(group["role_hints"]),
                "semantic_tags": sorted(group["semantic_tags"])[:12],
                "top_identifiers": sorted(group["top_identifiers"])[:16],
                "suggested_file_boundaries": sorted(group["boundary_candidates"]),
                "average_hub_score": round(group["average_hub_score"] / file_count, 4),
                "average_support_score": round(group["average_support_score"] / file_count, 4),
                "average_legacy_score": round(group["average_legacy_score"] / file_count, 4),
            })

        normalized_groups.sort(key=lambda item: (-len(item["files"]), item["domain_title"]))
        project = payload.get("project", {}) or {}
        return {
            "project": {
                "name": project.get("name") or inventory.get("project_name") or self.project_root.name,
                "description": project.get("description") or inventory.get("project_description") or f"Project dashboard for {self.project_root.name}",
            },
            "candidate_groups": normalized_groups,
            "hub_files": sorted(hub_files),
            "shared_infrastructure": sorted(shared_infrastructure),
            "unassigned": sorted(unassigned),
            "notes": payload.get("notes", []) or [],
        }

    def _build_feature_consolidation_input(
        self,
        classified: Dict[str, Any],
        inventory: Dict[str, Any],
    ) -> Dict[str, Any]:
        return {
            "project": classified.get("project", {}),
            "candidate_groups": classified.get("candidate_groups", []),
            "hub_files": classified.get("hub_files", []),
            "shared_infrastructure": classified.get("shared_infrastructure", []),
            "unassigned": classified.get("unassigned", []),
            "notes": classified.get("notes", []),
            "import_clusters": inventory.get("import_clusters", []),
        }

    def _normalize_llm_cluster_output(
        self,
        payload: Dict[str, Any],
        inventory: Dict[str, Any],
        default_hub_files: Optional[List[str]] = None,
        default_shared_infrastructure: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        known_files = {item["path"] for item in inventory.get("files", [])}
        remaining = set(known_files)
        features: List[Dict[str, Any]] = []
        hub_files = sorted({
            path for path in ((default_hub_files or []) + (payload.get("hub_files", []) or []))
            if isinstance(path, str) and path in known_files
        })
        shared_infrastructure = sorted({
            path for path in ((default_shared_infrastructure or []) + (payload.get("shared_infrastructure", []) or []))
            if isinstance(path, str) and path in known_files
        })
        unassigned = sorted({
            path for path in (payload.get("unassigned", []) or [])
            if isinstance(path, str) and path in known_files
        })
        non_feature_files = set(hub_files) | set(shared_infrastructure) | set(unassigned)

        raw_features = payload.get("features", [])

        for raw_feature in raw_features or []:
            files = [
                path for path in raw_feature.get("files", [])
                if (
                    isinstance(path, str)
                    and path in known_files
                    and path not in non_feature_files
                    and self._should_expose_file_in_feature(path)
                )
            ]
            if not files:
                continue

            for path in files:
                remaining.discard(path)

            title = (raw_feature.get("title") or self._feature_title_from_key(Path(files[0]).parent.as_posix())).strip()
            description = (raw_feature.get("description") or self._feature_description_from_group(title, files)).strip()
            feature_id = f"feat_{_slugify(title)}"
            suggested_boundaries = raw_feature.get("suggested_file_boundaries", [])
            if not suggested_boundaries:
                suggested_boundaries = raw_feature.get("file_boundaries", [])
            if suggested_boundaries:
                boundary_feature = {"file_boundaries": suggested_boundaries}
                boundary_matched = [
                    path for path in files
                    if self._path_matches_feature_boundaries(path, boundary_feature)
                ]
                if boundary_matched:
                    files = boundary_matched
                    if not files:
                        continue

            features.append(self._build_feature_record(
                feature_id=feature_id,
                title=title,
                description=description,
                files=sorted(set(files)),
                file_boundaries=sorted(set(suggested_boundaries)) or self._candidate_boundaries(files),
                source="auto",
                status=raw_feature.get("status", "in-progress"),
            ))

        remaining -= non_feature_files
        self._attach_remaining_support_files(remaining, inventory, features)

        if remaining:
            fallback_inventory = {
                "files": [
                    item for item in inventory.get("files", [])
                    if item["path"] in remaining and self._should_backfill_uncategorized(item["path"])
                ]
            }
            features.extend(self._scan_codebase_features(fallback_inventory))

        project = payload.get("project", {}) or {}
        return {
            "project": {
                "name": project.get("name") or inventory.get("project_name") or self.project_root.name,
                "description": project.get("description") or inventory.get("project_description") or f"Project dashboard for {self.project_root.name}",
            },
            "features": self._deduplicate_features(features),
            "hub_files": hub_files,
            "shared_infrastructure": shared_infrastructure,
            "unassigned": unassigned,
        }

    def _refine_features_with_artifact_candidates(
        self,
        features: List[Dict[str, Any]],
        inventory: Dict[str, Any],
    ) -> List[Dict[str, Any]]:
        candidates = inventory.get("artifact_candidates", []) or []
        if not candidates:
            return features

        candidate_map = {
            candidate["domain_tag"]: {
                "title": candidate["title"],
                "files": set(candidate.get("files", [])),
                "boundaries": candidate.get("suggested_file_boundaries", []),
            }
            for candidate in candidates
            if candidate.get("domain_tag") and candidate.get("files")
        }
        refined: List[Dict[str, Any]] = []
        generic_titles = {"runtime", "core", "engine", "backend", "system"}

        for feature in features:
            title_terms = set(_normalize_text(feature.get("title", "")).split())
            feature_files = set(feature.get("files_touched", []))
            overlapping = []
            for domain_tag, candidate in candidate_map.items():
                overlap = feature_files & candidate["files"]
                if overlap:
                    overlapping.append((domain_tag, candidate, overlap))

            should_split = (
                len(overlapping) >= 1
                and len(feature_files) >= 4
                and (title_terms & generic_titles or len(feature_files) >= 8)
            )
            if not should_split:
                refined.append(feature)
                continue

            consumed: set[str] = set()
            for domain_tag, candidate, overlap in sorted(overlapping, key=lambda item: (-len(item[2]), item[0])):
                consumed |= overlap
                refined.append(self._build_feature_record(
                    feature_id=f"feat_{domain_tag}",
                    title=candidate["title"],
                    description=f"Code contributing to {candidate['title'].lower()}.",
                    files=sorted(overlap),
                    file_boundaries=candidate["boundaries"] or self._candidate_boundaries(sorted(overlap)),
                    source=feature.get("source", "auto"),
                    status=feature.get("status", "in-progress"),
                ))

            remainder = sorted(feature_files - consumed)
            if remainder:
                residual = deepcopy(feature)
                residual["files_touched"] = remainder
                residual["file_boundaries"] = self._candidate_boundaries(remainder)
                refined.append(residual)

        return self._deduplicate_features(refined)

    def _link_documents_to_document(
        self,
        document: Dict[str, Any],
        inventory: Dict[str, Any],
        use_embeddings: bool = False,
    ) -> None:
        feature_map = {feature["feature_id"]: feature for feature in document.get("features", [])}
        for feature in feature_map.values():
            feature["docs"] = []

        project_docs: List[Dict[str, Any]] = []
        documents = inventory.get("documents", [])
        if not documents or not feature_map:
            document["project_docs"] = sorted(document.get("project_docs", []), key=lambda item: item.get("path", ""))
            return

        feature_profiles = self._build_feature_text_profiles(document.get("features", []))
        unresolved_docs: List[Dict[str, Any]] = []

        for doc in documents:
            deterministic = self._deterministic_doc_feature_links(doc, document.get("features", []))
            if deterministic:
                for feature_id, relevance, score, signals in deterministic:
                    feature_map[feature_id]["docs"].append({
                        "path": doc["path"],
                        "title": doc.get("title", ""),
                        "relevance": relevance,
                        "score": round(score, 4),
                        "signals": signals,
                    })
                continue
            unresolved_docs.append(doc)

        if unresolved_docs:
            ownership_links, project_scope_docs, still_unresolved = self._ownership_link_documents(
                unresolved_docs,
                document.get("features", []),
                inventory,
            )
            for feature_id, doc_link in ownership_links:
                feature_map[feature_id]["docs"].append(doc_link)
            project_docs.extend(project_scope_docs)
            unresolved_docs = still_unresolved

        if unresolved_docs:
            bm25_links, still_unresolved = self._bm25_link_documents(unresolved_docs, feature_profiles)
            for feature_id, doc_link in bm25_links:
                feature_map[feature_id]["docs"].append(doc_link)
            unresolved_docs = still_unresolved

        if unresolved_docs and use_embeddings:
            embedding_links, still_unresolved = self._embedding_link_documents(unresolved_docs, feature_profiles)
            for feature_id, doc_link in embedding_links:
                feature_map[feature_id]["docs"].append(doc_link)
            unresolved_docs = still_unresolved

        for doc in unresolved_docs:
            project_docs.append({
                "path": doc["path"],
                "title": doc.get("title", ""),
                "scope": "project",
                "signals": ["unresolved_doc"],
            })

        for feature in feature_map.values():
            feature["docs"] = self._deduplicate_doc_links(feature.get("docs", []))
        document["project_docs"] = self._deduplicate_doc_links(project_docs)

    def _build_feature_text_profiles(self, features: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        profiles: List[Dict[str, Any]] = []
        for feature in features:
            tokens = [
                feature.get("title", ""),
                feature.get("description", ""),
                " ".join(feature.get("files_touched", [])),
                " ".join(feature.get("file_boundaries", [])),
            ]
            text = "\n".join(part for part in tokens if part).strip()
            profiles.append({
                "feature_id": feature["feature_id"],
                "text": text,
            })
        return profiles

    def _build_feature_doc_profiles(
        self,
        features: List[Dict[str, Any]],
        inventory: Dict[str, Any],
    ) -> List[Dict[str, Any]]:
        file_lookup = {item["path"]: item for item in inventory.get("files", [])}
        profiles: List[Dict[str, Any]] = []

        for feature in features:
            title_terms = set(self._filtered_semantic_terms(
                feature.get("title", ""),
                extra_noise=DOC_NOISE_TERMS,
            ))
            alias_terms = set(title_terms)
            semantic_terms = set(title_terms)
            semantic_terms.update(self._filtered_semantic_terms(
                feature.get("description", ""),
                extra_noise=DOC_NOISE_TERMS,
                drop_generic_cluster_terms=True,
            ))

            alias_terms.update(self._filtered_semantic_terms(
                " ".join(feature.get("file_boundaries", [])),
                extra_noise=DOC_NOISE_TERMS,
                drop_generic_cluster_terms=True,
            ))

            for path in feature.get("files_touched", []):
                alias_terms.update(self._filtered_semantic_terms(
                    path,
                    extra_noise=DOC_NOISE_TERMS,
                    drop_generic_cluster_terms=True,
                ))
                file_info = file_lookup.get(path)
                if not file_info:
                    continue
                alias_terms.update(self._filtered_semantic_terms(
                    " ".join(file_info.get("top_identifiers", []) + file_info.get("exported_symbols", []) + file_info.get("semantic_tags", [])),
                    extra_noise=DOC_NOISE_TERMS,
                    drop_generic_cluster_terms=True,
                ))
                semantic_terms.update(self._filtered_semantic_terms(
                    "\n".join([
                        file_info.get("path", ""),
                        file_info.get("snippet", ""),
                        " ".join(file_info.get("top_identifiers", [])),
                        " ".join(file_info.get("structural_symbols", [])),
                        " ".join(file_info.get("signatures", [])),
                        " ".join(file_info.get("exported_symbols", [])),
                        " ".join(file_info.get("route_metadata", [])),
                        " ".join(file_info.get("semantic_tags", [])),
                    ]),
                    extra_noise=DOC_NOISE_TERMS,
                    drop_generic_cluster_terms=True,
                ))

            profiles.append({
                "feature_id": feature["feature_id"],
                "title_terms": title_terms,
                "alias_terms": alias_terms,
                "semantic_terms": semantic_terms,
                "files": set(feature.get("files_touched", [])),
            })

        return profiles

    def _ownership_link_documents(
        self,
        documents: List[Dict[str, Any]],
        features: List[Dict[str, Any]],
        inventory: Dict[str, Any],
    ) -> Tuple[List[Tuple[str, Dict[str, Any]]], List[Dict[str, Any]], List[Dict[str, Any]]]:
        profiles = self._build_feature_doc_profiles(features, inventory)
        profile_map = {profile["feature_id"]: profile for profile in profiles}
        term_df: Dict[str, int] = {}
        for profile in profiles:
            for term in profile.get("title_terms", set()) | profile.get("alias_terms", set()) | profile.get("semantic_terms", set()):
                term_df[term] = term_df.get(term, 0) + 1
        links: List[Tuple[str, Dict[str, Any]]] = []
        project_docs: List[Dict[str, Any]] = []
        unresolved: List[Dict[str, Any]] = []

        for doc in documents:
            project_scope_signals = doc.get("project_scope_signals", [])
            doc_kind = doc.get("doc_kind", "doc")
            strong_project_scope = (
                doc_kind in {"plan", "vision", "architecture"}
                and any(
                    signal in project_scope_signals
                    for signal in {"project_scope_doc_name", "project_scope_doc_phrase", "project_scope_root_overview"}
                )
            )

            if strong_project_scope:
                project_docs.append({
                    "path": doc["path"],
                    "title": doc.get("title", ""),
                    "scope": "project",
                    "signals": ["project_scope_doc"] + project_scope_signals,
                })
                continue

            scored: List[Tuple[float, str, List[str]]] = []
            for feature in features:
                profile = profile_map.get(feature["feature_id"])
                if not profile:
                    continue
                score, signals = self._doc_feature_ownership_score(doc, profile, term_df)
                if score > 0:
                    scored.append((score, feature["feature_id"], signals))

            scored.sort(key=lambda item: item[0], reverse=True)
            best_score = scored[0][0] if scored else 0.0
            second_score = scored[1][0] if len(scored) > 1 else 0.0

            if scored and best_score >= 2.2 and (best_score - second_score) >= 0.35:
                feature_id = scored[0][1]
                relevance = "primary" if best_score >= 3.2 or doc_kind in {"format", "spec"} else "reference"
                links.append((feature_id, {
                    "path": doc["path"],
                    "title": doc.get("title", ""),
                    "relevance": relevance,
                    "score": round(best_score, 4),
                    "signals": scored[0][2],
                }))
                continue

            if doc.get("project_scope_signals") and best_score < 2.8:
                project_docs.append({
                    "path": doc["path"],
                    "title": doc.get("title", ""),
                    "scope": "project",
                    "signals": ["project_scope_doc"] + doc.get("project_scope_signals", []),
                })
                continue

            unresolved.append(doc)

        return links, project_docs, unresolved

    def _doc_feature_ownership_score(
        self,
        doc: Dict[str, Any],
        feature_profile: Dict[str, Any],
        term_df: Dict[str, int],
    ) -> Tuple[float, List[str]]:
        score = 0.0
        signals: List[str] = []

        title_terms = set(doc.get("title_terms", []))
        semantic_terms = set(doc.get("semantic_terms", []))
        path_terms = set(self._filtered_semantic_terms(
            doc.get("path", ""),
            extra_noise=DOC_NOISE_TERMS,
            drop_generic_cluster_terms=True,
        ))
        feature_title_terms = feature_profile.get("title_terms", set())
        feature_alias_terms = feature_profile.get("alias_terms", set())
        feature_semantic_terms = feature_profile.get("semantic_terms", set())

        title_overlap = title_terms & feature_title_terms
        if title_overlap:
            title_weight = sum(1.0 / max(1, term_df.get(term, 1)) for term in title_overlap)
            score += 1.8 + min(1.8, 1.4 * title_weight)
            signals.append("doc_title_matches_feature_terms")

        alias_overlap = (title_terms | path_terms) & feature_alias_terms
        alias_overlap -= title_overlap
        if alias_overlap:
            alias_weight = sum(1.0 / max(1, term_df.get(term, 1)) for term in alias_overlap)
            score += 0.8 + min(1.6, 1.2 * alias_weight)
            signals.append("doc_name_matches_feature_alias")

        semantic_overlap = semantic_terms & feature_semantic_terms
        semantic_overlap -= title_overlap
        semantic_overlap -= alias_overlap
        if semantic_overlap:
            semantic_weight = sum(1.0 / max(1, term_df.get(term, 2)) for term in semantic_overlap)
            score += min(1.8, 0.6 * semantic_weight)
            signals.append("doc_semantic_matches_feature")

        referenced_paths = set(doc.get("referenced_paths", []))
        if referenced_paths & feature_profile.get("files", set()):
            score += 0.8
            signals.append("doc_references_feature_files")

        doc_kind = doc.get("doc_kind", "doc")
        if doc_kind in {"format", "spec", "architecture"} and (title_overlap or alias_overlap):
            score += 0.6
            signals.append("typed_doc_feature_owner")

        return score, signals

    def _deterministic_doc_feature_links(
        self,
        doc: Dict[str, Any],
        features: List[Dict[str, Any]],
    ) -> List[Tuple[str, str, float, List[str]]]:
        results: List[Tuple[str, str, float, List[str]]] = []
        doc_path = doc["path"]
        doc_title_slug = _slugify(doc.get("title", ""))
        doc_text = doc.get("content", "")
        referenced_paths = set(doc.get("referenced_paths", []))
        is_project_scope_doc = bool(doc.get("project_scope_signals"))

        for feature in features:
            signals: List[str] = []
            score = 0.0
            if self._path_matches_feature_boundaries(doc_path, feature):
                signals.append("doc_in_boundary")
                score += 1.0
            file_hits = referenced_paths & set(feature.get("files_touched", []))
            if file_hits:
                signals.append("doc_references_feature_files")
                score += 1.5 + (0.1 * len(file_hits))
            feature_slug = _slugify(feature.get("title", ""))
            if feature_slug and (feature_slug in _slugify(doc_path) or feature_slug in doc_title_slug):
                signals.append("doc_name_matches_feature")
                score += 1.0
            if feature_slug and feature_slug in _slugify(doc_text[:600]):
                signals.append("doc_text_matches_feature")
                score += 0.5
            if (
                is_project_scope_doc
                and file_hits
                and not any(
                    signal in signals
                    for signal in {"doc_in_boundary", "doc_name_matches_feature", "doc_text_matches_feature"}
                )
            ):
                continue
            if score >= 1.0:
                relevance = "primary" if score >= 1.5 else "reference"
                results.append((feature["feature_id"], relevance, score, signals))

        results.sort(key=lambda item: item[2], reverse=True)
        return results[:2]

    def _bm25_link_documents(
        self,
        documents: List[Dict[str, Any]],
        feature_profiles: List[Dict[str, Any]],
    ) -> Tuple[List[Tuple[str, Dict[str, Any]]], List[Dict[str, Any]]]:
        feature_tokens = {
            profile["feature_id"]: self._tokenize_text(profile["text"])
            for profile in feature_profiles
        }
        corpus = list(feature_tokens.values())
        avg_doc_len = sum(len(tokens) for tokens in corpus) / len(corpus) if corpus else 0.0
        df: Dict[str, int] = {}
        for tokens in corpus:
            for term in set(tokens):
                df[term] = df.get(term, 0) + 1

        links: List[Tuple[str, Dict[str, Any]]] = []
        unresolved: List[Dict[str, Any]] = []
        total_docs = len(corpus)

        for doc in documents:
            query_tokens = self._tokenize_text(" ".join([doc.get("title", ""), doc.get("content", "")[:2000]]))
            best_feature_id = None
            best_score = 0.0
            second_score = 0.0
            for feature_id, tokens in feature_tokens.items():
                score = self._bm25_score(query_tokens, tokens, df, avg_doc_len, total_docs)
                if score > best_score:
                    second_score = best_score
                    best_score = score
                    best_feature_id = feature_id
                elif score > second_score:
                    second_score = score

            if best_feature_id and best_score >= 1.2 and (best_score - second_score) >= 0.15:
                relevance = "primary" if best_score >= 2.5 else "reference"
                links.append((best_feature_id, {
                    "path": doc["path"],
                    "title": doc.get("title", ""),
                    "relevance": relevance,
                    "score": round(best_score, 4),
                    "signals": ["bm25"],
                }))
            else:
                unresolved.append(doc)

        return links, unresolved

    def _embedding_link_documents(
        self,
        documents: List[Dict[str, Any]],
        feature_profiles: List[Dict[str, Any]],
    ) -> Tuple[List[Tuple[str, Dict[str, Any]]], List[Dict[str, Any]]]:
        from ..retrieval.embeddings import cosine_similarity, get_embedding_provider, normalize_vector

        if not documents or not feature_profiles:
            return [], documents

        try:
            provider = get_embedding_provider()
        except Exception:
            return [], documents

        feature_texts = [profile["text"] for profile in feature_profiles]
        doc_texts = [
            " ".join([doc.get("title", ""), doc.get("content", "")[:2000]]).strip()
            for doc in documents
        ]

        try:
            feature_vectors = [normalize_vector(vec) for vec in provider.embed_batch(feature_texts)]
            doc_vectors = [normalize_vector(vec) for vec in provider.embed_batch(doc_texts)]
        except Exception:
            return [], documents

        links: List[Tuple[str, Dict[str, Any]]] = []
        unresolved: List[Dict[str, Any]] = []

        for doc, doc_vec in zip(documents, doc_vectors):
            scored = []
            for profile, feature_vec in zip(feature_profiles, feature_vectors):
                scored.append((cosine_similarity(doc_vec, feature_vec), profile["feature_id"]))
            scored.sort(reverse=True)
            if not scored:
                unresolved.append(doc)
                continue
            best_score, best_feature_id = scored[0]
            second_score = scored[1][0] if len(scored) > 1 else 0.0
            if best_score >= 0.35 and (best_score - second_score) >= 0.03:
                relevance = "primary" if best_score >= 0.55 else "reference"
                links.append((best_feature_id, {
                    "path": doc["path"],
                    "title": doc.get("title", ""),
                    "relevance": relevance,
                    "score": round(best_score, 4),
                    "signals": ["embedding_similarity"],
                }))
            else:
                unresolved.append(doc)

        return links, unresolved

    def _tokenize_text(self, text: str) -> List[str]:
        return [token for token in re.findall(r"[a-z0-9_./-]+", _normalize_text(text)) if len(token) > 2]

    def _semantic_terms(self, text: str) -> List[str]:
        terms: List[str] = []
        for raw_token in re.findall(r"[A-Za-z0-9_./-]+", text or ""):
            token = _normalize_text(raw_token)
            if len(token) > 2:
                terms.append(token)
            split_parts = re.split(r"[._/\-]+", raw_token)
            for part in split_parts:
                lowered_part = _normalize_text(part)
                if len(lowered_part) > 2:
                    terms.append(lowered_part)
                camel_parts = re.findall(r"[A-Z]+(?=[A-Z][a-z]|\d|$)|[A-Z]?[a-z]+|\d+", part)
                for camel_part in camel_parts:
                    lowered_camel = _normalize_text(camel_part)
                    if len(lowered_camel) > 2:
                        terms.append(lowered_camel)
        return terms

    def _filtered_semantic_terms(
        self,
        text: str,
        extra_noise: Optional[set[str]] = None,
        drop_generic_cluster_terms: bool = False,
    ) -> List[str]:
        noise = set(CODE_NOISE_TERMS) | set(TEXT_STOP_TERMS) | {"py", "ts", "tsx", "js", "jsx", "md", "rst", "txt", "adoc"}
        if extra_noise:
            noise.update(extra_noise)
        if drop_generic_cluster_terms:
            noise.update(GENERIC_CLUSTER_TERMS)

        filtered: List[str] = []
        seen: set[str] = set()
        for term in self._semantic_terms(text):
            if len(term) <= 2 or term in noise:
                continue
            if term in seen:
                continue
            seen.add(term)
            filtered.append(term)
        return filtered

    def _bm25_score(
        self,
        query_tokens: List[str],
        doc_tokens: List[str],
        df: Dict[str, int],
        avg_doc_len: float,
        total_docs: int,
        k1: float = 1.5,
        b: float = 0.75,
    ) -> float:
        if not query_tokens or not doc_tokens or total_docs <= 0:
            return 0.0
        score = 0.0
        doc_len = len(doc_tokens)
        for term in set(query_tokens):
            if term not in df:
                continue
            tf = doc_tokens.count(term)
            if tf == 0:
                continue
            idf = max(0.0, math.log((total_docs - df[term] + 0.5) / (df[term] + 0.5) + 1.0))
            denom = tf + k1 * (1 - b + b * (doc_len / avg_doc_len if avg_doc_len else 1.0))
            score += idf * ((tf * (k1 + 1)) / denom)
        return score

    def _deduplicate_doc_links(self, docs: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        deduped: Dict[str, Dict[str, Any]] = {}
        for doc in docs:
            path = doc.get("path")
            if not path:
                continue
            existing = deduped.get(path)
            if existing is None or doc.get("score", 0) > existing.get("score", 0):
                deduped[path] = doc
        return sorted(deduped.values(), key=lambda item: item.get("path", ""))

    def _doc_links_additions(self, current: List[Dict[str, Any]], updated: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        current_keys = {(item.get("path"), item.get("relevance"), item.get("scope")) for item in current}
        additions = [
            item for item in updated
            if (item.get("path"), item.get("relevance"), item.get("scope")) not in current_keys
        ]
        return additions

    def _attach_remaining_support_files(
        self,
        remaining: set[str],
        inventory: Dict[str, Any],
        features: List[Dict[str, Any]],
    ) -> None:
        file_lookup = {item["path"]: item for item in inventory.get("files", [])}
        changed = True
        while changed:
            changed = False
            for path in sorted(list(remaining)):
                if not self._should_expose_file_in_feature(path):
                    continue
                file_info = file_lookup.get(path)
                if not file_info:
                    continue
                match = self._find_support_feature_match(file_info, features)
                if match is None:
                    continue
                if not (
                    self._is_support_like_file(file_info)
                    or self._is_strong_shadow_match(file_info, match)
                ):
                    continue
                match["files_touched"] = sorted(set(match.get("files_touched", [])) | {path})
                parent = Path(path).parent
                if parent != Path("."):
                    match["file_boundaries"] = sorted(set(match.get("file_boundaries", [])) | {parent.as_posix() + "/**"})
                remaining.discard(path)
                changed = True

    def _find_support_feature_match(
        self,
        file_info: Dict[str, Any],
        features: List[Dict[str, Any]],
    ) -> Optional[Dict[str, Any]]:
        scored: List[Tuple[int, Dict[str, Any]]] = []
        for feature in features:
            score = self._support_similarity_score(file_info, feature)
            if score > 0:
                scored.append((score, feature))

        if not scored:
            return None

        scored.sort(key=lambda item: item[0], reverse=True)
        top_score = scored[0][0]
        top_matches = [feature for score, feature in scored if score == top_score]
        if len(top_matches) != 1:
            return None
        return top_matches[0]

    def _is_support_like_file(self, file_info: Dict[str, Any]) -> bool:
        role_hint = file_info.get("role_hint")
        if role_hint in {"test", "config", "legacy"}:
            return True
        return file_info.get("support_score", 0.0) >= 0.5

    def _is_strong_shadow_match(self, file_info: Dict[str, Any], feature: Dict[str, Any]) -> bool:
        score = self._support_similarity_score(file_info, feature)
        if score < 8:
            return False
        feature_files = set(feature.get("files_touched", []))
        import_links = len((set(file_info.get("imports", [])) | set(file_info.get("imported_by", []))) & feature_files)
        if import_links > 0:
            return True
        generic_stems = {"app", "index", "layout", "main", "page", "route", "server", "__init__"}
        return Path(file_info.get("path", "")).stem.lower() not in generic_stems

    def _support_similarity_score(self, file_info: Dict[str, Any], feature: Dict[str, Any]) -> int:
        path = file_info["path"]
        group_key = file_info.get("group_key", "")
        imports = set(file_info.get("imports", []))
        imported_by = set(file_info.get("imported_by", []))
        feature_files = set(feature.get("files_touched", []))
        feature_paths = [Path(item) for item in feature_files]

        import_links = len((imports | imported_by) & feature_files)
        same_group = sum(1 for candidate_path in feature_files if group_key and candidate_path.startswith(group_key))

        path_obj = Path(path)
        file_stem = path_obj.stem.lower()
        parent_name = path_obj.parent.name.lower()
        feature_stems = {candidate.stem.lower() for candidate in feature_paths}
        feature_parent_names = {candidate.parent.name.lower() for candidate in feature_paths}
        title_tokens = set(_slugify(feature.get("title", "")).split("_"))
        desc_tokens = set(_slugify(feature.get("description", "")).split("_"))

        stem_overlap = 1 if file_stem and file_stem in feature_stems else 0
        parent_overlap = 1 if parent_name and (parent_name in feature_parent_names or parent_name in title_tokens or parent_name in desc_tokens) else 0
        duplicate_basename = sum(1 for candidate in feature_paths if candidate.name.lower() == path_obj.name.lower())

        return import_links * 10 + same_group * 2 + duplicate_basename * 4 + stem_overlap * 3 + parent_overlap * 2

    def _should_backfill_uncategorized(self, path: str) -> bool:
        """Whether an uncovered file should be backfilled into fallback features.

        More permissive than _should_expose_file_in_feature — allows test and
        config files to be backfilled so they land in *some* feature rather
        than being silently dropped.
        """
        rel = Path(path)
        if not rel.parts:
            return False
        if rel.parts[0] in NON_FEATURE_TOP_LEVEL_DIRS:
            return False
        if rel.name in NON_FEATURE_FILENAMES:
            return False
        if rel.name == "__init__.py":
            return False
        return True

    def _should_expose_file_in_feature(self, path: str) -> bool:
        """Whether a file should be listed in a feature's files_touched.

        Stricter than backfill — excludes top-level config files and
        non-feature directories so features stay focused on domain code.
        """
        rel = Path(path)
        if not rel.parts:
            return False
        if rel.parts[0] in NON_FEATURE_TOP_LEVEL_DIRS:
            return False
        if rel.name in NON_FEATURE_FILENAMES:
            return False
        # Top-level config files without a directory are infrastructure, not features
        if len(rel.parts) == 1 and self._is_config_file(self.project_root / path):
            return False
        return True

    def _deduplicate_features(self, features: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        deduped: List[Dict[str, Any]] = []
        for feature in features:
            merged = False
            for existing in deduped:
                same_id = existing["feature_id"] == feature["feature_id"]
                same_files = set(existing["files_touched"]) == set(feature["files_touched"])
                if same_id or same_files:
                    existing["files_touched"] = sorted(set(existing["files_touched"]) | set(feature["files_touched"]))
                    existing["file_boundaries"] = sorted(set(existing["file_boundaries"]) | set(feature["file_boundaries"]))
                    merged = True
                    break
            if not merged:
                deduped.append(feature)
        return deduped

    def _build_feature_record(
        self,
        feature_id: str,
        title: str,
        description: str,
        files: List[str],
        file_boundaries: Optional[List[str]] = None,
        source: str = "auto",
        status: str = "in-progress",
    ) -> Dict[str, Any]:
        return {
            "feature_id": feature_id,
            "feature_version": 1,
            "title": title,
            "description": description,
            "status": status,
            "source": source,
            "files_touched": sorted(set(files)),
            "file_boundaries": sorted(set(file_boundaries or self._candidate_boundaries(files))),
            "docs": [],
            "session_ids": [],
            "last_updated_session": None,
            "updated_at": _utc_now(),
            "staleness": {
                "status": "unknown",
                "checked_at": _utc_now(),
                "signals": [],
            },
        }

    def _extract_feature_candidates(self, session, summary: Dict[str, Any]) -> List[Dict[str, Any]]:
        code_changes = list(summary.get("code_changes", []) or [])
        overview = (summary.get("overview") or "").strip()
        session_id = session.session_id

        grouped: List[Dict[str, Any]] = []
        for change in code_changes:
            files = sorted({
                self._normalize_file_path(path)
                for path in change.get("files", [])
                if isinstance(path, str) and path.strip()
            })
            if not files:
                continue

            description = change.get("description") or "Feature work from session summary"
            candidate = {
                "feature_id": f"feat_{_slugify(files[0])}",
                "feature_version": 1,
                "title": self._candidate_title(files, description),
                "description": description,
                "status": "in-progress",
                "source": "auto",
                "files_touched": files,
                "file_boundaries": self._candidate_boundaries(files),
                "session_ids": [session_id],
                "last_updated_session": session_id,
                "updated_at": _utc_now(),
                "staleness": {
                    "status": "unknown",
                    "checked_at": _utc_now(),
                    "signals": [],
                },
            }

            merged = False
            for existing in grouped:
                if self._files_overlap(existing["files_touched"], candidate["files_touched"]):
                    existing["files_touched"] = sorted(set(existing["files_touched"]) | set(candidate["files_touched"]))
                    existing["file_boundaries"] = sorted(set(existing["file_boundaries"]) | set(candidate["file_boundaries"]))
                    if description not in existing["description"]:
                        existing["description"] = f"{existing['description']} {description}".strip()
                    merged = True
                    break
            if not merged:
                grouped.append(candidate)

        if grouped:
            return grouped

        if overview:
            return [{
                "feature_id": f"feat_{_slugify(overview[:40])}",
                "feature_version": 1,
                "title": self._candidate_title([], overview),
                "description": overview,
                "status": "in-progress",
                "source": "auto",
                "files_touched": [],
                "file_boundaries": [],
                "session_ids": [session_id],
                "last_updated_session": session_id,
                "updated_at": _utc_now(),
                "staleness": {
                    "status": "unknown",
                    "checked_at": _utc_now(),
                    "signals": [],
                },
            }]
        return []

    def _compute_feature_staleness(
        self,
        features: List[Dict[str, Any]],
        known_paths: set[str],
        inventory: Dict[str, Any],
    ) -> None:
        """Compute staleness signals for each feature based on codebase state."""
        now = _utc_now()
        inventory_file_set = {item["path"] for item in inventory.get("files", [])}

        for feature in features:
            signals: List[str] = []
            files_touched = set(feature.get("files_touched", []))
            file_boundaries = feature.get("file_boundaries", [])
            status = feature.get("status", "in-progress")

            # Signal: files in files_touched that no longer exist
            deleted_files = files_touched - known_paths
            if deleted_files:
                signals.append(f"deleted_files:{len(deleted_files)}")

            # Signal: files_touched outside declared file_boundaries
            if file_boundaries and files_touched:
                boundary_violations = [
                    path for path in files_touched
                    if path in known_paths and not self._path_matches_feature_boundaries(path, feature)
                ]
                if boundary_violations:
                    signals.append(f"files_outside_boundaries:{len(boundary_violations)}")

            # Signal: feature marked complete but files still changing
            # (detected by checking if inventory files in boundary have recent modifications)
            if status == "complete" and files_touched:
                # If we have session_ids, a complete feature shouldn't be getting new sessions
                # This is a lightweight heuristic; full detection needs session timestamps
                pass

            # Signal: feature marked in-progress but all files deleted
            if status == "in-progress" and files_touched and not (files_touched & known_paths):
                signals.append("all_files_removed")

            # Signal: planned feature with files that now exist
            if status == "planned" and not files_touched:
                # Check if any boundary files appeared
                for path in inventory_file_set:
                    if self._path_matches_feature_boundaries(path, feature):
                        signals.append("planned_feature_has_code")
                        break

            if signals:
                staleness_status = "stale"
            elif deleted_files or not files_touched:
                staleness_status = "unknown"
            else:
                staleness_status = "current"

            feature["staleness"] = {
                "status": staleness_status,
                "checked_at": now,
                "signals": signals,
            }

    def detect_boundary_overlaps(self, document: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
        """Detect files or boundaries claimed by multiple features.

        Returns a list of overlap records, each with the file path and the
        feature_ids that claim it.
        """
        if document is None:
            document = self.load_or_create()

        features = document.get("features", [])
        if len(features) < 2:
            return []

        # Check files_touched overlaps
        file_owners: Dict[str, List[str]] = {}
        for feature in features:
            fid = feature.get("feature_id", "")
            for path in feature.get("files_touched", []):
                file_owners.setdefault(path, []).append(fid)

        overlaps: List[Dict[str, Any]] = []
        for path, owners in sorted(file_owners.items()):
            if len(owners) > 1:
                overlaps.append({
                    "path": path,
                    "kind": "files_touched",
                    "feature_ids": sorted(owners),
                })

        # Check file_boundaries overlaps (by expanding boundaries to actual files)
        inventory = self._build_codebase_inventory()
        all_paths = {item["path"] for item in inventory.get("files", [])}
        boundary_owners: Dict[str, List[str]] = {}

        for feature in features:
            fid = feature.get("feature_id", "")
            for path in all_paths:
                if self._path_matches_feature_boundaries(path, feature):
                    boundary_owners.setdefault(path, []).append(fid)

        for path, owners in sorted(boundary_owners.items()):
            if len(owners) > 1:
                # Don't duplicate if already caught by files_touched
                if not any(o["path"] == path and o["kind"] == "files_touched" for o in overlaps):
                    overlaps.append({
                        "path": path,
                        "kind": "file_boundaries",
                        "feature_ids": sorted(owners),
                    })

        return overlaps

    def _candidate_title(self, files: List[str], description: str) -> str:
        if description:
            return " ".join(description.strip().rstrip(".").split()[:8])
        if files:
            return Path(files[0]).stem.replace("_", " ").replace("-", " ").title()
        return "Project Feature"

    def _candidate_boundaries(self, files: List[str]) -> List[str]:
        boundaries = set()
        for path_str in files:
            path = Path(path_str)
            if len(path.parts) > 1:
                boundaries.add(Path(*path.parts[:-1]).as_posix() + "/**")
            else:
                boundaries.add(path.as_posix())
        return sorted(boundaries)

    def _normalize_file_path(self, value: str) -> str:
        path = Path(value)
        if path.is_absolute():
            return _relative_or_absolute(path, self.project_root)
        return path.as_posix()

    def _match_feature(self, features: List[Dict[str, Any]], candidate: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        candidate_files = set(candidate.get("files_touched", []))
        if not candidate_files:
            return None

        best_match = None
        best_score = 0
        for feature in features:
            evidence = set(feature.get("files_touched", []))
            boundaries = [str(boundary).replace("/**", "") for boundary in feature.get("file_boundaries", [])]
            overlap = len(candidate_files & evidence)
            prefix_overlap = sum(
                1
                for file_path in candidate_files
                for boundary in boundaries
                if boundary and file_path.startswith(boundary)
            )
            score = overlap + prefix_overlap
            if score > best_score:
                best_score = score
                best_match = feature
        return best_match if best_score > 0 else None

    def _find_covering_feature(self, features: List[Dict[str, Any]], path: str) -> Optional[Dict[str, Any]]:
        exact_matches = [
            feature for feature in features
            if path in feature.get("files_touched", [])
        ]
        if len(exact_matches) == 1:
            return exact_matches[0]
        if len(exact_matches) > 1:
            return exact_matches[0]

        boundary_matches = [
            feature for feature in features
            if self._path_matches_feature_boundaries(path, feature)
        ]
        if not boundary_matches:
            return None
        if len(boundary_matches) == 1:
            return boundary_matches[0]
        boundary_matches.sort(
            key=lambda feature: self._boundary_specificity(feature, path),
            reverse=True,
        )
        return boundary_matches[0]

    def _path_matches_feature_boundaries(self, path: str, feature: Dict[str, Any]) -> bool:
        for raw_boundary in feature.get("file_boundaries", []):
            boundary = str(raw_boundary)
            if boundary.endswith("/**"):
                prefix = boundary[:-3]
                if prefix and path.startswith(prefix):
                    return True
                continue
            if path == boundary:
                return True
        return False

    def _boundary_specificity(self, feature: Dict[str, Any], path: str) -> int:
        best = 0
        for raw_boundary in feature.get("file_boundaries", []):
            boundary = str(raw_boundary)
            if boundary.endswith("/**"):
                prefix = boundary[:-3]
                if prefix and path.startswith(prefix):
                    best = max(best, len(prefix))
            elif path == boundary:
                best = max(best, len(boundary) + 1000)
        return best

    def _files_overlap(self, left: List[str], right: List[str]) -> bool:
        return bool(set(left) & set(right))

    def _find_session_entry(self, document: Dict[str, Any], session_id: str) -> Optional[Dict[str, Any]]:
        return next((entry for entry in document.get("session_index", []) if entry.get("session_id") == session_id), None)

    def _drop_pending_for_session(self, document: Dict[str, Any], session_id: str) -> None:
        document["proposals"] = [
            proposal for proposal in document.get("proposals", [])
            if not (proposal.get("status") == "pending" and proposal.get("source_session_id") == session_id)
        ]

    def _archive_proposal(self, document: Dict[str, Any], proposal: Dict[str, Any]) -> None:
        document["proposals"] = [
            current for current in document.get("proposals", [])
            if current.get("proposal_id") != proposal.get("proposal_id")
        ]
        document.setdefault("update_history", []).append(deepcopy(proposal))
        document["update_history"] = document["update_history"][-MAX_UPDATE_HISTORY:]

    def _diff_op_is_noop(self, op: Dict[str, Any]) -> bool:
        kind = op.get("op")
        if kind == "update_feature":
            changes = op.get("changes", {})
            return not any(
                value for key, value in changes.items()
                if key.endswith("_add") or key in ("status", "title", "description")
            )
        if kind == "update_project_docs":
            return not op.get("docs_add")
        if kind == "mark_status":
            return not op.get("status")
        if kind == "archive_feature":
            return not op.get("feature_id")
        if kind == "unlink_session":
            return not op.get("session_id")
        return False

    def _apply_diff_op(self, document: Dict[str, Any], op: Dict[str, Any]) -> None:
        kind = op.get("op")
        if kind == "add_feature":
            feature = deepcopy(op["feature"])
            if not any(item.get("feature_id") == feature.get("feature_id") for item in document.get("features", [])):
                document.setdefault("features", []).append(feature)
            return

        if kind == "update_feature":
            feature = next(
                (item for item in document.get("features", []) if item.get("feature_id") == op.get("feature_id")),
                None,
            )
            if feature is None:
                return

            changes = op.get("changes", {})
            material_change = False
            for field in ("files_touched_add", "file_boundaries_add", "session_ids_add"):
                if field in changes:
                    target = field.replace("_add", "")
                    new_values = set(changes[field]) - set(feature.get(target, []))
                    if new_values:
                        feature[target] = sorted(set(feature.get(target, [])) | new_values)
                        if field != "session_ids_add":
                            material_change = True
            if "docs_add" in changes:
                feature["docs"] = self._deduplicate_doc_links(feature.get("docs", []) + changes["docs_add"])
            if "status" in changes and changes["status"] != feature.get("status"):
                feature["status"] = changes["status"]
                material_change = True
            if "title" in changes and changes["title"] != feature.get("title"):
                feature["title"] = changes["title"]
                material_change = True
            if "description" in changes and changes["description"] != feature.get("description"):
                feature["description"] = changes["description"]
                material_change = True
            if "last_updated_session" in changes:
                feature["last_updated_session"] = changes["last_updated_session"]
            if "updated_at" in changes:
                feature["updated_at"] = changes["updated_at"]
            if material_change:
                feature["feature_version"] = feature.get("feature_version", 1) + 1
            return

        if kind == "mark_status":
            feature = next(
                (item for item in document.get("features", []) if item.get("feature_id") == op.get("feature_id")),
                None,
            )
            if feature is None:
                return
            new_status = op.get("status")
            if new_status and new_status != feature.get("status"):
                feature["status"] = new_status
                feature["updated_at"] = op.get("updated_at") or _utc_now()
                feature["feature_version"] = feature.get("feature_version", 1) + 1
            return

        if kind == "archive_feature":
            feature_id = op.get("feature_id")
            feature = next(
                (item for item in document.get("features", []) if item.get("feature_id") == feature_id),
                None,
            )
            if feature is not None:
                feature["status"] = "archived"
                feature["updated_at"] = op.get("updated_at") or _utc_now()
                feature["feature_version"] = feature.get("feature_version", 1) + 1
            return

        if kind == "update_project_docs":
            document["project_docs"] = self._deduplicate_doc_links(document.get("project_docs", []) + op.get("docs_add", []))
            return

        if kind == "link_session":
            payload = deepcopy(op.get("session", {}))
            session_id = payload.get("session_id")
            if not session_id:
                return
            existing = self._find_session_entry(document, session_id)
            if existing is None:
                document.setdefault("session_index", []).append(payload)
                return
            if "feature_ids_add" in payload:
                existing["feature_ids"] = sorted(set(existing.get("feature_ids", [])) | set(payload["feature_ids_add"]))
            for field in ("path", "started_at", "ended_at", "feature_ids"):
                if field in payload:
                    existing[field] = payload[field]
            return

        if kind == "unlink_session":
            session_id = op.get("session_id")
            if not session_id:
                return
            document["session_index"] = [
                entry for entry in document.get("session_index", [])
                if entry.get("session_id") != session_id
            ]
            # Also remove session references from features
            for feature in document.get("features", []):
                if session_id in feature.get("session_ids", []):
                    feature["session_ids"] = [sid for sid in feature["session_ids"] if sid != session_id]
            return
