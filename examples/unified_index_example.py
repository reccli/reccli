#!/usr/bin/env python3
"""
Example usage of Unified Vector Index
Demonstrates cross-session search functionality
"""

import sys
from pathlib import Path

# Add src to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.devsession import (
    build_unified_index,
    update_index_with_new_session,
    search_all_sessions,
    search_with_filters,
    load_full_context_from_result,
    search_recent_sessions_only,
    validate_index
)
from src.devsession.embeddings import embed_text


def example_build_index():
    """Example: Build unified index from sessions"""
    print("=" * 60)
    print("Example 1: Building Unified Index")
    print("=" * 60)

    # Assume we have a project with .devsessions/ folder
    project_dir = Path.cwd()
    sessions_dir = project_dir / '.devsessions'

    if not sessions_dir.exists():
        print(f"Creating example sessions directory: {sessions_dir}")
        sessions_dir.mkdir(parents=True)
        print("⚠️  No .devsession files found. Add some session files first.")
        return

    # Build index
    index = build_unified_index(sessions_dir)

    print(f"\n✓ Index created:")
    print(f"  Total sessions: {index['total_sessions']}")
    print(f"  Total vectors: {index['total_vectors']}")
    print(f"  Model: {index['embedding_model']}")
    print()


def example_search():
    """Example: Search across all sessions"""
    print("=" * 60)
    print("Example 2: Cross-Session Search")
    print("=" * 60)

    project_dir = Path.cwd()

    # Search for "webhook" across all sessions
    query = "webhook signature verification"
    print(f"\nSearching for: '{query}'")

    results = search_all_sessions(
        project_dir,
        query,
        embedding_func=embed_text,
        top_k=5
    )

    if not results:
        print("No results found (no index or no matches)")
        return

    print(f"\nFound {len(results)} results:\n")

    for i, result in enumerate(results, 1):
        print(f"{i}. Session: {result['session']}")
        print(f"   Similarity: {result['similarity']:.3f}")
        print(f"   Type: {result['type']}")
        print(f"   Preview: {result['content_preview'][:100]}...")
        print()


def example_filtered_search():
    """Example: Search with filters"""
    print("=" * 60)
    print("Example 3: Filtered Search")
    print("=" * 60)

    project_dir = Path.cwd()

    # Search only decisions about authentication
    query = "authentication strategy"
    print(f"\nSearching for decisions about: '{query}'")

    results = search_with_filters(
        project_dir,
        query,
        embedding_func=embed_text,
        filters={'types': ['decision']},
        top_k=5
    )

    print(f"\nFound {len(results)} decision(s):\n")

    for i, result in enumerate(results, 1):
        print(f"{i}. {result['content_preview'][:80]}...")
        print(f"   Similarity: {result['similarity']:.3f}")
        print()


def example_load_context():
    """Example: Load full context from search result"""
    print("=" * 60)
    print("Example 4: Loading Full Context")
    print("=" * 60)

    project_dir = Path.cwd()

    # First, search for something
    query = "webhook"
    print(f"\nSearching for: '{query}'")

    results = search_all_sessions(
        project_dir,
        query,
        embedding_func=embed_text,
        top_k=1
    )

    if not results:
        print("No results found")
        return

    print(f"\nTop result: {results[0]['content_preview'][:100]}...")

    # Load full context
    print("\nLoading full context...")
    context = load_full_context_from_result(project_dir, results[0])

    if 'error' in context:
        print(f"Error: {context['message']}")
        return

    print(f"\n✓ Context loaded:")
    print(f"  Session: {context['session_metadata'].get('session_id', 'unknown')}")
    print(f"  Summary: {context['session_summary'][:100]}...")
    print(f"  Message: {context['message']['content'][:150]}...")
    print(f"  Context messages: {len(context['context_messages'])}")

    if context['summary_context']:
        print(f"  Linked to: {context['summary_context']['type']}")
        print(f"  Summary: {context['summary_context'].get('summary', 'N/A')[:100]}...")
    print()


def example_recent_search():
    """Example: Search recent sessions only (fast path)"""
    print("=" * 60)
    print("Example 5: Search Recent Sessions Only")
    print("=" * 60)

    project_dir = Path.cwd()

    query = "error handling"
    print(f"\nSearching last 3 sessions for: '{query}'")

    results = search_recent_sessions_only(
        project_dir,
        query,
        embedding_func=embed_text,
        num_sessions=3,
        top_k=5
    )

    print(f"\nFound {len(results)} results in recent sessions:\n")

    for i, result in enumerate(results, 1):
        print(f"{i}. {result['content_preview'][:80]}...")
        print(f"   Session: {result['session']}")
        print()


def example_validate():
    """Example: Validate index integrity"""
    print("=" * 60)
    print("Example 6: Validate Index")
    print("=" * 60)

    project_dir = Path.cwd()
    sessions_dir = project_dir / '.devsessions'

    print("\nValidating index...")
    validation = validate_index(sessions_dir)

    if validation['valid']:
        print("✓ Index is valid")
        print(f"  Sessions: {validation['total_sessions']}")
        print(f"  Vectors: {validation['total_vectors']}")
    else:
        print("❌ Index validation failed:")
        for error in validation['errors']:
            print(f"  - {error}")

    if validation['warnings']:
        print("\n⚠️  Warnings:")
        for warning in validation['warnings']:
            print(f"  - {warning}")
    print()


def main():
    """Run all examples"""
    print("\n" + "=" * 60)
    print("RecCli Unified Vector Index - Examples")
    print("=" * 60 + "\n")

    examples = [
        ("Build Index", example_build_index),
        ("Search All Sessions", example_search),
        ("Filtered Search", example_filtered_search),
        ("Load Context", example_load_context),
        ("Recent Search", example_recent_search),
        ("Validate Index", example_validate)
    ]

    print("Available examples:")
    for i, (name, _) in enumerate(examples, 1):
        print(f"  {i}. {name}")
    print(f"  0. Run all\n")

    try:
        choice = input("Choose example (0-6): ").strip()

        if choice == '0':
            for name, func in examples:
                func()
                input("\nPress Enter to continue...")
        elif choice.isdigit() and 1 <= int(choice) <= len(examples):
            examples[int(choice) - 1][1]()
        else:
            print("Invalid choice")
    except KeyboardInterrupt:
        print("\n\nExiting...")
    except Exception as e:
        print(f"\n❌ Error: {e}")
        import traceback
        traceback.print_exc()


if __name__ == '__main__':
    main()
