"""
RecCli .devsession format implementation
Unified vector index for cross-session context retrieval
"""

from .unified_index import (
    build_unified_index,
    update_index_with_new_session,
    search_all_sessions,
    search_with_filters,
    load_full_context_from_result,
    search_recent_sessions_only,
    validate_index,
    rebuild_index
)

__all__ = [
    'build_unified_index',
    'update_index_with_new_session',
    'search_all_sessions',
    'search_with_filters',
    'load_full_context_from_result',
    'search_recent_sessions_only',
    'validate_index',
    'rebuild_index'
]

__version__ = '1.0.0'
