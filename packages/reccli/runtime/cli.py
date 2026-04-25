"""
CLI - Command-line interface for RecCli
"""

import sys
import argparse
import json
from pathlib import Path
from datetime import datetime
from typing import Optional

from ..recording.recorder import watch_terminals
from ..recording.wal_recorder import record_session_wal
from ..session.devsession import DevSession
from .llm import chat_session, one_shot_query
from .config import Config
from ..retrieval.vector_index import (
    build_unified_index,
    update_index_with_new_session,
    validate_index,
    get_index_stats
)
from ..retrieval.search import search, expand_result
from ..project.devproject import (
    DevProjectManager,
    canonical_devproject_path,
    default_devsession_path,
    discover_project_root,
    resolve_session_project_root,
)


def cmd_record(args):
    """Record a new terminal session"""
    config = Config()

    # Determine output path
    if args.output:
        output_path = Path(args.output)
    else:
        output_path = default_devsession_path(Path.cwd())

    # Start recording using WAL pattern
    exit_code = record_session_wal(output_path, shell=args.shell)
    return exit_code


def cmd_list(args):
    """List recorded sessions"""
    config = Config()
    sessions_dir = config.get_sessions_dir()

    if not sessions_dir.exists():
        print("No sessions found. Record your first session with: reccli record")
        return 0

    # Find all .devsession files
    sessions = sorted(sessions_dir.glob('*.devsession'), key=lambda p: p.stat().st_mtime, reverse=True)

    if not sessions:
        print("No sessions found. Record your first session with: reccli record")
        return 0

    print(f"\n📁 Sessions in {sessions_dir}\n")
    print(f"{'Name':<30} {'Duration':<12} {'Events':<10} {'Created':<20}")
    print("-" * 75)

    for session_path in sessions:
        try:
            session = DevSession.load(session_path)
            duration = session.get_duration()
            event_count = session.get_event_count()
            created = datetime.fromisoformat(session.created).strftime('%Y-%m-%d %H:%M')

            # Format duration
            if duration < 60:
                duration_str = f"{duration:.1f}s"
            elif duration < 3600:
                duration_str = f"{duration/60:.1f}m"
            else:
                duration_str = f"{duration/3600:.1f}h"

            print(f"{session_path.stem:<30} {duration_str:<12} {event_count:<10} {created:<20}")
        except Exception as e:
            print(f"{session_path.stem:<30} {'ERROR':<12} {'-':<10} {str(e)[:20]:<20}")

    print()
    return 0


def cmd_show(args):
    """Show details of a session"""
    session_path = Path(args.session)

    # If not absolute path, look in sessions directory
    if not session_path.is_absolute():
        config = Config()
        sessions_dir = config.get_sessions_dir()
        if not session_path.suffix:
            session_path = sessions_dir / f"{session_path}.devsession"
        else:
            session_path = sessions_dir / session_path

    if not session_path.exists():
        print(f"❌ Session not found: {session_path}", file=sys.stderr)
        return 1

    try:
        session = DevSession.load(session_path)
    except Exception as e:
        print(f"❌ Error loading session: {e}", file=sys.stderr)
        return 1

    # Display session info
    print(f"\n📋 Session: {session.session_id}")
    print(f"   File: {session_path}")
    print(f"   Created: {session.created}")
    print(f"   Updated: {session.updated}")
    print(f"\n🖥️  Terminal Recording:")
    print(f"   Size: {session.terminal_recording['width']}x{session.terminal_recording['height']}")
    print(f"   Shell: {session.terminal_recording['shell']}")
    print(f"   Events: {session.get_event_count()}")
    print(f"   Duration: {session.get_duration():.1f}s")

    if session.conversation:
        print(f"\n💬 Conversation: {len(session.conversation)} messages")

    if session.summary:
        print(f"\n📝 Summary: Available")

    if session.vector_index:
        print(f"\n🔍 Vector Index: {session.vector_index.get('dimensions', 'N/A')} dimensions")

    if session.compaction_history:
        print(f"\n🗜️  Compaction History: {len(session.compaction_history)} compactions")

    print()
    return 0


def cmd_export(args):
    """Export session to different format"""
    session_path = Path(args.session)

    # If not absolute path, look in sessions directory
    if not session_path.is_absolute():
        config = Config()
        sessions_dir = config.get_sessions_dir()
        if not session_path.suffix:
            session_path = sessions_dir / f"{session_path}.devsession"
        else:
            session_path = sessions_dir / session_path

    if not session_path.exists():
        print(f"❌ Session not found: {session_path}", file=sys.stderr)
        return 1

    try:
        session = DevSession.load(session_path)
    except Exception as e:
        print(f"❌ Error loading session: {e}", file=sys.stderr)
        return 1

    # Determine output path
    if args.output:
        output_path = Path(args.output)
    else:
        output_path = session_path.with_suffix(f'.{args.format}')

    # Export based on format
    if args.format == 'txt':
        _export_txt(session, output_path)
    elif args.format == 'md':
        _export_md(session, output_path)
    else:
        print(f"❌ Unknown format: {args.format}", file=sys.stderr)
        return 1

    print(f"✓ Exported to {output_path}")
    return 0


def _export_txt(session: DevSession, output_path: Path):
    """Export to plain text"""
    with open(output_path, 'w', encoding='utf-8') as f:
        for timestamp, event_type, data in session.terminal_recording['events']:
            if event_type == 'o':  # Only output events
                f.write(data)


def _export_md(session: DevSession, output_path: Path):
    """Export to markdown"""
    with open(output_path, 'w', encoding='utf-8') as f:
        f.write(f"# {session.session_id}\n\n")
        f.write(f"**Created**: {session.created}\n")
        f.write(f"**Duration**: {session.get_duration():.1f}s\n")
        f.write(f"**Events**: {session.get_event_count()}\n\n")
        f.write("## Terminal Output\n\n```\n")

        for timestamp, event_type, data in session.terminal_recording['events']:
            if event_type == 'o':  # Only output events
                f.write(data)

        f.write("\n```\n")


def cmd_chat(args):
    """Start interactive chat with LLM"""
    from .chat_ui import launch_typescript_ui

    config = Config()

    # Determine model
    model = args.model or config.get_default_model()

    # Normalize model alias
    if model == 'claude':
        model = 'claude-sonnet'

    # Determine session name
    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    name = args.name or f"session_{timestamp}"

    # Launch TypeScript UI
    launch_typescript_ui(model, name)
    return 0


def cmd_ask(args):
    """Ask a single question (one-shot mode)"""
    config = Config()

    # Determine model
    model = args.model or config.get_default_model()

    # Determine output path
    if args.output:
        output_path = Path(args.output)
    else:
        output_path = default_devsession_path(Path.cwd())

    # Ask question
    response = one_shot_query(model, args.question, output_path, api_key=args.api_key)
    print(f"\n{model.title()}: {response}\n")
    return 0


def cmd_config(args):
    """Manage configuration"""
    config = Config()

    # Set API keys
    if args.anthropic_key:
        config.set_api_key('anthropic', args.anthropic_key)
        print("✓ Anthropic API key saved")

    if args.openai_key:
        config.set_api_key('openai', args.openai_key)
        print("✓ OpenAI API key saved")

    # Set default model
    if args.default_model:
        config.set_default_model(args.default_model)
        print(f"✓ Default model set to: {args.default_model}")

    # Show current config
    if not any([args.anthropic_key, args.openai_key, args.default_model]):
        print("\n📋 Current Configuration\n")
        print(f"Sessions directory: {config.get_sessions_dir()}")
        print(f"Default model: {config.get_default_model()}")
        print(f"\nAPI Keys:")
        print(f"  Anthropic: {'✓ Set' if config.get_api_key('anthropic') else '✗ Not set'}")
        print(f"  OpenAI: {'✓ Set' if config.get_api_key('openai') else '✗ Not set'}")
        print()

    return 0


def _resolve_session_path(session_arg: str) -> Path:
    session_path = Path(session_arg)
    if not session_path.is_absolute():
        config = Config()
        sessions_dir = config.get_sessions_dir()
        if not session_path.suffix:
            session_path = sessions_dir / f"{session_path}.devsession"
        else:
            session_path = sessions_dir / session_path
    return session_path


def _resolve_project_root_arg(project_root_arg: str = None) -> Optional[Path]:
    if project_root_arg:
        return Path(project_root_arg).expanduser().resolve()
    return discover_project_root(Path.cwd())


def cmd_watch(args):
    """Watch for new terminal windows and auto-launch GUI"""
    return watch_terminals()


def cmd_project_show(args):
    """Show .devproject summary or raw JSON."""
    project_root = _resolve_project_root_arg(args.project_root)
    if project_root is None:
        print("❌ No project root found (.git or .devproject)", file=sys.stderr)
        return 1

    manager = DevProjectManager(project_root)
    document = manager.load_or_create()

    if args.json:
        print(json.dumps(document, indent=2, ensure_ascii=False))
        return 0

    print(f"\n📘 Project Dashboard: {canonical_devproject_path(project_root)}\n")
    project = document.get("project", {})
    print(f"Name: {project.get('name', project_root.name)}")
    print(f"Status: {project.get('status', 'unknown')}")
    print(f"Features: {len(document.get('features', []))}")
    print(f"Sessions linked: {len(document.get('session_index', []))}")
    print(f"Pending proposals: {len(document.get('proposals', []))}")
    print()

    for feature in document.get("features", []):
        print(f"- {feature.get('feature_id')}: {feature.get('title')}")
        print(f"  Status: {feature.get('status')} | Version: {feature.get('feature_version', 1)}")
        print(f"  Files touched: {len(feature.get('files_touched', []))}")
        print(f"  Sessions: {len(feature.get('session_ids', []))}")
    print()
    return 0


def cmd_project_init(args):
    """Initialize .devproject from the existing codebase."""
    project_root = _resolve_project_root_arg(args.project_root) or Path.cwd().resolve()
    manager = DevProjectManager(project_root)
    project_context = (args.description or "").strip() or manager.suggest_init_project_context()

    if not project_context and sys.stdin.isatty():
        try:
            project_context = input("Describe your project in 1-2 sentences: ").strip() or None
        except EOFError:
            project_context = None

    try:
        document = manager.initialize_from_codebase(
            force=args.force,
            model=args.model,
            project_context=project_context,
        )
    except ValueError as e:
        print(f"❌ {e}", file=sys.stderr)
        return 1
    except RuntimeError as e:
        print(f"❌ {e}", file=sys.stderr)
        return 1

    print(f"\n✅ Initialized .devproject from codebase: {manager.path}\n")
    print(f"Project: {document['project'].get('name', project_root.name)}")
    print(f"Features detected: {len(document.get('features', []))}")
    print()
    for feature in document.get("features", [])[:10]:
        print(f"- {feature.get('feature_id')}: {feature.get('title')} ({len(feature.get('files_touched', []))} files)")
    if len(document.get("features", [])) > 10:
        print(f"... and {len(document['features']) - 10} more")
    print()

    if sys.stdin.isatty() and not args.no_review:
        try:
            missing = input("Are any features missing from this list? Leave blank if not: ").strip()
        except EOFError:
            missing = ""
        if missing:
            try:
                document = manager.initialize_from_codebase(
                    force=True,
                    model=args.model,
                    project_context=project_context,
                    missing_feature_hints=[missing],
                )
            except RuntimeError as e:
                print(f"❌ {e}", file=sys.stderr)
                return 1

            print("\n🔁 Rebuilt .devproject with missing-feature hint:\n")
            print(f"Project: {document['project'].get('name', project_root.name)}")
            print(f"Features detected: {len(document.get('features', []))}")
            print()
            for feature in document.get("features", [])[:10]:
                print(f"- {feature.get('feature_id')}: {feature.get('title')} ({len(feature.get('files_touched', []))} files)")
            if len(document.get("features", [])) > 10:
                print(f"... and {len(document['features']) - 10} more")
            print()
    return 0


def cmd_project_sync(args):
    """Scan the codebase and propose .devproject updates."""
    project_root = _resolve_project_root_arg(args.project_root)
    if project_root is None:
        print("❌ No project root found (.git or .devproject)", file=sys.stderr)
        return 1

    manager = DevProjectManager(project_root)
    document, proposal = manager.generate_sync_proposal_from_codebase()

    if proposal is None:
        print("\n✅ .devproject already matches the scanned codebase.\n")
        return 0

    print(f"\n📝 Proposed codebase sync update: {proposal['proposal_id']}\n")
    for op in proposal.get("diff", []):
        print(json.dumps(op, indent=2, ensure_ascii=False))

    if args.apply:
        _, accepted = manager.apply_proposal(proposal["proposal_id"])
        print(f"\n✅ Applied proposal: {accepted['proposal_id']}\n")
    else:
        print(f"\nUse 'reccli project apply {proposal['proposal_id']}' to accept it.\n")

    return 0


def cmd_project_proposals(args):
    """List pending .devproject proposals."""
    project_root = _resolve_project_root_arg(args.project_root)
    if project_root is None:
        print("❌ No project root found (.git or .devproject)", file=sys.stderr)
        return 1

    manager = DevProjectManager(project_root)
    document = manager.load_or_create()
    proposals = document.get("proposals", [])

    if not proposals:
        print("\nNo pending .devproject proposals.\n")
        return 0

    print(f"\n📝 Pending .devproject proposals for {project_root}\n")
    for proposal in proposals:
        print(f"{proposal['proposal_id']} [{proposal.get('status', 'pending')}]")
        print(f"  Source session: {proposal.get('source_session_id')}")
        for op in proposal.get("diff", []):
            print(f"  - {op.get('op')}")
        print()
    return 0


def cmd_project_update(args):
    """Generate a .devproject proposal from a session."""
    from ..summarization.summarizer import SessionSummarizer

    session_path = _resolve_session_path(args.session)
    if not session_path.exists():
        print(f"❌ Session not found: {session_path}", file=sys.stderr)
        return 1

    session = DevSession.load(session_path)
    project_root = _resolve_project_root_arg(args.project_root) or resolve_session_project_root(session, Path.cwd())
    if project_root is None:
        print("❌ No project root found for this session", file=sys.stderr)
        return 1

    if not session.summary:
        summarizer = SessionSummarizer(llm_client=None)
        session.summary = summarizer.summarize_session(session.conversation, redact_secrets=False)
        session.save(session_path, skip_validation=True)

    manager = DevProjectManager(project_root)
    _, proposal = manager.generate_proposal_for_session(session, session_path)

    if proposal is None:
        print("\n✅ .devproject already in sync.\n")
        return 0

    print(f"\n📝 Proposed .devproject update: {proposal['proposal_id']}\n")
    for op in proposal.get("diff", []):
        print(json.dumps(op, indent=2, ensure_ascii=False))

    if args.apply:
        _, accepted = manager.apply_proposal(proposal["proposal_id"])
        print(f"\n✅ Applied proposal: {accepted['proposal_id']}\n")
    else:
        print(f"\nUse 'reccli project apply {proposal['proposal_id']}' to accept it.\n")

    return 0


def cmd_project_apply(args):
    """Apply a pending .devproject proposal."""
    project_root = _resolve_project_root_arg(args.project_root)
    if project_root is None:
        print("❌ No project root found (.git or .devproject)", file=sys.stderr)
        return 1

    manager = DevProjectManager(project_root)
    _, proposal = manager.apply_proposal(args.proposal_id)
    print(f"\n✅ Applied .devproject proposal: {proposal['proposal_id']}\n")
    return 0


def cmd_project_reject(args):
    """Reject a pending .devproject proposal."""
    project_root = _resolve_project_root_arg(args.project_root)
    if project_root is None:
        print("❌ No project root found (.git or .devproject)", file=sys.stderr)
        return 1

    manager = DevProjectManager(project_root)
    _, proposal = manager.reject_proposal(args.proposal_id, reason=args.reason)
    print(f"\n✅ Rejected .devproject proposal: {proposal['proposal_id']}\n")
    return 0


def cmd_index_build(args):
    """Build unified vector index from all sessions"""
    config = Config()
    sessions_dir = config.get_sessions_dir()

    print(f"Building index from: {sessions_dir}")

    try:
        index = build_unified_index(sessions_dir, verbose=True)
        print(f"\n✅ Index built successfully")
        print(f"   Total sessions: {index['total_sessions']}")
        print(f"   Total vectors: {index['total_vectors']}")
        print(f"   Total messages: {index['total_messages']}")
        return 0
    except Exception as e:
        print(f"❌ Error building index: {e}", file=sys.stderr)
        return 1


def cmd_index_validate(args):
    """Validate index integrity"""
    config = Config()
    sessions_dir = config.get_sessions_dir()

    errors = validate_index(sessions_dir, verbose=True)

    if errors:
        return 1
    else:
        return 0


def cmd_index_stats(args):
    """Show index statistics"""
    config = Config()
    sessions_dir = config.get_sessions_dir()

    stats = get_index_stats(sessions_dir)

    if not stats.get('exists'):
        print("❌ Index not found. Run 'reccli index build' first.", file=sys.stderr)
        return 1

    if stats.get('error'):
        print(f"❌ Error: {stats['error']}", file=sys.stderr)
        return 1

    # Display stats
    print(f"\n📊 Index Statistics\n")
    print(f"Format: {stats['format']} v{stats['version']}")
    print(f"Created: {stats['created_at']}")
    print(f"Last updated: {stats['last_updated']}")
    print()
    print(f"Sessions: {stats['total_sessions']}")
    print(f"Messages: {stats['total_messages']}")
    print(f"Vectors: {stats['total_vectors']}")
    print()

    embedding = stats.get('embedding', {})
    print(f"Embedding:")
    print(f"  Provider: {embedding.get('provider', 'N/A')}")
    print(f"  Model: {embedding.get('model', 'N/A')}")
    print(f"  Dimensions: {embedding.get('dimensions', 'N/A')}")
    print()

    statistics = stats.get('statistics', {})
    print(f"Activity:")
    print(f"  Total duration: {statistics.get('total_duration_hours', 0):.1f} hours")
    print(f"  Avg session length: {statistics.get('average_session_length_minutes', 0)} minutes")
    print(f"  Total decisions: {statistics.get('total_decisions', 0)}")
    print(f"  Total problems solved: {statistics.get('total_problems_solved', 0)}")
    print(f"  Total code changes: {statistics.get('total_code_changes', 0)}")

    most_active = statistics.get('most_active_days', [])
    if most_active:
        print(f"\nMost active days: {', '.join(most_active)}")

    print()
    return 0


def cmd_search(args):
    """Search across all sessions"""
    config = Config()
    sessions_dir = config.get_sessions_dir()

    # Build time filter
    time_filter = None
    if args.last_hours:
        time_filter = {'lastHours': args.last_hours}

    # Build scope filter
    scope_filter = None
    if args.section or args.session or args.episode:
        scope_filter = {}
        if args.section:
            scope_filter['section'] = args.section
        if args.session:
            scope_filter['session_id'] = args.session
        if getattr(args, 'episode', None):
            scope_filter['episode_id'] = args.episode
        elif args.session and not getattr(args, 'all_episodes', False):
            from ..session.devsession import DevSession
            session_path = Path(args.session)
            if not session_path.is_absolute():
                sessions_dir_cfg = config.get_sessions_dir()
                if not session_path.suffix:
                    session_path = sessions_dir_cfg / f"{session_path}.devsession"
                else:
                    session_path = sessions_dir_cfg / session_path
            if session_path.exists():
                try:
                    session = DevSession.load(session_path)
                    if getattr(session, 'current_episode_id', None):
                        scope_filter['episode_id'] = session.current_episode_id
                except Exception:
                    pass

    # Search
    try:
        results = search(
            sessions_dir=sessions_dir,
            query=args.query,
            top_k=args.top_k,
            time=time_filter,
            scope=scope_filter
        )
    except Exception as e:
        print(f"❌ Error searching: {e}", file=sys.stderr)
        return 1

    if not results:
        print(f"No results found for: {args.query}")
        return 0

    # Display results
    print(f"\n🔍 Found {len(results)} results for: {args.query}\n")

    for i, result in enumerate(results, 1):
        # Format badges
        badges = result.get('badges', [])
        badge_str = ' '.join(f'[{b}]' for b in badges) if badges else ''

        # Format score
        score = result.get('final_score', 0)
        cosine = result.get('cosine_score', 0)

        # Display result
        print(f"{i}. {badge_str}")
        print(f"   ID: {result['id']}")
        print(f"   Session: {result['session']}")
        print(f"   Kind: {result['kind']}")
        print(f"   Score: {score:.4f} (cosine: {cosine:.3f})")
        print(f"   Timestamp: {result['timestamp']}")
        print(f"   Preview: {result['content_preview'][:150]}...")
        print()

    print(f"Use 'reccli expand <result-id>' to view full context")
    print()

    return 0


def cmd_expand(args):
    """Expand a search result to show full context"""
    config = Config()
    sessions_dir = config.get_sessions_dir()

    try:
        expanded = expand_result(sessions_dir, args.result_id, context_window=args.context)
    except Exception as e:
        print(f"❌ Error expanding result: {e}", file=sys.stderr)
        return 1

    if not expanded:
        print(f"❌ Result not found: {args.result_id}", file=sys.stderr)
        return 1

    # Display expanded result
    result = expanded['result']
    context_messages = expanded['context_messages']

    print(f"\n📄 {result['id']}\n")
    print(f"Session: {result['session']}")
    print(f"Kind: {result['kind']}")
    print(f"Timestamp: {result['timestamp']}")
    print()

    print(f"Context (messages {expanded['context_start']}-{expanded['context_end']}):")
    print("-" * 80)

    for msg in context_messages:
        role = msg.get('role', 'unknown')
        content = msg.get('content', '')
        msg_id = msg.get('id', '?')

        # Highlight the target message
        marker = '>>>' if msg_id == result['message_id'] else '   '

        print(f"\n{marker} [{role}] (id: {msg_id})")
        print(content[:500])  # Show first 500 chars

    print()
    return 0


def cmd_embed(args):
    """Generate embeddings for a session"""
    session_path = _resolve_session_path(args.session)

    if not session_path.exists():
        print(f"❌ Session not found: {session_path}", file=sys.stderr)
        return 1

    try:
        session = DevSession.load(session_path)
    except Exception as e:
        print(f"❌ Error loading session: {e}", file=sys.stderr)
        return 1

    # Generate embeddings
    try:
        count = session.generate_embeddings(force=args.force)

        if count > 0:
            # Save session
            session.save(session_path)
            print(f"✅ Generated {count} embeddings and saved to {session_path}")

            # Update index if it exists
            config = Config()
            sessions_dir = config.get_sessions_dir()
            index_path = sessions_dir / 'index.json'

            if index_path.exists():
                print(f"Updating unified index...")
                update_index_with_new_session(sessions_dir, session_path, verbose=False)
                print(f"✅ Index updated")

        return 0
    except Exception as e:
        print(f"❌ Error generating embeddings: {e}", file=sys.stderr)
        return 1


def cmd_hydrate(args):
    """Test memory middleware context hydration"""
    session_path = _resolve_session_path(args.session)

    if not session_path.exists():
        print(f"❌ Session not found: {session_path}", file=sys.stderr)
        return 1

    try:
        session = DevSession.load(session_path)
    except Exception as e:
        print(f"❌ Error loading session: {e}", file=sys.stderr)
        return 1

    # Initialize memory middleware
    config = Config()
    sessions_dir = config.get_sessions_dir()

    from ..retrieval.memory_middleware import MemoryMiddleware

    middleware = MemoryMiddleware(session, sessions_dir)

    # Hydrate context
    try:
        result = middleware.hydrate_prompt(
            user_input=args.query,
            num_recent=args.num_recent,
            include_wpc=False  # WPC not yet integrated
        )

        # Display results
        print(f"\n🧠 Memory Middleware - Context Hydration\n")
        print(f"Session: {session_path.stem}")
        print(f"Query: {args.query}")
        print()

        # Token allocation
        print(f"📊 Token Allocation (Budget: {result['budget']})")
        print(f"   Used: {result['tokens_used']} tokens")
        allocation = result['allocation']
        if allocation['summary'] > 0:
            print(f"   - Summary: {allocation['summary']} tokens")
        if allocation['recent'] > 0:
            print(f"   - Recent messages: {allocation['recent']} tokens")
        if allocation['relevant'] > 0:
            print(f"   - Relevant history: {allocation['relevant']} tokens")
        if allocation['project'] > 0:
            print(f"   - Project overview: {allocation['project']} tokens")
        if allocation.get('wpc', 0) > 0:
            print(f"   - WPC staged: {allocation['wpc']} tokens")
        print()

        # Context breakdown
        context = result['context']

        if context.get('project_overview'):
            print("✅ Project Overview: Loaded")
        else:
            print("⏸️  Project Overview: Skipped (saved tokens for vector search)")

        if context.get('summary'):
            print("✅ Session Summary: Loaded")

        if context.get('recent'):
            print(f"✅ Recent Messages: {len(context['recent'])} messages")

        if context.get('relevant_history'):
            print(f"✅ Relevant History: {len(context['relevant_history'])} messages (vector search)")
            print("\n   Top matches:")
            for i, msg in enumerate(context['relevant_history'][:3], 1):
                kind = msg.get('kind', 'note')
                score = msg.get('cosine_score', 0)
                preview = msg.get('content', '')[:80]
                print(f"   {i}. [{kind}] {preview}... (score: {score:.3f})")

        print()

        if args.show_prompt:
            print("=" * 80)
            print("GENERATED PROMPT:")
            print("=" * 80)
            print(result['prompt'])
            print("=" * 80)

        return 0

    except Exception as e:
        print(f"❌ Error hydrating context: {e}", file=sys.stderr)
        import traceback
        traceback.print_exc()
        return 1


def cmd_hydrate_streaming(args):
    """Test streaming memory middleware with progressive enhancement"""
    import asyncio
    import time

    session_path = _resolve_session_path(args.session)

    if not session_path.exists():
        print(f"❌ Session not found: {session_path}", file=sys.stderr)
        return 1

    # Load session
    from ..session.devsession import DevSession
    session = DevSession.load(session_path)

    # Load memory middleware
    from ..retrieval.memory_middleware import MemoryMiddleware
    config = Config()
    sessions_dir = config.get_sessions_dir()
    middleware = MemoryMiddleware(session, sessions_dir)

    # Run streaming retrieval
    async def stream_retrieval():
        """Run async streaming retrieval"""
        print(f"\n🚀 Streaming Hybrid Retrieval - Progressive Enhancement\n")
        print(f"Session: {session_path.stem}")
        print(f"Query: {args.query}")
        print()

        start_time = time.time()

        async for stage_result in middleware.hydrate_prompt_streaming(
            user_input=args.query,
            num_recent=args.num_recent,
            llm_client=None  # LLM client would be passed here
        ):
            stage = stage_result['stage']
            status = stage_result['status']
            latency = stage_result.get('latency_ms', 0)
            elapsed = (time.time() - start_time) * 1000

            if stage == 'instant':
                print(f"⚡ INSTANT ({elapsed:.0f}ms total) - Stage 1: Recent Messages")
                print(f"   └─ {stage_result['results']['count']} messages loaded from memory")

            elif stage == 'fast':
                print(f"\n🔍 FAST ({elapsed:.0f}ms total, +{latency:.0f}ms) - Stage 2: Quick Vector Search")
                results = stage_result['results']
                vector_results = results.get('quick_vector_results', [])
                print(f"   └─ {len(vector_results)} vector matches found")

                hints = results.get('context_hints', {})
                if hints.get('current_file'):
                    print(f"   └─ Context: Working on {hints['current_file']}")
                if hints.get('current_topic'):
                    print(f"   └─ Topic: {hints['current_topic']}")

            elif stage == 'smart':
                reasoning_used = stage_result.get('reasoning_used', False)

                if reasoning_used:
                    print(f"\n🧠 SMART ({elapsed:.0f}ms total, +{latency:.0f}ms) - Stage 3: LLM Reasoning + Refined Search")

                    reasoning = stage_result['results'].get('reasoning', {})
                    if reasoning.get('intent'):
                        print(f"   └─ Intent: {reasoning['intent']}")
                    if reasoning.get('searches'):
                        print(f"   └─ Refined queries: {reasoning['searches']}")

                    vector_results = stage_result['results'].get('vector_results', [])
                    print(f"   └─ {len(vector_results)} refined matches")
                else:
                    skipped_reason = stage_result.get('reasoning_skipped', 'Unknown')
                    print(f"\n✨ SMART ({elapsed:.0f}ms total) - Stage 3: Skipped LLM Reasoning")
                    print(f"   └─ Reason: {skipped_reason}")
                    print(f"   └─ Using fast results (query was clear)")

                # Final stats
                print(f"\n📊 Final Context:")
                print(f"   └─ Tokens: {stage_result.get('tokens_used', 0)}")
                print(f"   └─ Total time: {elapsed:.0f}ms")

                if args.show_prompt:
                    print("\n" + "=" * 80)
                    print("GENERATED PROMPT:")
                    print("=" * 80)
                    print(stage_result.get('prompt', ''))
                    print("=" * 80)

        print("\n✅ Streaming retrieval complete")

    try:
        # Run async streaming
        asyncio.run(stream_retrieval())
        return 0

    except Exception as e:
        print(f"❌ Error in streaming retrieval: {e}", file=sys.stderr)
        import traceback
        traceback.print_exc()
        return 1


def cmd_compact(args):
    """Manually trigger preemptive compaction"""
    from ..session.devsession import DevSession
    from ..summarization.preemptive_compaction import PreemptiveCompactor
    from .config import Config

    session_path = _resolve_session_path(args.session)

    if not session_path.exists():
        print(f"❌ Session not found: {session_path}", file=sys.stderr)
        return 1

    try:
        # Load session
        session = DevSession.load(session_path)

        # Initialize compactor (without LLM client for CLI commands)
        config = Config()
        sessions_dir = config.get_sessions_dir()
        compactor = PreemptiveCompactor(
            session,
            sessions_dir,
            llm_client=None  # CLI commands don't have LLM client
        )

        print("\n⚠️  Note: Manual compaction from CLI runs without AI summary generation")
        print("   (Use 'reccli chat' for full compaction with AI summaries)")
        print()

        # Trigger manual compaction
        compacted_context = compactor.manual_compact()

        print(f"\n✅ Compaction complete")
        print(f"📄 Session saved: {session_path}")

        return 0

    except Exception as e:
        print(f"❌ Error during compaction: {e}", file=sys.stderr)
        import traceback
        traceback.print_exc()
        return 1


def cmd_check_tokens(args):
    """Show token count and compaction status"""
    from ..session.devsession import DevSession
    from ..summarization.preemptive_compaction import PreemptiveCompactor
    from .config import Config

    session_path = _resolve_session_path(args.session)

    if not session_path.exists():
        print(f"❌ Session not found: {session_path}", file=sys.stderr)
        return 1

    try:
        # Load session
        session = DevSession.load(session_path)

        # Initialize compactor (without LLM client for status check)
        config = Config()
        sessions_dir = config.get_sessions_dir()
        compactor = PreemptiveCompactor(
            session,
            sessions_dir,
            llm_client=None  # Status check doesn't need LLM client
        )

        # Get status
        status = compactor.get_status()

        print(f"\n📊 Token Count Status - {session.session_id}\n")
        print(f"Current tokens: {status['current_tokens']:,}")
        print(f"Warn threshold: {status['warn_threshold']:,} (90%)")
        print(f"Compact threshold: {status['compact_threshold']:,} (95%)")
        print(f"Percentage: {status['percentage']:.1f}%")
        print(f"Remaining: {status['remaining']:,} tokens")
        print(f"Status: {status['status'].upper()}")
        print(f"Compaction count: {status['compaction_count']}")
        print()

        # Status indicator
        if status['status'] == 'critical':
            print("🔴 CRITICAL: Compaction should be triggered now")
        elif status['status'] == 'warning':
            print(f"⚠️  WARNING: Approaching compaction threshold ({status['remaining']:,} remaining)")
        else:
            print("✅ OK: Token count is healthy")

        print()
        return 0

    except Exception as e:
        print(f"❌ Error checking tokens: {e}", file=sys.stderr)
        return 1


def cmd_checkpoint_add(args):
    """Add a checkpoint"""
    from ..session.devsession import DevSession
    from ..session.checkpoints import CheckpointManager
    from .config import Config

    # Find session
    if args.session:
        session_path = Path(args.session)
    else:
        # Default to most recent session
        config = Config()
        sessions_dir = config.get_sessions_dir()
        sessions = sorted(sessions_dir.glob('*.devsession'), key=lambda p: p.stat().st_mtime, reverse=True)
        if not sessions:
            print("❌ No sessions found", file=sys.stderr)
            return 1
        session_path = sessions[0]

    # If not absolute path, look in sessions directory
    if not session_path.is_absolute():
        config = Config()
        sessions_dir = config.get_sessions_dir()
        if not session_path.suffix:
            session_path = sessions_dir / f"{session_path}.devsession"
        else:
            session_path = sessions_dir / session_path

    if not session_path.exists():
        print(f"❌ Session not found: {session_path}", file=sys.stderr)
        return 1

    try:
        # Load session
        session = DevSession.load(session_path)

        # Add checkpoint
        manager = CheckpointManager(session)
        checkpoint = manager.add_checkpoint(args.label, args.criteria)

        print(f"\n✅ Checkpoint created: {checkpoint['id']}")
        print(f"   Label: {checkpoint['label']}")
        if checkpoint['criteria']:
            print(f"   Criteria: {checkpoint['criteria']}")
        print(f"   Time: {checkpoint['t']}")
        print(f"   Message index: {checkpoint['message_index']}")
        print(f"   Tokens: {checkpoint['token_count']:,}")
        print()

        return 0

    except Exception as e:
        print(f"❌ Error adding checkpoint: {e}", file=sys.stderr)
        return 1


def cmd_checkpoint_list(args):
    """List all checkpoints"""
    from ..session.devsession import DevSession
    from ..session.checkpoints import CheckpointManager
    from .config import Config

    # Find session
    if args.session:
        session_path = Path(args.session)
    else:
        # Default to most recent session
        config = Config()
        sessions_dir = config.get_sessions_dir()
        sessions = sorted(sessions_dir.glob('*.devsession'), key=lambda p: p.stat().st_mtime, reverse=True)
        if not sessions:
            print("❌ No sessions found", file=sys.stderr)
            return 1
        session_path = sessions[0]

    # If not absolute path, look in sessions directory
    if not session_path.is_absolute():
        config = Config()
        sessions_dir = config.get_sessions_dir()
        if not session_path.suffix:
            session_path = sessions_dir / f"{session_path}.devsession"
        else:
            session_path = sessions_dir / session_path

    if not session_path.exists():
        print(f"❌ Session not found: {session_path}", file=sys.stderr)
        return 1

    try:
        # Load session
        session = DevSession.load(session_path)

        # List checkpoints
        manager = CheckpointManager(session)
        checkpoints = manager.list_checkpoints()

        if not checkpoints:
            print("\nNo checkpoints found in this session.")
            print("Create one with: reccli checkpoint add <label>")
            print()
            return 0

        print(f"\n📍 Checkpoints in {session.session_id}\n")

        for cp in checkpoints:
            print(f"{cp['id']}: {cp['label']}")
            print(f"   Time: {cp['t']}")
            if cp.get('criteria'):
                print(f"   Criteria: {cp['criteria']}")
            print(f"   Message index: {cp['message_index']}")
            print()

        return 0

    except Exception as e:
        print(f"❌ Error listing checkpoints: {e}", file=sys.stderr)
        return 1


def cmd_checkpoint_diff(args):
    """Show changes since checkpoint"""
    from ..session.devsession import DevSession
    from ..session.checkpoints import CheckpointManager, format_checkpoint_diff
    from .config import Config

    # Find session
    if args.session:
        session_path = Path(args.session)
    else:
        # Default to most recent session
        config = Config()
        sessions_dir = config.get_sessions_dir()
        sessions = sorted(sessions_dir.glob('*.devsession'), key=lambda p: p.stat().st_mtime, reverse=True)
        if not sessions:
            print("❌ No sessions found", file=sys.stderr)
            return 1
        session_path = sessions[0]

    # If not absolute path, look in sessions directory
    if not session_path.is_absolute():
        config = Config()
        sessions_dir = config.get_sessions_dir()
        if not session_path.suffix:
            session_path = sessions_dir / f"{session_path}.devsession"
        else:
            session_path = sessions_dir / session_path

    if not session_path.exists():
        print(f"❌ Session not found: {session_path}", file=sys.stderr)
        return 1

    try:
        # Load session
        session = DevSession.load(session_path)

        # Get diff
        manager = CheckpointManager(session)
        diff = manager.diff_since_checkpoint(args.checkpoint_id)

        # Format and display
        print(format_checkpoint_diff(diff))

        return 0

    except ValueError as e:
        print(f"❌ {e}", file=sys.stderr)
        return 1
    except Exception as e:
        print(f"❌ Error getting diff: {e}", file=sys.stderr)
        return 1


def cmd_episode_new(args):
    from ..session.devsession import DevSession
    from .config import Config
    if args.session:
        session_path = Path(args.session)
    else:
        config = Config()
        sessions_dir = config.get_sessions_dir()
        sessions = sorted(sessions_dir.glob('*.devsession'), key=lambda p: p.stat().st_mtime, reverse=True)
        if not sessions:
            print("❌ No sessions found", file=sys.stderr)
            return 1
        session_path = sessions[0]

    if not session_path.is_absolute():
        config = Config()
        sessions_dir = config.get_sessions_dir()
        if not session_path.suffix:
            session_path = sessions_dir / f"{session_path}.devsession"
        else:
            session_path = sessions_dir / session_path

    if not session_path.exists():
        print(f"❌ Session not found: {session_path}", file=sys.stderr)
        return 1

    try:
        session = DevSession.load(session_path)
        eid = session.start_episode(args.goal)
        session.save(session_path)
        print(f"\n✅ New episode started: {eid}")
        print(f"   Goal: {args.goal}")
        print(f"   Session: {session.session_id}")
        print()
        return 0
    except Exception as e:
        print(f"❌ Error starting episode: {e}", file=sys.stderr)
        return 1


CODEX_AGENTS_BEGIN = "<!-- BEGIN RECCLI CODEX INSTRUCTIONS -->"
CODEX_AGENTS_END = "<!-- END RECCLI CODEX INSTRUCTIONS -->"


def _codex_agents_block() -> str:
    """Instructions Codex can load from AGENTS.md to emulate SessionStart hooks."""
    return f"""{CODEX_AGENTS_BEGIN}
# RecCli Codex Session Bootstrap

At the start of a new Codex session, use RecCli project memory before doing substantive work.

1. If the current working directory is inside a RecCli project, immediately call `mcp__reccli__.load_project_context` with that directory.
2. If the current working directory is not inside a RecCli project, read `~/.reccli/projects.json` and ask the user:

   `Which project would you like to work on today?`

   List the registered project names as bullets. If the user chooses one, immediately call `mcp__reccli__.load_project_context` with that project's path before continuing. If they want a new project, use `mcp__reccli__.project_init` on the project root.
3. When the user signals they are wrapping up, call `mcp__reccli__.save_session_notes` with the current project before ending the session.
4. If the user switches projects mid-session, ask whether to save notes for the current project first; if they confirm, call `save_session_notes`, then call `load_project_context` for the new project.

Codex does not currently provide Claude Code-style lifecycle hooks, so these instructions are the Codex-side equivalent of RecCli's Claude `SessionStart` and save-on-exit behavior.
{CODEX_AGENTS_END}
"""


def _install_codex_agents_instructions() -> Path:
    """Install or update the RecCli managed block in ~/AGENTS.md."""
    agents_path = Path.home() / "AGENTS.md"
    block = _codex_agents_block()

    if agents_path.exists():
        content = agents_path.read_text()
        if CODEX_AGENTS_BEGIN in content and CODEX_AGENTS_END in content:
            before, rest = content.split(CODEX_AGENTS_BEGIN, 1)
            _, after = rest.split(CODEX_AGENTS_END, 1)
            content = before.rstrip() + "\n\n" + block + after.lstrip()
        else:
            content = content.rstrip() + "\n\n" + block
    else:
        content = block

    agents_path.write_text(content)
    return agents_path


def _uninstall_codex_agents_instructions() -> bool:
    """Remove the RecCli managed block from ~/AGENTS.md. Returns True if changed."""
    agents_path = Path.home() / "AGENTS.md"
    if not agents_path.exists():
        return False

    content = agents_path.read_text()
    if CODEX_AGENTS_BEGIN not in content or CODEX_AGENTS_END not in content:
        return False

    before, rest = content.split(CODEX_AGENTS_BEGIN, 1)
    _, after = rest.split(CODEX_AGENTS_END, 1)
    new_content = (before.rstrip() + "\n\n" + after.lstrip()).strip()
    if new_content:
        agents_path.write_text(new_content + "\n")
    else:
        agents_path.unlink()
    return True


def _setup_codex(args, python_path: str, pythonpath: str) -> int:
    """Configure Codex CLI integration: MCP server plus AGENTS.md bootstrap."""

    if getattr(args, 'uninstall', False):
        config_path = Path.home() / ".codex" / "config.toml"
        if config_path.exists():
            content = config_path.read_text()
            if "[mcp_servers.reccli]" in content:
                # Remove the reccli section
                lines = content.split("\n")
                new_lines = []
                skip = False
                for line in lines:
                    if line.strip() == "[mcp_servers.reccli]":
                        skip = True
                        continue
                    if skip and line.strip().startswith("[") and line.strip() != "[mcp_servers.reccli]":
                        skip = False
                    if not skip:
                        new_lines.append(line)
                config_path.write_text("\n".join(new_lines))
                print("Removed RecCli MCP server from ~/.codex/config.toml")
            else:
                print("RecCli not found in Codex config.")
        else:
            print("No Codex config found at ~/.codex/config.toml")
        if _uninstall_codex_agents_instructions():
            print("Removed RecCli Codex instructions from ~/AGENTS.md")
        print("RecCli uninstalled from Codex CLI.")
        return 0

    # --- Install ---
    config_path = Path.home() / ".codex" / "config.toml"
    config_path.parent.mkdir(parents=True, exist_ok=True)

    # Build the TOML block
    toml_block = (
        '\n[mcp_servers.reccli]\n'
        f'command = "{python_path}"\n'
        f'args = ["-m", "reccli.mcp_server"]\n'
        f'env = {{ PYTHONPATH = "{pythonpath}" }}\n'
    )

    if config_path.exists():
        content = config_path.read_text()
        if "[mcp_servers.reccli]" in content:
            print("RecCli MCP server already configured in Codex.")
        else:
            # Append to existing config
            with open(config_path, "a") as f:
                f.write(toml_block)
    else:
        config_path.write_text(toml_block.lstrip())

    agents_path = _install_codex_agents_instructions()

    # Create ~/.reccli directory
    reccli_dir = Path.home() / ".reccli"
    reccli_dir.mkdir(exist_ok=True)
    (reccli_dir / "active_sessions").mkdir(exist_ok=True)

    print(f"RecCli configured for Codex CLI.")
    print(f"  MCP server: reccli ({python_path})")
    print(f"  Config: {config_path}")
    print(f"  Startup instructions: {agents_path}")
    print(f"\nAll MCP tools are available. Start a new Codex session to use them.")
    print(f"Note: Codex does not expose Claude Code-style lifecycle hooks; ~/AGENTS.md provides the session-start project picker behavior.")
    print(f"Use save_session_notes at the end of each session to preserve your work.")
    return 0


def cmd_setup(args):
    """Configure AI agent integration: MCP server + hooks for automatic recording."""
    import subprocess

    reccli_root = Path(__file__).resolve().parent.parent.parent.parent
    packages_dir = reccli_root / "packages"
    venv_python = reccli_root / "venv" / "bin" / "python3"

    # Use repo venv when present, otherwise preserve the interpreter that ran setup.
    if venv_python.exists():
        python_path = str(venv_python)
    else:
        python_path = sys.executable

    pythonpath = str(packages_dir)

    if getattr(args, 'codex', False):
        return _setup_codex(args, python_path, pythonpath)

    if args.uninstall:
        # Remove hooks from settings.json
        settings_path = Path.home() / ".claude" / "settings.json"
        if settings_path.exists():
            settings = json.loads(settings_path.read_text())
            hooks = settings.get("hooks", {})
            changed = False
            for event_name in list(hooks.keys()):
                hooks[event_name] = [
                    entry for entry in hooks[event_name]
                    if not any("reccli.hooks.handle_event" in h.get("command", "") for h in entry.get("hooks", []))
                ]
                if not hooks[event_name]:
                    del hooks[event_name]
                    changed = True
                else:
                    changed = True
            if changed:
                settings["hooks"] = hooks
                settings_path.write_text(json.dumps(settings, indent=2) + "\n")
                print("Removed RecCli hooks from ~/.claude/settings.json")

        # Remove MCP server
        try:
            subprocess.run(["claude", "mcp", "remove", "reccli"], capture_output=True)
            print("Removed RecCli MCP server")
        except FileNotFoundError:
            pass

        print("RecCli uninstalled from Claude Code.")
        return 0

    # --- Install ---

    # 1. Add MCP server
    print("Adding MCP server...")
    mcp_cmd = ["claude", "mcp", "add", "--scope", "user", "reccli", "--",
               "env", f"PYTHONPATH={pythonpath}", python_path, "-m", "reccli.mcp_server"]
    try:
        result = subprocess.run(mcp_cmd, capture_output=True, text=True)
        if result.returncode == 0:
            print(f"  MCP server registered (user scope)")
        else:
            # Might already exist — try remove then re-add
            subprocess.run(["claude", "mcp", "remove", "reccli"], capture_output=True)
            result = subprocess.run(mcp_cmd, capture_output=True, text=True)
            if result.returncode == 0:
                print(f"  MCP server re-registered (user scope)")
            else:
                print(f"  Warning: MCP registration failed: {result.stderr.strip()}")
    except FileNotFoundError:
        print("  Error: 'claude' CLI not found. Install Claude Code first.")
        return 1

    # 2. Add hooks to settings.json
    print("Configuring hooks...")
    settings_path = Path.home() / ".claude" / "settings.json"
    settings_path.parent.mkdir(parents=True, exist_ok=True)

    if settings_path.exists():
        settings = json.loads(settings_path.read_text())
    else:
        settings = {}

    hook_command = f"cd /tmp && PYTHONPATH={pythonpath} {python_path} -m reccli.hooks.handle_event"
    hook_entry = {"type": "command", "command": hook_command}

    hook_events = {
        "SessionStart": [{"matcher": "", "hooks": [hook_entry]}],
        "UserPromptSubmit": [{"matcher": "", "hooks": [hook_entry]}],
        "Stop": [{"matcher": "", "hooks": [hook_entry]}],
        "PostToolUse": [{"matcher": "", "hooks": [hook_entry]}],
        "PostCompact": [{"matcher": "", "hooks": [hook_entry]}],
        "SessionEnd": [{"matcher": "", "hooks": [{**hook_entry, "timeout": 5000}]}],
    }

    existing_hooks = settings.get("hooks", {})

    for event_name, entries in hook_events.items():
        if event_name in existing_hooks:
            # Check if reccli hook already exists
            has_reccli = any(
                "reccli.hooks.handle_event" in h.get("command", "")
                for entry in existing_hooks[event_name]
                for h in entry.get("hooks", [])
            )
            if has_reccli:
                # Update the command in case paths changed
                for entry in existing_hooks[event_name]:
                    for h in entry.get("hooks", []):
                        if "reccli.hooks.handle_event" in h.get("command", ""):
                            h["command"] = hook_command
                continue
            # Add alongside existing hooks
            existing_hooks[event_name].extend(entries)
        else:
            existing_hooks[event_name] = entries

    settings["hooks"] = existing_hooks
    settings_path.write_text(json.dumps(settings, indent=2) + "\n")
    print(f"  Hooks configured for: {', '.join(hook_events.keys())}")

    # 3. Create ~/.reccli directory
    reccli_dir = Path.home() / ".reccli"
    reccli_dir.mkdir(exist_ok=True)
    (reccli_dir / "active_sessions").mkdir(exist_ok=True)

    print(f"\nRecCli is ready. Start a new Claude Code session to begin recording.")
    print(f"  MCP server: reccli ({python_path})")
    print(f"  Hooks: 6 events configured")
    print(f"  Config: ~/.claude/settings.json")
    return 0


def main():
    """Main CLI entry point"""
    parser = argparse.ArgumentParser(
        description='RecCli - Intelligent Terminal Session Recorder with Native LLM',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Native LLM Chat
  reccli chat                      # Start interactive chat (uses default model)
  reccli chat --model claude       # Chat with Claude
  reccli chat --model gpt5         # Chat with GPT-5
  reccli ask "explain .devsession" # One-shot question

  # Configuration
  reccli config --anthropic-key sk-ant-...
  reccli config --openai-key sk-...
  reccli config --default-model gpt5

  # Terminal Recording
  reccli record                    # Record terminal session
  reccli record -n my-session      # Record with custom name

  # Session Management
  reccli list                      # List all sessions
  reccli show my-session           # Show session details
  reccli export my-session         # Export to markdown

  # Vector Embeddings & Search (Phase 5)
  reccli embed my-session          # Generate embeddings for a session
  reccli index build               # Build unified vector index
  reccli index stats               # Show index statistics
  reccli index validate            # Validate index integrity
  reccli search "error handling"   # Search across all sessions
  reccli search "bug" --last-hours 48  # Search recent sessions
  reccli expand result-id          # Expand search result with context

  # Memory Middleware (Phase 6)
  reccli hydrate my-session "what next?"  # Test context loading
        """
    )

    subparsers = parser.add_subparsers(dest='command', help='Commands')

    # Chat command (NEW - Native LLM)
    chat_parser = subparsers.add_parser('chat', help='Interactive chat with LLM (use /model in-session to switch)')
    chat_parser.add_argument('-m', '--model', choices=[
        'claude', 'claude-sonnet', 'claude-opus', 'claude-haiku',
        'gpt5', 'gpt5-mini', 'gpt5-nano',
        'gpt4o'
    ], help='Model to use (default: claude-sonnet). Type /model during chat to switch interactively.')
    chat_parser.add_argument('-n', '--name', help='Session name')
    chat_parser.add_argument('-o', '--output', help='Output file path')
    chat_parser.add_argument('--api-key', help='API key (overrides config)')
    chat_parser.set_defaults(func=cmd_chat)

    # Ask command (NEW - One-shot query)
    ask_parser = subparsers.add_parser('ask', help='Ask a single question')
    ask_parser.add_argument('question', help='Question to ask')
    ask_parser.add_argument('-m', '--model', choices=[
        'claude', 'claude-sonnet', 'claude-opus', 'claude-haiku',
        'gpt5', 'gpt5-mini', 'gpt5-nano',
        'gpt4o'
    ], help='Model to use (default: claude-sonnet)')
    ask_parser.add_argument('-o', '--output', help='Output file path')
    ask_parser.add_argument('--api-key', help='API key (overrides config)')
    ask_parser.set_defaults(func=cmd_ask)

    # Config command (NEW - Manage API keys)
    config_parser = subparsers.add_parser('config', help='Manage configuration')
    config_parser.add_argument('--anthropic-key', help='Set Anthropic API key')
    config_parser.add_argument('--openai-key', help='Set OpenAI API key')
    config_parser.add_argument('--default-model', choices=[
        'claude', 'claude-sonnet', 'claude-opus', 'claude-haiku',
        'gpt5', 'gpt5-mini', 'gpt5-nano', 'gpt4o'
    ], help='Set default model')
    config_parser.set_defaults(func=cmd_config)

    # Record command
    record_parser = subparsers.add_parser('record', help='Record a new terminal session')
    record_parser.add_argument('-n', '--name', help='Session name')
    record_parser.add_argument('-o', '--output', help='Output file path')
    record_parser.add_argument('-s', '--shell', help='Shell to spawn (default: $SHELL)')
    record_parser.set_defaults(func=cmd_record)

    # List command
    list_parser = subparsers.add_parser('list', help='List recorded sessions')
    list_parser.set_defaults(func=cmd_list)

    # Show command
    show_parser = subparsers.add_parser('show', help='Show session details')
    show_parser.add_argument('session', help='Session name or path')
    show_parser.set_defaults(func=cmd_show)

    # Export command
    export_parser = subparsers.add_parser('export', help='Export session to different format')
    export_parser.add_argument('session', help='Session name or path')
    export_parser.add_argument('-f', '--format', choices=['txt', 'md'], default='md', help='Export format')
    export_parser.add_argument('-o', '--output', help='Output file path')
    export_parser.set_defaults(func=cmd_export)

    # Watch command (NEW - Auto-launch GUI for terminals)
    watch_parser = subparsers.add_parser('watch', help='Watch for new terminal windows and auto-launch GUI')
    watch_parser.set_defaults(func=cmd_watch)

    # Index commands (NEW - Phase 5: Vector indexing)
    index_parser = subparsers.add_parser('index', help='Manage vector index')
    index_subparsers = index_parser.add_subparsers(dest='index_command', help='Index commands')

    # index build
    index_build_parser = index_subparsers.add_parser('build', help='Build unified vector index')
    index_build_parser.set_defaults(func=cmd_index_build)

    # index validate
    index_validate_parser = index_subparsers.add_parser('validate', help='Validate index integrity')
    index_validate_parser.set_defaults(func=cmd_index_validate)

    # index stats
    index_stats_parser = index_subparsers.add_parser('stats', help='Show index statistics')
    index_stats_parser.set_defaults(func=cmd_index_stats)

    # Search command (NEW - Phase 5: Hybrid search)
    search_parser = subparsers.add_parser('search', help='Search across all sessions')
    search_parser.add_argument('query', help='Search query')
    search_parser.add_argument('-k', '--top-k', type=int, default=10, help='Number of results (default: 10)')
    search_parser.add_argument('--last-hours', type=int, help='Filter to last N hours')
    search_parser.add_argument('--section', help='Filter to specific section')
    search_parser.add_argument('--session', help='Filter to specific session')
    search_parser.add_argument('--episode', help='Filter to specific episode ID (e.g., ep_003)')
    search_parser.add_argument('--all-episodes', action='store_true', help='Do not scope to current episode when --session is provided')
    search_parser.set_defaults(func=cmd_search)

    # Expand command (NEW - Phase 5: Expand search result)
    expand_parser = subparsers.add_parser('expand', help='Expand search result to show full context')
    expand_parser.add_argument('result_id', help='Result ID (from search output)')
    expand_parser.add_argument('-c', '--context', type=int, default=5, help='Context window size (default: 5)')
    expand_parser.set_defaults(func=cmd_expand)

    # Embed command (NEW - Phase 5: Generate embeddings)
    embed_parser = subparsers.add_parser('embed', help='Generate embeddings for a session')
    embed_parser.add_argument('session', help='Session name or path')
    embed_parser.add_argument('-f', '--force', action='store_true', help='Force re-embedding even if exists')
    embed_parser.set_defaults(func=cmd_embed)

    # Hydrate command (NEW - Phase 6: Test memory middleware)
    hydrate_parser = subparsers.add_parser('hydrate', help='Test memory middleware context hydration')
    hydrate_parser.add_argument('session', help='Session name or path')
    hydrate_parser.add_argument('query', help='User query to test context loading')
    hydrate_parser.add_argument('-n', '--num-recent', type=int, default=20, help='Number of recent messages (default: 20)')
    hydrate_parser.add_argument('-p', '--show-prompt', action='store_true', help='Show generated prompt')
    hydrate_parser.set_defaults(func=cmd_hydrate)

    # Hydrate streaming command (NEW - Streaming hybrid retrieval)
    hydrate_stream_parser = subparsers.add_parser('hydrate-stream', help='Test streaming hybrid retrieval with progressive enhancement')
    hydrate_stream_parser.add_argument('session', help='Session name or path')
    hydrate_stream_parser.add_argument('query', help='User query to test streaming retrieval')
    hydrate_stream_parser.add_argument('-n', '--num-recent', type=int, default=20, help='Number of recent messages (default: 20)')
    hydrate_stream_parser.add_argument('-p', '--show-prompt', action='store_true', help='Show generated prompt')
    hydrate_stream_parser.set_defaults(func=cmd_hydrate_streaming)

    # Compact command (NEW - Phase 7: Manual compaction)
    compact_parser = subparsers.add_parser('compact', help='Manually trigger preemptive compaction')
    compact_parser.add_argument('session', help='Session name or path')
    compact_parser.set_defaults(func=cmd_compact)

    # Project commands (.devproject)
    project_parser = subparsers.add_parser('project', help='Manage .devproject dashboard and proposals')
    project_subparsers = project_parser.add_subparsers(dest='project_command')

    project_show_parser = project_subparsers.add_parser('show', help='Show .devproject summary')
    project_show_parser.add_argument('--project-root', help='Project root containing .devproject')
    project_show_parser.add_argument('--json', action='store_true', help='Print raw JSON')
    project_show_parser.set_defaults(func=cmd_project_show)

    project_init_parser = project_subparsers.add_parser('init', help='Initialize .devproject from the current codebase with LLM clustering')
    project_init_parser.add_argument('--project-root', help='Project root to scan')
    project_init_parser.add_argument('--force', action='store_true', help='Overwrite an existing .devproject')
    project_init_parser.add_argument('--model', help='Model to use for semantic clustering (defaults to configured default model)')
    project_init_parser.add_argument('--description', help='Optional 1-2 sentence project description to guide feature generation')
    project_init_parser.add_argument('--no-review', action='store_true', help='Skip the post-init "what is missing?" review prompt')
    project_init_parser.set_defaults(func=cmd_project_init)

    project_sync_parser = project_subparsers.add_parser('sync', help='Scan codebase and propose .devproject updates')
    project_sync_parser.add_argument('--project-root', help='Project root containing .devproject')
    project_sync_parser.add_argument('--apply', action='store_true', help='Apply generated proposal immediately')
    project_sync_parser.set_defaults(func=cmd_project_sync)

    project_proposals_parser = project_subparsers.add_parser('proposals', help='List pending .devproject proposals')
    project_proposals_parser.add_argument('--project-root', help='Project root containing .devproject')
    project_proposals_parser.set_defaults(func=cmd_project_proposals)

    project_update_parser = project_subparsers.add_parser('update', help='Generate a .devproject proposal from a session')
    project_update_parser.add_argument('session', help='Session name or path')
    project_update_parser.add_argument('--project-root', help='Project root containing .devproject')
    project_update_parser.add_argument('--apply', action='store_true', help='Apply generated proposal immediately')
    project_update_parser.set_defaults(func=cmd_project_update)

    project_apply_parser = project_subparsers.add_parser('apply', help='Apply a pending .devproject proposal')
    project_apply_parser.add_argument('proposal_id', help='Pending proposal ID')
    project_apply_parser.add_argument('--project-root', help='Project root containing .devproject')
    project_apply_parser.set_defaults(func=cmd_project_apply)

    project_reject_parser = project_subparsers.add_parser('reject', help='Reject a pending .devproject proposal')
    project_reject_parser.add_argument('proposal_id', help='Pending proposal ID')
    project_reject_parser.add_argument('--project-root', help='Project root containing .devproject')
    project_reject_parser.add_argument('--reason', help='Optional rejection reason')
    project_reject_parser.set_defaults(func=cmd_project_reject)

    # Check tokens command (NEW - Phase 7: Check token count)
    check_tokens_parser = subparsers.add_parser('check-tokens', help='Show token count and compaction status')
    check_tokens_parser.add_argument('session', help='Session name or path')
    check_tokens_parser.set_defaults(func=cmd_check_tokens)

    # Checkpoint commands (NEW - Phase 7: Checkpoint management)
    checkpoint_parser = subparsers.add_parser('checkpoint', help='Manage checkpoints')
    checkpoint_subparsers = checkpoint_parser.add_subparsers(dest='checkpoint_command')

    checkpoint_add_parser = checkpoint_subparsers.add_parser('add', help='Add a checkpoint')
    checkpoint_add_parser.add_argument('label', help='Checkpoint label (e.g., "pre-release")')
    checkpoint_add_parser.add_argument('-c', '--criteria', help='Optional criteria description')
    checkpoint_add_parser.add_argument('-s', '--session', help='Session name or path (defaults to current)')
    checkpoint_add_parser.set_defaults(func=cmd_checkpoint_add)

    checkpoint_list_parser = checkpoint_subparsers.add_parser('list', help='List all checkpoints')
    checkpoint_list_parser.add_argument('-s', '--session', help='Session name or path (defaults to current)')
    checkpoint_list_parser.set_defaults(func=cmd_checkpoint_list)

    checkpoint_diff_parser = checkpoint_subparsers.add_parser('diff-since', help='Show changes since checkpoint')
    checkpoint_diff_parser.add_argument('checkpoint_id', help='Checkpoint ID (e.g., "CP_12")')
    checkpoint_diff_parser.add_argument('-s', '--session', help='Session name or path (defaults to current)')
    checkpoint_diff_parser.set_defaults(func=cmd_checkpoint_diff)

    # Episode commands (NEW - Phase 7: Manual episode control)
    episode_parser = subparsers.add_parser('episode', help='Manage episodes')
    episode_subparsers = episode_parser.add_subparsers(dest='episode_command')

    episode_new_parser = episode_subparsers.add_parser('new', help='Start a new episode with a goal')
    episode_new_parser.add_argument('goal', help='Episode goal/description (e.g., "Refactor search scoping")')
    episode_new_parser.add_argument('-s', '--session', help='Session name or path (defaults to most recent)')
    episode_new_parser.set_defaults(func=cmd_episode_new)

    # Setup command
    setup_parser = subparsers.add_parser('setup', help='Configure AI agent integration (MCP server + hooks)')
    setup_parser.add_argument('--uninstall', action='store_true', help='Remove hooks and MCP server')
    setup_parser.add_argument('--codex', action='store_true', help='Configure for OpenAI Codex CLI instead of Claude Code')
    setup_parser.set_defaults(func=cmd_setup)

    # Parse arguments
    args = parser.parse_args()

    # Default to chat mode if no command specified
    if not args.command:
        # Create a namespace with chat defaults
        from argparse import Namespace
        args = Namespace(
            command='chat',
            model=None,
            name=None,
            output=None,
            api_key=None,
            func=cmd_chat
        )

    # Execute command
    return args.func(args)


if __name__ == '__main__':
    sys.exit(main())
