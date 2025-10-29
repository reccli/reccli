"""
Unified Vector Index for Cross-Session Context
Individual .devsession files + unified index for searching across all sessions
"""

import json
import time
import shutil
from pathlib import Path
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple, Any
import numpy as np


def build_unified_index(sessions_dir: Path) -> Dict:
    """
    Build unified vector index from all .devsession files

    Args:
        sessions_dir: Directory containing .devsession files

    Returns:
        Complete unified index dictionary
    """
    print("🔍 Building unified vector index...")

    index = {
        'format': 'devsession-index',
        'version': '1.0.0',
        'created_at': datetime.now().isoformat(),
        'last_updated': datetime.now().isoformat(),
        'total_sessions': 0,
        'total_messages': 0,
        'total_vectors': 0,
        'embedding_model': 'sentence-transformers/all-MiniLM-L6-v2',
        'dimensions': 384,
        'distance_metric': 'cosine',
        'unified_vectors': [],
        'session_manifest': [],
        'statistics': {
            'total_duration_hours': 0,
            'total_decisions': 0,
            'total_problems_solved': 0,
            'total_code_changes': 0
        }
    }

    # Get all session files, sorted chronologically
    session_files = sorted(
        sessions_dir.glob('session-*.devsession'),
        key=lambda f: f.name
    )

    if not session_files:
        print("  No session files found")
        return index

    vector_offset = 0

    for session_file in session_files:
        print(f"  Processing {session_file.name}...")

        try:
            # Load session
            with open(session_file, 'r') as f:
                session = json.load(f)

            session_id = session['metadata']['session_id']

            # Extract vectors from conversation
            session_vectors = []
            for msg in session.get('conversation', []):
                if 'embedding' not in msg:
                    continue  # Skip messages without embeddings

                # Determine message type from metadata or summary
                msg_type = _classify_message_type(msg, session.get('summary'))

                # Add to unified index
                session_vectors.append({
                    'id': f"{session_id}_{msg['id']}",
                    'session': session_id,
                    'message_id': msg['id'],
                    'message_index': msg.get('index', 0),
                    'timestamp': msg['timestamp'],
                    'role': msg['role'],
                    'content_preview': msg['content'][:200] if msg.get('content') else '',
                    'embedding': msg['embedding'],
                    'metadata': {
                        'type': msg_type,
                        'summary_ref': _find_summary_ref(msg, session.get('summary')),
                        'tokens': msg.get('metadata', {}).get('tokens', 0)
                    }
                })

            index['unified_vectors'].extend(session_vectors)

            # Add to session manifest
            message_count = len(session_vectors)

            index['session_manifest'].append({
                'session_id': session_id,
                'file': session_file.name,
                'date': session['metadata'].get('created_at', '')[:10],  # YYYY-MM-DD
                'created_at': session['metadata'].get('created_at', ''),
                'duration_seconds': session['metadata'].get('duration_seconds', 0),
                'message_count': message_count,
                'vector_range': {
                    'start': vector_offset,
                    'end': vector_offset + message_count - 1
                },
                'summary': session.get('summary', {}).get('overview', 'No summary'),
                'tags': _extract_tags(session),
                'has_decisions': len(session.get('summary', {}).get('decisions', [])) > 0,
                'has_problems': len(session.get('summary', {}).get('problems_solved', [])) > 0
            })

            # Update statistics
            summary = session.get('summary', {})
            index['statistics']['total_duration_hours'] += session['metadata'].get('duration_seconds', 0) / 3600
            index['statistics']['total_decisions'] += len(summary.get('decisions', []))
            index['statistics']['total_problems_solved'] += len(summary.get('problems_solved', []))
            index['statistics']['total_code_changes'] += len(summary.get('code_changes', []))

            vector_offset += message_count
            index['total_sessions'] += 1

        except Exception as e:
            print(f"  ⚠️  Error processing {session_file.name}: {e}")
            continue

    index['total_messages'] = vector_offset
    index['total_vectors'] = vector_offset

    # Save index
    index_path = sessions_dir / 'index.json'
    with open(index_path, 'w') as f:
        json.dump(index, f, indent=2)

    print(f"✓ Index built: {index['total_sessions']} sessions, {index['total_vectors']} vectors")
    return index


def update_index_with_new_session(sessions_dir: Path, new_session: Dict) -> Dict:
    """
    Add new session to unified index (incremental update)

    Args:
        sessions_dir: Directory containing index and sessions
        new_session: New session dictionary to add

    Returns:
        Updated index dictionary
    """
    print(f"📝 Updating index with {new_session['metadata']['session_id']}...")

    index_path = sessions_dir / 'index.json'

    # Load existing index
    if index_path.exists():
        with open(index_path, 'r') as f:
            index = json.load(f)
    else:
        # First session - build from scratch
        return build_unified_index(sessions_dir)

    session_id = new_session['metadata']['session_id']
    vector_offset = len(index['unified_vectors'])

    # Extract vectors from new session
    new_vectors = []
    for msg in new_session.get('conversation', []):
        if 'embedding' not in msg:
            continue

        msg_type = _classify_message_type(msg, new_session.get('summary'))

        new_vectors.append({
            'id': f"{session_id}_{msg['id']}",
            'session': session_id,
            'message_id': msg['id'],
            'message_index': msg.get('index', 0),
            'timestamp': msg['timestamp'],
            'role': msg['role'],
            'content_preview': msg['content'][:200] if msg.get('content') else '',
            'embedding': msg['embedding'],
            'metadata': {
                'type': msg_type,
                'summary_ref': _find_summary_ref(msg, new_session.get('summary')),
                'tokens': msg.get('metadata', {}).get('tokens', 0)
            }
        })

    # Append to unified vectors
    index['unified_vectors'].extend(new_vectors)

    # Add to manifest
    message_count = len(new_vectors)
    index['session_manifest'].append({
        'session_id': session_id,
        'file': f"{session_id}.devsession",
        'date': new_session['metadata'].get('created_at', '')[:10],
        'created_at': new_session['metadata'].get('created_at', ''),
        'duration_seconds': new_session['metadata'].get('duration_seconds', 0),
        'message_count': message_count,
        'vector_range': {
            'start': vector_offset,
            'end': vector_offset + message_count - 1
        },
        'summary': new_session.get('summary', {}).get('overview', 'No summary'),
        'tags': _extract_tags(new_session),
        'has_decisions': len(new_session.get('summary', {}).get('decisions', [])) > 0,
        'has_problems': len(new_session.get('summary', {}).get('problems_solved', [])) > 0
    })

    # Update metadata
    index['last_updated'] = datetime.now().isoformat()
    index['total_sessions'] += 1
    index['total_messages'] += message_count
    index['total_vectors'] += message_count

    # Update statistics
    summary = new_session.get('summary', {})
    index['statistics']['total_duration_hours'] += new_session['metadata'].get('duration_seconds', 0) / 3600
    index['statistics']['total_decisions'] += len(summary.get('decisions', []))
    index['statistics']['total_problems_solved'] += len(summary.get('problems_solved', []))
    index['statistics']['total_code_changes'] += len(summary.get('code_changes', []))

    # Save updated index
    with open(index_path, 'w') as f:
        json.dump(index, f, indent=2)

    print(f"✓ Index updated: {index['total_sessions']} sessions, {index['total_vectors']} vectors")
    return index


def search_all_sessions(
    project_dir: Path,
    query: str,
    embedding_func,
    top_k: int = 10
) -> List[Dict]:
    """
    Search across all sessions using unified index

    Args:
        project_dir: Project root directory
        query: Search query string
        embedding_func: Function to generate query embedding
        top_k: Number of results to return

    Returns:
        List of search results sorted by relevance
    """
    sessions_dir = project_dir / '.devsessions'
    index_path = sessions_dir / 'index.json'

    if not index_path.exists():
        return []

    # Load index
    with open(index_path, 'r') as f:
        index = json.load(f)

    # Embed query
    query_embedding = embedding_func(query)

    # Calculate similarities
    results = []
    for vector_item in index['unified_vectors']:
        similarity = cosine_similarity(query_embedding, vector_item['embedding'])
        results.append({
            'similarity': float(similarity),
            'session': vector_item['session'],
            'message_id': vector_item['message_id'],
            'message_index': vector_item['message_index'],
            'timestamp': vector_item['timestamp'],
            'content_preview': vector_item['content_preview'],
            'type': vector_item['metadata']['type'],
            'role': vector_item['role']
        })

    # Sort by similarity, take top k
    results = sorted(results, key=lambda x: x['similarity'], reverse=True)[:top_k]

    return results


def search_with_filters(
    project_dir: Path,
    query: str,
    embedding_func,
    filters: Optional[Dict] = None,
    top_k: int = 10
) -> List[Dict]:
    """
    Search with time, session, or type filters

    Args:
        project_dir: Project root directory
        query: Search query string
        embedding_func: Function to generate query embedding
        filters: Dictionary of filters (start_date, end_date, sessions, types, tags)
        top_k: Number of results to return

    Returns:
        List of filtered search results
    """
    sessions_dir = project_dir / '.devsessions'
    index_path = sessions_dir / 'index.json'

    if not index_path.exists():
        return []

    with open(index_path, 'r') as f:
        index = json.load(f)

    query_embedding = embedding_func(query)

    # Apply filters
    filtered_vectors = index['unified_vectors']

    if filters:
        # Filter by date range
        if 'start_date' in filters:
            filtered_vectors = [
                v for v in filtered_vectors
                if v['timestamp'] >= filters['start_date']
            ]

        if 'end_date' in filters:
            filtered_vectors = [
                v for v in filtered_vectors
                if v['timestamp'] <= filters['end_date']
            ]

        # Filter by session
        if 'sessions' in filters:
            filtered_vectors = [
                v for v in filtered_vectors
                if v['session'] in filters['sessions']
            ]

        # Filter by type
        if 'types' in filters:
            filtered_vectors = [
                v for v in filtered_vectors
                if v['metadata']['type'] in filters['types']
            ]

        # Filter by tags
        if 'tags' in filters:
            # Get sessions with matching tags
            matching_sessions = [
                s['session_id'] for s in index['session_manifest']
                if any(tag in s['tags'] for tag in filters['tags'])
            ]
            filtered_vectors = [
                v for v in filtered_vectors
                if v['session'] in matching_sessions
            ]

    # Search filtered vectors
    results = []
    for vector_item in filtered_vectors:
        similarity = cosine_similarity(query_embedding, vector_item['embedding'])
        results.append({
            'similarity': float(similarity),
            'session': vector_item['session'],
            'message_id': vector_item['message_id'],
            'content_preview': vector_item['content_preview'],
            'type': vector_item['metadata']['type'],
            'timestamp': vector_item['timestamp'],
            'role': vector_item['role']
        })

    results = sorted(results, key=lambda x: x['similarity'], reverse=True)[:top_k]
    return results


def search_recent_sessions_only(
    project_dir: Path,
    query: str,
    embedding_func,
    num_sessions: int = 3,
    top_k: int = 10
) -> List[Dict]:
    """
    Search only recent N sessions (fast path)

    Args:
        project_dir: Project root directory
        query: Search query string
        embedding_func: Function to generate query embedding
        num_sessions: Number of recent sessions to search
        top_k: Number of results to return

    Returns:
        List of search results from recent sessions only
    """
    sessions_dir = project_dir / '.devsessions'
    index_path = sessions_dir / 'index.json'

    if not index_path.exists():
        return []

    with open(index_path, 'r') as f:
        index = json.load(f)

    # Get recent sessions
    recent_sessions = sorted(
        index['session_manifest'],
        key=lambda s: s['created_at'],
        reverse=True
    )[:num_sessions]

    # Get vector ranges for recent sessions
    vector_indices = []
    for session in recent_sessions:
        vr = session['vector_range']
        vector_indices.extend(range(vr['start'], vr['end'] + 1))

    # Search only recent vectors
    query_embedding = embedding_func(query)
    results = []

    for idx in vector_indices:
        if idx >= len(index['unified_vectors']):
            continue
        vector_item = index['unified_vectors'][idx]
        similarity = cosine_similarity(query_embedding, vector_item['embedding'])
        results.append({
            'similarity': float(similarity),
            'session': vector_item['session'],
            'message_id': vector_item['message_id'],
            'content_preview': vector_item['content_preview'],
            'type': vector_item['metadata']['type'],
            'role': vector_item['role']
        })

    results = sorted(results, key=lambda x: x['similarity'], reverse=True)[:top_k]
    return results


def load_full_context_from_result(
    project_dir: Path,
    search_result: Dict,
    context_window: int = 5
) -> Dict:
    """
    Given a search result, load full message context from that session

    Args:
        project_dir: Project root directory
        search_result: Search result dictionary with session and message_id
        context_window: Number of messages before/after to include

    Returns:
        Dictionary with full context including message, surrounding messages, and summary
    """
    sessions_dir = project_dir / '.devsessions'

    # Find session file
    session_file = sessions_dir / f"{search_result['session']}.devsession"

    if not session_file.exists():
        return {
            'error': 'session_not_found',
            'message': f'Session file {search_result["session"]}.devsession not found',
            'suggestion': 'Check .devsessions/archive/ directory'
        }

    try:
        with open(session_file, 'r') as f:
            session = json.load(f)

        # Find the specific message
        target_message = None
        for msg in session.get('conversation', []):
            if msg['id'] == search_result['message_id']:
                target_message = msg
                break

        if not target_message:
            return {
                'error': 'message_not_found',
                'message': f'Message {search_result["message_id"]} not found in session'
            }

        # Get surrounding context (chronological range)
        message_index = target_message.get('index', 0)

        # Get messages in range [index - context_window, index + context_window]
        context_start = max(1, message_index - context_window)
        context_end = min(len(session['conversation']), message_index + context_window)

        context_messages = [
            msg for msg in session.get('conversation', [])
            if context_start <= msg.get('index', 0) <= context_end
        ]

        # Check if this message links to a summary item
        summary_context = None
        summary_ref = target_message.get('metadata', {}).get('summary_ref')
        if summary_ref:
            summary = session.get('summary', {})

            # Check decisions
            for decision in summary.get('decisions', []):
                if decision.get('id') == summary_ref:
                    summary_context = {
                        'type': 'decision',
                        'summary': decision.get('decision', ''),
                        'reasoning': decision.get('reasoning', ''),
                        'message_range': decision.get('message_range')
                    }
                    break

            # Check problems_solved
            if not summary_context:
                for problem in summary.get('problems_solved', []):
                    if problem.get('id') == summary_ref:
                        summary_context = {
                            'type': 'problem_solved',
                            'problem': problem.get('problem', ''),
                            'solution': problem.get('solution', ''),
                            'message_range': problem.get('message_range')
                        }
                        break

        return {
            'message': target_message,
            'context_messages': context_messages,
            'summary_context': summary_context,
            'session_metadata': session.get('metadata', {}),
            'session_summary': session.get('summary', {}).get('overview', '')
        }

    except Exception as e:
        return {
            'error': 'load_failed',
            'message': f'Failed to load session: {e}'
        }


def validate_index(sessions_dir: Path) -> Dict:
    """
    Validate index integrity

    Args:
        sessions_dir: Directory containing index

    Returns:
        Validation result dictionary
    """
    index_path = sessions_dir / 'index.json'

    if not index_path.exists():
        return {'valid': False, 'error': 'Index file not found'}

    try:
        with open(index_path, 'r') as f:
            index = json.load(f)
    except json.JSONDecodeError as e:
        return {'valid': False, 'error': f'Invalid JSON: {e}'}

    errors = []
    warnings = []

    # Check format
    if index.get('format') != 'devsession-index':
        errors.append('Invalid format field')

    # Check session files exist
    for session_info in index.get('session_manifest', []):
        session_file = sessions_dir / session_info['file']
        if not session_file.exists():
            warnings.append(f"Session file missing: {session_info['file']}")

    # Check vector counts match
    expected_vectors = sum(s['message_count'] for s in index.get('session_manifest', []))
    actual_vectors = len(index.get('unified_vectors', []))
    if expected_vectors != actual_vectors:
        errors.append(f'Vector count mismatch: expected {expected_vectors}, got {actual_vectors}')

    # Check vector ranges are contiguous
    for i, session_info in enumerate(index.get('session_manifest', [])):
        vr = session_info['vector_range']
        expected_start = sum(s['message_count'] for s in index['session_manifest'][:i])
        if vr['start'] != expected_start:
            errors.append(f"Session {session_info['session_id']}: vector range start mismatch")

    return {
        'valid': len(errors) == 0,
        'errors': errors,
        'warnings': warnings,
        'total_sessions': index.get('total_sessions', 0),
        'total_vectors': index.get('total_vectors', 0)
    }


def rebuild_index(sessions_dir: Path) -> Dict:
    """
    Rebuild index from scratch (use if corrupted or after schema change)

    Args:
        sessions_dir: Directory containing sessions

    Returns:
        New index dictionary
    """
    print("🔧 Rebuilding index from scratch...")

    # Backup old index
    old_index = sessions_dir / 'index.json'
    if old_index.exists():
        backup_path = sessions_dir / f'index.backup.{int(time.time())}.json'
        shutil.copy(old_index, backup_path)
        print(f"  Backed up old index to {backup_path.name}")

    # Build new index
    new_index = build_unified_index(sessions_dir)

    print("✓ Index rebuilt successfully")
    return new_index


# Helper functions

def _classify_message_type(msg: Dict, summary: Optional[Dict]) -> str:
    """Classify message type based on content and summary links"""
    if summary:
        # Check if message is referenced in decisions
        for decision in summary.get('decisions', []):
            if msg['id'] in decision.get('references', []):
                return 'decision'

        # Check if in problems_solved
        for problem in summary.get('problems_solved', []):
            if msg['id'] in problem.get('references', []):
                return 'problem' if msg['role'] == 'user' else 'solution'

        # Check if in code_changes
        for change in summary.get('code_changes', []):
            if msg['id'] in change.get('references', []):
                return 'code_change'

    # Default
    return 'discussion'


def _find_summary_ref(msg: Dict, summary: Optional[Dict]) -> Optional[str]:
    """Find which summary item references this message"""
    if not summary:
        return None

    for decision in summary.get('decisions', []):
        if msg['id'] in decision.get('references', []):
            return decision.get('id')

    for problem in summary.get('problems_solved', []):
        if msg['id'] in problem.get('references', []):
            return problem.get('id')

    for change in summary.get('code_changes', []):
        if msg['id'] in change.get('references', []):
            return change.get('id')

    return None


def _extract_tags(session: Dict) -> List[str]:
    """Extract relevant tags from session"""
    tags = set()

    # From summary
    summary = session.get('summary', {})
    overview = summary.get('overview', '').lower()

    # Common tech keywords
    keywords = [
        'stripe', 'payment', 'webhook', 'auth', 'database',
        'api', 'frontend', 'backend', 'testing', 'deployment',
        'react', 'python', 'javascript', 'node', 'django',
        'flask', 'fastapi', 'postgres', 'redis', 'mongodb'
    ]

    for keyword in keywords:
        if keyword in overview:
            tags.add(keyword)

    # From decisions
    for decision in summary.get('decisions', []):
        decision_text = decision.get('decision', '').lower()
        for keyword in keywords:
            if keyword in decision_text:
                tags.add(keyword)

    return sorted(list(tags))


def cosine_similarity(vec1: List[float], vec2: List[float]) -> float:
    """
    Calculate cosine similarity between two vectors

    Args:
        vec1: First vector
        vec2: Second vector

    Returns:
        Cosine similarity score (0-1)
    """
    vec1 = np.array(vec1)
    vec2 = np.array(vec2)

    dot_product = np.dot(vec1, vec2)
    norm1 = np.linalg.norm(vec1)
    norm2 = np.linalg.norm(vec2)

    if norm1 == 0 or norm2 == 0:
        return 0.0

    return float(dot_product / (norm1 * norm2))
