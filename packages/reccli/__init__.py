"""
RecCli - Temporal memory engine for coding agents.

Tri-layer memory: .devproject (project features) → .devsession summary
(compacted working memory) → .devsession full conversation (source of truth).
"""

__version__ = "0.9.0"
__author__ = "Will Luecke"

from .recording import DevsessionRecorder
from .devsession import DevSession
from .project import DevProjectManager, discover_project_root
from .runtime import LLMSession, chat_session, one_shot_query, Config
