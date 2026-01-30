"""
RecCli - Intelligent Terminal Session Recorder with .devsession format
Version 2.0 - Pure Python implementation with Native LLM
"""

__version__ = "2.0.0"
__author__ = "RecCli Team"

from .recorder import DevsessionRecorder
from .devsession import DevSession
from .llm import LLMSession, chat_session, one_shot_query
from .config import Config
from .cli import main

__all__ = [
    'DevsessionRecorder',
    'DevSession',
    'LLMSession',
    'chat_session',
    'one_shot_query',
    'Config',
    'main'
]
