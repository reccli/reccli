"""
Unified Vector Index for RecCli Phase 5

Builds and maintains a unified index of embeddings across all devsession files.
Supports hybrid retrieval (dense + sparse), temporal indexing, and incremental updates.
"""

from typing import List, Dict, Optional
from pathlib import Path
from datetime import datetime
import json
import hashlib


def classify_message_type(msg: Dict, summary: Optional[Dict]) -> str:
    """
    Classify message type based on content and summary linkage

    Returns:
        Message kind: decision | code | problem | note | log | doc
    """
    content = msg.get('content', '').lower()
    role = msg.get('role', '')

    # Check if linked to summary
    if summary:
        # Check if message is referenced in decisions
        for dec in summary.get('decisions', []):
            if msg.get('id') in dec.get('message_ids', []):
                return 'decision'

        # Check if in problems_solved
        for prob in summary.get('problems_solved', []):
            if msg.get('id') in prob.get('message_ids', []):
                return 'problem'

        # Check if in code_changes
        for change in summary.get('code_changes', []):
            if msg.get('id') in change.get('message_ids', []):
                return 'code'

    # Heuristic classification
    if role == 'user':
        # User messages often ask questions (problems)
        if '?' in content or any(word in content for word in ['error', 'issue', 'problem', 'bug', 'help']):
            return 'problem'
        elif any(word in content for word in ['should', 'recommend', 'approach', 'decision']):
            return 'decision'
        else:
            return 'note'

    elif role == 'assistant':
        # Assistant messages
        if any(word in content for word in ['i recommend', 'we should', 'decision', 'approach', 'strategy']):
            return 'decision'
        elif any(word in content for word in ['fixed', 'solved', 'resolved', 'issue']):
            return 'problem'
        elif any(word in content for word in ['```', 'def ', 'class ', 'function', 'import ']):
            return 'code'
        elif any(word in content for word in ['documentation', 'readme', 'guide']):
            return 'doc'
        else:
            return 'note'

    # Default
    return 'log'


def find_summary_ref(msg: Dict, summary: Optional[Dict]) -> Optional[str]:
    """
    Find summary item reference for this message

    Returns:
        Summary item ID (e.g., "dec_001") or None
    """
    if not summary:
        return None

    msg_id = msg.get('id')
    if not msg_id:
        return None

    # Check decisions
    for i, dec in enumerate(summary.get('decisions', [])):
        if msg_id in dec.get('message_ids', []):
            return f"dec_{i:03d}"

    # Check problems
    for i, prob in enumerate(summary.get('problems_solved', [])):
        if msg_id in prob.get('message_ids', []):
            return f"prob_{i:03d}"

    # Check code changes
    for i, change in enumerate(summary.get('code_changes', [])):
        if msg_id in change.get('message_ids', []):
            return f"code_{i:03d}"

    return None


def extract_tags(session) -> List[str]:
    """
    Extract tags from session metadata and summary

    Returns:
        List of tags/keywords
    """
    tags = []

    # From metadata
    if hasattr(session, 'metadata') and session.metadata:
        meta_tags = session.metadata.get('tags', [])
        if meta_tags:
            tags.extend(meta_tags)

    # From summary
    if hasattr(session, 'summary') and session.summary:
        # Extract from overview
        overview = session.summary.get('overview', '')
        # Simple keyword extraction (can be improved)
        common_words = {'the', 'a', 'an', 'and', 'or', 'but', 'in', 'on', 'at', 'to', 'for'}
        words = overview.lower().split()
        keywords = [w.strip('.,!?') for w in words if len(w) > 4 and w not in common_words]
        tags.extend(keywords[:5])  # Top 5 keywords

    # Deduplicate
    return list(set(tags))


def compute_text_hash(text: str) -> str:
    """
    Compute hash of text for caching

    Uses blake3 if available, otherwise sha256
    """
    try:
        import blake3
        return f"blake3:{blake3.blake3(text.encode()).hexdigest()[:16]}"
    except ImportError:
        return f"sha256:{hashlib.sha256(text.encode()).hexdigest()[:16]}"


def count_tokens(text: str) -> int:
    """
    Estimate token count (rough approximation)

    Uses simple word count * 1.3 heuristic
    """
    words = text.split()
    return int(len(words) * 1.3)


def build_unified_index(sessions_dir: Path, verbose: bool = True) -> Dict:
    """
    Build unified vector index from all .devsession files

    Args:
        sessions_dir: Path to directory containing .devsession files
        verbose: Print progress messages

    Returns:
        Index dictionary
    """
    if verbose:
        print("🔍 Building unified vector index...")

    index = {
        'format': 'devsession-index',
        'version': '1.1.0',
        'created_at': datetime.now().isoformat(),
        'last_updated': datetime.now().isoformat(),
        'total_sessions': 0,
        'total_messages': 0,
        'total_vectors': 0,
        'embedding': {
            'provider': 'openai',
            'model': 'text-embedding-3-small',
            'dimensions': 1536,
            'distance_metric': 'cosine'
        },
        'unified_vectors': [],
        'session_manifest': [],
        'statistics': {
            'total_duration_hours': 0.0,
            'average_session_length_minutes': 0,
            'most_active_days': [],
            'total_decisions': 0,
            'total_problems_solved': 0,
            'total_code_changes': 0
        }
    }

    # Get all session files, sorted chronologically
    session_files = sorted(
        sessions_dir.glob('*.devsession'),
        key=lambda f: f.name
    )

    if not session_files:
        if verbose:
            print("⚠️  No .devsession files found")
        return index

    vector_offset = 0
    total_duration = 0
    day_counts = {}

    for session_file in session_files:
        if verbose:
            print(f"  Processing {session_file.name}...")

        # Load session
        from .devsession import DevSession
        try:
            session = DevSession.load(session_file)
        except Exception as e:
            if verbose:
                print(f"    ⚠️  Failed to load: {e}")
            continue

        session_id = session_file.stem

        # Extract embedding metadata from first message (if exists)
        embed_model = 'unknown'
        embed_provider = 'unknown'
        embed_dim = 0

        if session.conversation and len(session.conversation) > 0:
            first_msg = session.conversation[0]
            if 'embed_model' in first_msg:
                embed_model = first_msg['embed_model']
                embed_provider = first_msg.get('embed_provider', 'unknown')
                embed_dim = first_msg.get('embed_dim', 0)

                # Update index embedding metadata
                index['embedding']['model'] = embed_model
                index['embedding']['provider'] = embed_provider
                index['embedding']['dimensions'] = embed_dim

        # Extract vectors from conversation
        message_count = 0
        for msg_idx, msg in enumerate(session.conversation):
            if 'embedding' not in msg:
                continue

            # Classify message type
            msg_type = classify_message_type(msg, session.summary)

            # Get timestamp
            timestamp = msg.get('timestamp', '')
            if not timestamp and session.metadata:
                # Fallback to session start time
                timestamp = session.metadata.get('created_at', '')

            # Extract temporal fields
            t_day = timestamp[:10] if len(timestamp) >= 10 else ''
            t_hour = timestamp[:13] if len(timestamp) >= 13 else ''

            # Add to unified index
            index['unified_vectors'].append({
                'id': f"{session_id}_{msg.get('id', msg_idx)}",
                'session': session_id,
                'message_id': msg.get('id', f'msg_{msg_idx}'),
                'message_index': msg_idx,
                'timestamp': timestamp,

                # Temporal
                'section': session.metadata.get('section', 'default') if session.metadata else 'default',
                'episode_id': 0,  # TODO: Implement episode detection
                't_start': timestamp,
                't_end': timestamp,
                't_day': t_day,
                't_hour': t_hour,

                # Content
                'role': msg.get('role', 'unknown'),
                'kind': msg_type,
                'content_preview': msg['content'][:200] if len(msg['content']) > 200 else msg['content'],
                'text_hash': msg.get('text_hash', compute_text_hash(msg['content'])),

                # Embedding
                'embedding': msg['embedding'],
                'embed_model': msg.get('embed_model', embed_model),
                'embed_provider': msg.get('embed_provider', embed_provider),
                'embed_dim': msg.get('embed_dim', embed_dim),
                'embed_ts': msg.get('embed_ts', ''),

                # Metadata
                'metadata': {
                    'summary_ref': find_summary_ref(msg, session.summary),
                    'tokens': count_tokens(msg['content'])
                }
            })

            message_count += 1

        # Get session duration
        duration = 0
        if hasattr(session, 'get_duration'):
            try:
                duration = session.get_duration()
            except:
                pass

        # Track day activity
        if session.metadata:
            created_at = session.metadata.get('created_at', '')
            if created_at:
                day = created_at[:10]
                day_counts[day] = day_counts.get(day, 0) + 1

        # Add to session manifest
        index['session_manifest'].append({
            'session_id': session_id,
            'file': session_file.name,
            'date': session.metadata.get('created_at', '')[:10] if session.metadata else '',
            'created_at': session.metadata.get('created_at', '') if session.metadata else '',
            'duration_seconds': duration,
            'message_count': message_count,
            'vector_range': {
                'start': vector_offset,
                'end': vector_offset + message_count - 1
            },
            'summary': session.summary.get('overview', 'No summary') if session.summary else 'No summary',
            'tags': extract_tags(session),
            'has_decisions': len(session.summary.get('decisions', [])) > 0 if session.summary else False,
            'has_problems': len(session.summary.get('problems_solved', [])) > 0 if session.summary else False
        })

        # Update statistics
        total_duration += duration
        if session.summary:
            index['statistics']['total_decisions'] += len(session.summary.get('decisions', []))
            index['statistics']['total_problems_solved'] += len(session.summary.get('problems_solved', []))
            index['statistics']['total_code_changes'] += len(session.summary.get('code_changes', []))

        vector_offset += message_count
        index['total_sessions'] += 1

    # Finalize statistics
    index['total_messages'] = vector_offset
    index['total_vectors'] = vector_offset
    index['statistics']['total_duration_hours'] = round(total_duration / 3600, 2)

    if index['total_sessions'] > 0:
        index['statistics']['average_session_length_minutes'] = round(total_duration / index['total_sessions'] / 60)

    # Most active days (top 5)
    most_active = sorted(day_counts.items(), key=lambda x: x[1], reverse=True)[:5]
    index['statistics']['most_active_days'] = [day for day, _ in most_active]

    # Save index
    index_path = sessions_dir / 'index.json'
    with open(index_path, 'w') as f:
        json.dump(index, f, indent=2)

    if verbose:
        print(f"✅ Index built: {index['total_sessions']} sessions, {index['total_vectors']} vectors")
        print(f"   Saved to: {index_path}")

    return index


def update_index_with_new_session(sessions_dir: Path, session_file: Path, verbose: bool = True) -> Dict:
    """
    Add new session to unified index (incremental update)

    Args:
        sessions_dir: Path to .devsessions directory
        session_file: Path to new .devsession file
        verbose: Print progress messages

    Returns:
        Updated index dictionary
    """
    if verbose:
        print(f"📝 Updating index with {session_file.stem}...")

    index_path = sessions_dir / 'index.json'

    # Load existing index
    if index_path.exists():
        with open(index_path, 'r') as f:
            index = json.load(f)
    else:
        # First session - build from scratch
        if verbose:
            print("   No existing index found, building from scratch...")
        return build_unified_index(sessions_dir, verbose=verbose)

    # Check if session already in index
    session_id = session_file.stem
    existing_sessions = [s['session_id'] for s in index['session_manifest']]
    if session_id in existing_sessions:
        if verbose:
            print(f"   ⚠️  Session {session_id} already in index, skipping...")
        return index

    # Load new session
    from .devsession import DevSession
    try:
        session = DevSession.load(session_file)
    except Exception as e:
        if verbose:
            print(f"   ⚠️  Failed to load: {e}")
        return index

    vector_offset = len(index['unified_vectors'])

    # Extract embedding metadata
    embed_model = index['embedding']['model']
    embed_provider = index['embedding']['provider']
    embed_dim = index['embedding']['dimensions']

    if session.conversation and len(session.conversation) > 0:
        first_msg = session.conversation[0]
        if 'embed_model' in first_msg:
            embed_model = first_msg['embed_model']
            embed_provider = first_msg.get('embed_provider', embed_provider)
            embed_dim = first_msg.get('embed_dim', embed_dim)

    # Extract vectors from new session
    message_count = 0
    for msg_idx, msg in enumerate(session.conversation):
        if 'embedding' not in msg:
            continue

        # Classify message type
        msg_type = classify_message_type(msg, session.summary)

        # Get timestamp
        timestamp = msg.get('timestamp', '')
        if not timestamp and session.metadata:
            timestamp = session.metadata.get('created_at', '')

        # Extract temporal fields
        t_day = timestamp[:10] if len(timestamp) >= 10 else ''
        t_hour = timestamp[:13] if len(timestamp) >= 13 else ''

        # Add to unified index
        index['unified_vectors'].append({
            'id': f"{session_id}_{msg.get('id', msg_idx)}",
            'session': session_id,
            'message_id': msg.get('id', f'msg_{msg_idx}'),
            'message_index': msg_idx,
            'timestamp': timestamp,

            # Temporal
            'section': session.metadata.get('section', 'default') if session.metadata else 'default',
            'episode_id': 0,
            't_start': timestamp,
            't_end': timestamp,
            't_day': t_day,
            't_hour': t_hour,

            # Content
            'role': msg.get('role', 'unknown'),
            'kind': msg_type,
            'content_preview': msg['content'][:200] if len(msg['content']) > 200 else msg['content'],
            'text_hash': msg.get('text_hash', compute_text_hash(msg['content'])),

            # Embedding
            'embedding': msg['embedding'],
            'embed_model': msg.get('embed_model', embed_model),
            'embed_provider': msg.get('embed_provider', embed_provider),
            'embed_dim': msg.get('embed_dim', embed_dim),
            'embed_ts': msg.get('embed_ts', ''),

            # Metadata
            'metadata': {
                'summary_ref': find_summary_ref(msg, session.summary),
                'tokens': count_tokens(msg['content'])
            }
        })

        message_count += 1

    # Get session duration
    duration = 0
    if hasattr(session, 'get_duration'):
        try:
            duration = session.get_duration()
        except:
            pass

    # Add to session manifest
    index['session_manifest'].append({
        'session_id': session_id,
        'file': session_file.name,
        'date': session.metadata.get('created_at', '')[:10] if session.metadata else '',
        'created_at': session.metadata.get('created_at', '') if session.metadata else '',
        'duration_seconds': duration,
        'message_count': message_count,
        'vector_range': {
            'start': vector_offset,
            'end': vector_offset + message_count - 1
        },
        'summary': session.summary.get('overview', 'No summary') if session.summary else 'No summary',
        'tags': extract_tags(session),
        'has_decisions': len(session.summary.get('decisions', [])) > 0 if session.summary else False,
        'has_problems': len(session.summary.get('problems_solved', [])) > 0 if session.summary else False
    })

    # Update index metadata
    index['last_updated'] = datetime.now().isoformat()
    index['total_sessions'] += 1
    index['total_messages'] += message_count
    index['total_vectors'] += message_count

    # Update statistics
    index['statistics']['total_duration_hours'] = round(
        index['statistics']['total_duration_hours'] + (duration / 3600), 2
    )

    if session.summary:
        index['statistics']['total_decisions'] += len(session.summary.get('decisions', []))
        index['statistics']['total_problems_solved'] += len(session.summary.get('problems_solved', []))
        index['statistics']['total_code_changes'] += len(session.summary.get('code_changes', []))

    # Recalculate average session length
    if index['total_sessions'] > 0:
        total_duration = index['statistics']['total_duration_hours'] * 3600
        index['statistics']['average_session_length_minutes'] = round(
            total_duration / index['total_sessions'] / 60
        )

    # Update most active days
    day_counts = {}
    for manifest in index['session_manifest']:
        day = manifest['date']
        if day:
            day_counts[day] = day_counts.get(day, 0) + 1

    most_active = sorted(day_counts.items(), key=lambda x: x[1], reverse=True)[:5]
    index['statistics']['most_active_days'] = [day for day, _ in most_active]

    # Save updated index
    with open(index_path, 'w') as f:
        json.dump(index, f, indent=2)

    if verbose:
        print(f"✅ Index updated: {index['total_sessions']} sessions, {index['total_vectors']} vectors")

    return index


def validate_index(sessions_dir: Path, verbose: bool = True) -> List[str]:
    """
    Validate index integrity

    Args:
        sessions_dir: Path to .devsessions directory
        verbose: Print validation messages

    Returns:
        List of validation errors (empty if valid)
    """
    errors = []

    index_path = sessions_dir / 'index.json'

    if not index_path.exists():
        errors.append("Index file not found")
        return errors

    # Load index
    try:
        with open(index_path, 'r') as f:
            index = json.load(f)
    except Exception as e:
        errors.append(f"Failed to load index: {e}")
        return errors

    if verbose:
        print("🔍 Validating index...")

    # Check format
    if index.get('format') != 'devsession-index':
        errors.append(f"Invalid format: {index.get('format')}")

    # Check version
    if index.get('version') not in ['1.0.0', '1.1.0']:
        errors.append(f"Unknown version: {index.get('version')}")

    # Check all referenced session files exist
    for manifest in index.get('session_manifest', []):
        session_file = sessions_dir / manifest['file']
        if not session_file.exists():
            errors.append(f"Missing session file: {manifest['file']}")

    # Check vector count matches
    expected_vectors = index.get('total_vectors', 0)
    actual_vectors = len(index.get('unified_vectors', []))
    if expected_vectors != actual_vectors:
        errors.append(f"Vector count mismatch: expected {expected_vectors}, found {actual_vectors}")

    # Check session count matches
    expected_sessions = index.get('total_sessions', 0)
    actual_sessions = len(index.get('session_manifest', []))
    if expected_sessions != actual_sessions:
        errors.append(f"Session count mismatch: expected {expected_sessions}, found {actual_sessions}")

    # Check vector ranges are contiguous
    manifests = sorted(index.get('session_manifest', []), key=lambda m: m['vector_range']['start'])
    for i, manifest in enumerate(manifests):
        expected_start = sum(m['message_count'] for m in manifests[:i])
        actual_start = manifest['vector_range']['start']
        if expected_start != actual_start:
            errors.append(f"Vector range gap at session {manifest['session_id']}: expected start {expected_start}, found {actual_start}")

    if verbose:
        if errors:
            print(f"❌ Validation failed with {len(errors)} errors:")
            for error in errors:
                print(f"   - {error}")
        else:
            print("✅ Index is valid")

    return errors


def get_index_stats(sessions_dir: Path) -> Dict:
    """
    Get index statistics

    Args:
        sessions_dir: Path to .devsessions directory

    Returns:
        Statistics dictionary
    """
    index_path = sessions_dir / 'index.json'

    if not index_path.exists():
        return {
            'exists': False,
            'error': 'Index not found'
        }

    try:
        with open(index_path, 'r') as f:
            index = json.load(f)
    except Exception as e:
        return {
            'exists': True,
            'error': f'Failed to load: {e}'
        }

    return {
        'exists': True,
        'format': index.get('format'),
        'version': index.get('version'),
        'created_at': index.get('created_at'),
        'last_updated': index.get('last_updated'),
        'total_sessions': index.get('total_sessions', 0),
        'total_messages': index.get('total_messages', 0),
        'total_vectors': index.get('total_vectors', 0),
        'embedding': index.get('embedding', {}),
        'statistics': index.get('statistics', {})
    }
