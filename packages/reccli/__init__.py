"""
RecCli - Temporal memory engine for coding agents.

Tri-layer memory: .devproject (project features) → .devsession summary
(compacted working memory) → .devsession full conversation (source of truth).
"""

from __future__ import annotations

from importlib import import_module
from typing import Any

__version__ = "0.9.0"
__author__ = "Will Luecke"

__all__ = [
    "Config",
    "DevProjectManager",
    "DevSession",
    "DevsessionRecorder",
    "LLMSession",
    "chat_session",
    "discover_project_root",
    "one_shot_query",
]

_LAZY_EXPORTS = {
    "DevsessionRecorder": (".recording", "DevsessionRecorder"),
    "DevSession": (".session.devsession", "DevSession"),
    "DevProjectManager": (".project", "DevProjectManager"),
    "discover_project_root": (".project", "discover_project_root"),
    "LLMSession": (".runtime", "LLMSession"),
    "chat_session": (".runtime", "chat_session"),
    "one_shot_query": (".runtime", "one_shot_query"),
    "Config": (".runtime", "Config"),
}


def __getattr__(name: str) -> Any:
    """Load optional interactive dependencies only when their API is used.

    The organization console and its control CLI are intentionally standard-
    library-only. Importing the package must not pull in prompt-toolkit,
    provider SDKs, or vector-search dependencies before those features are
    requested.
    """
    target = _LAZY_EXPORTS.get(name)
    if target is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    module_name, attribute = target
    value = getattr(import_module(module_name, package=__name__), attribute)
    globals()[name] = value
    return value
