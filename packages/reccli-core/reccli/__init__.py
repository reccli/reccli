"""
RecCli - Intelligent Terminal Session Recorder with .devsession format
Version 2.0 - Pure Python implementation with Native LLM
"""

__version__ = "2.0.0"
__author__ = "RecCli Team"

from .recorder import DevsessionRecorder
from .devsession import DevSession
from .devproject import DevProjectManager, discover_project_root
from .llm import LLMSession, chat_session, one_shot_query
from .config import Config

__all__ = [
    'DevsessionRecorder',
    'DevSession',
    'DevProjectManager',
    'discover_project_root',
    'LLMSession',
    'chat_session',
    'one_shot_query',
    'Config'
]
