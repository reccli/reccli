"""
CLI - Command-line interface for RecCli
"""

import sys
import argparse
from pathlib import Path
from datetime import datetime

from .recorder import record_session, watch_terminals
from .devsession import DevSession
from .llm import chat_session, one_shot_query
from .config import Config


def cmd_record(args):
    """Record a new terminal session"""
    # Determine output path
    if args.output:
        output_path = Path(args.output)
    else:
        # Auto-generate filename
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        name = args.name or f"session_{timestamp}"
        output_dir = Path.home() / '.reccli' / 'sessions'
        output_dir.mkdir(parents=True, exist_ok=True)
        output_path = output_dir / f"{name}.devsession"

    # Start recording
    exit_code = record_session(output_path, shell=args.shell)
    return exit_code


def cmd_list(args):
    """List recorded sessions"""
    sessions_dir = Path.home() / '.reccli' / 'sessions'

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
        sessions_dir = Path.home() / '.reccli' / 'sessions'
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
        sessions_dir = Path.home() / '.reccli' / 'sessions'
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
    elif args.format == 'cast':
        _export_cast(session, output_path)
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


def _export_cast(session: DevSession, output_path: Path):
    """Export to asciinema .cast format (v2)"""
    import json

    with open(output_path, 'w', encoding='utf-8') as f:
        # Write header
        header = {
            "version": 2,
            "width": session.terminal_recording['width'],
            "height": session.terminal_recording['height'],
            "timestamp": int(datetime.fromisoformat(session.created).timestamp())
        }
        f.write(json.dumps(header) + '\n')

        # Write events
        for event in session.terminal_recording['events']:
            f.write(json.dumps(event) + '\n')


def cmd_chat(args):
    """Start interactive chat with LLM"""
    config = Config()

    # Determine model
    model = args.model or config.get_default_model()

    # Determine output path
    if args.output:
        output_path = Path(args.output)
    else:
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        name = args.name or f"chat_{model}_{timestamp}"
        output_path = config.get_sessions_dir() / f"{name}.devsession"

    # Start chat
    chat_session(model, output_path, api_key=args.api_key)
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
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        output_path = config.get_sessions_dir() / f"ask_{timestamp}.devsession"

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


def cmd_watch(args):
    """Watch for new terminal windows and auto-launch GUI"""
    return watch_terminals()


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
  reccli chat --model gpt5-codex   # Chat with GPT-5 Codex (coding optimized)
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
        """
    )

    subparsers = parser.add_subparsers(dest='command', help='Commands')

    # Chat command (NEW - Native LLM)
    chat_parser = subparsers.add_parser('chat', help='Interactive chat with LLM')
    chat_parser.add_argument('-m', '--model', choices=[
        'claude', 'claude-sonnet', 'claude-opus', 'claude-haiku',
        'gpt5', 'gpt5-chat', 'gpt5-mini', 'gpt5-nano', 'gpt5-codex',
        'gpt4', 'gpt4o'
    ], help='Model to use')
    chat_parser.add_argument('-n', '--name', help='Session name')
    chat_parser.add_argument('-o', '--output', help='Output file path')
    chat_parser.add_argument('--api-key', help='API key (overrides config)')
    chat_parser.set_defaults(func=cmd_chat)

    # Ask command (NEW - One-shot query)
    ask_parser = subparsers.add_parser('ask', help='Ask a single question')
    ask_parser.add_argument('question', help='Question to ask')
    ask_parser.add_argument('-m', '--model', choices=[
        'claude', 'claude-sonnet', 'claude-opus', 'claude-haiku',
        'gpt5', 'gpt5-chat', 'gpt5-mini', 'gpt5-nano', 'gpt5-codex',
        'gpt4', 'gpt4o'
    ], help='Model to use')
    ask_parser.add_argument('-o', '--output', help='Output file path')
    ask_parser.add_argument('--api-key', help='API key (overrides config)')
    ask_parser.set_defaults(func=cmd_ask)

    # Config command (NEW - Manage API keys)
    config_parser = subparsers.add_parser('config', help='Manage configuration')
    config_parser.add_argument('--anthropic-key', help='Set Anthropic API key')
    config_parser.add_argument('--openai-key', help='Set OpenAI API key')
    config_parser.add_argument('--default-model', choices=[
        'claude', 'gpt5', 'gpt5-chat', 'gpt5-codex', 'gpt4', 'gpt4o'
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
    export_parser.add_argument('-f', '--format', choices=['txt', 'md', 'cast'], default='md', help='Export format')
    export_parser.add_argument('-o', '--output', help='Output file path')
    export_parser.set_defaults(func=cmd_export)

    # Watch command (NEW - Auto-launch GUI for terminals)
    watch_parser = subparsers.add_parser('watch', help='Watch for new terminal windows and auto-launch GUI')
    watch_parser.set_defaults(func=cmd_watch)

    # Parse arguments
    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        return 0

    # Execute command
    return args.func(args)


if __name__ == '__main__':
    sys.exit(main())
