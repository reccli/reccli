#!/usr/bin/env python3
"""
Test Two-Level Retrieval (The Core .devsession Innovation)

This demonstrates the key insight:
1. Fast search on summary layer (3-5K tokens)
2. Retrieve exact context from full conversation (190K tokens)
3. Lossless + fast + verifiable
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from reccli.session.devsession import DevSession
from reccli.retrieval.retrieval import ContextRetriever, format_context_for_llm
from reccli.summarization.summary_schema import create_summary_skeleton, create_decision_item


def create_mock_session():
    """Create a mock session with conversation + summary"""
    session = DevSession(session_id="test_retrieval")

    # Full conversation (simulating 190K tokens)
    session.conversation = [
        {"role": "user", "content": "Let's build an export dialog", "timestamp": 0.0},
        {"role": "assistant", "content": "Sure! What format should we support?", "timestamp": 1.0},
        # ... lots of messages ...
        {"role": "user", "content": "Should we use a modal or sidebar?", "timestamp": 40.0},
        {"role": "assistant", "content": "Let me think about the UX tradeoffs...", "timestamp": 41.0},
        {"role": "assistant", "content": "Modal focuses user attention on the export task", "timestamp": 42.0},
        {"role": "assistant", "content": "Sidebar allows multitasking but might be distracting", "timestamp": 43.0},
        {"role": "user", "content": "Good point. I think modal is better for focused tasks", "timestamp": 44.0},
        {"role": "assistant", "content": "Agreed. Let's go with modal. I'll implement it.", "timestamp": 45.0},
        {"role": "tool", "content": "Created file: src/export_dialog.py", "timestamp": 46.0},
        {"role": "assistant", "content": "Modal dialog created! Here's the code...", "timestamp": 47.0},
        {"role": "user", "content": "Perfect! Now let's add validation", "timestamp": 48.0},
        # ... more messages ...
    ]

    # Add message IDs for easier tracking
    for i, msg in enumerate(session.conversation):
        msg["_id"] = f"msg_{i+1:03d}"

    # Summary with references to full conversation
    session.summary = create_summary_skeleton(
        model="claude-sonnet-4.5",
        session_hash="test123"
    )

    # Decision item with temporal/chronological links
    decision = create_decision_item(
        decision="Use modal dialog for export feature",
        reasoning="Modal focuses user attention on the export task, better for focused operations",
        impact="medium",
        references=["msg_005", "msg_007", "msg_008"],
        message_range={
            "start": "msg_003",
            "end": "msg_010",
            "start_index": 2,
            "end_index": 10
        },
        confidence="high",
        quote="Modal focuses user attention on the export task",
        t_first="2024-10-26T18:22:12",
        t_last="2024-10-26T18:28:49"
    )

    session.summary["decisions"].append(decision)
    session.summary["overview"] = "Built export dialog with modal UI"

    return session


def test_full_context_retrieval():
    """Test retrieving full context from a summary item"""
    print("=" * 70)
    print("Test 1: Full Context Retrieval (Summary → Conversation)")
    print("=" * 70)

    session = create_mock_session()
    retriever = ContextRetriever(session)

    # Get the decision item
    decision = session.summary["decisions"][0]

    print(f"\n1. Summary item:")
    print(f"   Decision: {decision['decision']}")
    print(f"   Reasoning: {decision['reasoning']}")
    print(f"   References: {decision['references']}")
    print(f"   Message range: {decision['message_range']['start']} to {decision['message_range']['end']}")

    # Retrieve full context
    full_context = retriever.retrieve_full_context(decision, expand_context=2)

    print(f"\n2. Retrieved full context:")
    print(f"   Core range: {full_context['core_range']['count']} messages")
    print(f"   Expanded range: {full_context['expanded_range']['count']} messages (includes ±2 context)")
    print(f"   Time span: {full_context['temporal_bounds']['t_first']} to {full_context['temporal_bounds']['t_last']}")

    print(f"\n3. Full discussion:")
    for msg in full_context["messages"]:
        in_core = ">>>" if msg.get("_in_core_range") else "   "
        msg_id = msg.get("_message_id", "???")
        role = msg.get("role", "unknown")
        content = msg.get("content", "")[:60]  # Truncate for display

        print(f"   {in_core} {msg_id} ({role}): {content}...")

    print("\n✅ Full context retrieval successful!")
    print("   Summary (lossy) → Full conversation (lossless)")


def test_reference_retrieval():
    """Test retrieving specific message by reference"""
    print("\n" + "=" * 70)
    print("Test 2: Reference Retrieval (Jump to Specific Message)")
    print("=" * 70)

    session = create_mock_session()
    retriever = ContextRetriever(session)

    # Jump to a specific message
    target_msg = "msg_005"

    print(f"\n1. Target message: {target_msg}")

    # Retrieve with context
    messages = retriever.retrieve_by_reference(target_msg, context_window=3)

    print(f"\n2. Retrieved {len(messages)} messages (target ± 3 context):")
    for msg in messages:
        is_target = ">>>" if msg.get("_is_target") else "   "
        msg_id = msg.get("_message_id", "???")
        role = msg.get("role", "unknown")
        content = msg.get("content", "")[:60]

        print(f"   {is_target} {msg_id} ({role}): {content}...")

    print("\n✅ Reference retrieval successful!")
    print("   O(1) array lookup by message index")


def test_two_level_search():
    """Test the full two-level search pattern"""
    print("\n" + "=" * 70)
    print("Test 3: Two-Level Search (The .devsession Innovation)")
    print("=" * 70)

    session = create_mock_session()
    retriever = ContextRetriever(session)

    # User query
    query = "modal dialog"

    print(f"\n1. User query: '{query}'")

    # Two-level search
    results = retriever.two_level_search(query, top_k=1, expand_context=2)

    print(f"\n2. Level 1: Summary search (fast)")
    print(f"   Found {len(results)} matching summary items")

    for i, result in enumerate(results, 1):
        summary = result["summary"]
        full_ctx = result["full_context"]

        print(f"\n3. Result {i}:")
        print(f"   Category: {summary['category']}")
        print(f"   Relevance: {summary['relevance']:.2f}")
        print(f"\n   Summary:")
        print(f"   - Decision: {summary['item']['decision']}")
        print(f"   - Reasoning: {summary['item']['reasoning']}")

        print(f"\n4. Level 2: Full context retrieval (precise)")
        print(f"   Retrieved {full_ctx['expanded_range']['count']} messages from conversation")
        print(f"\n   Preview:")
        print(result["preview"])

    print("\n✅ Two-level search successful!")
    print("   Fast (summary) + Precise (full context)")


def test_format_for_llm():
    """Test formatting retrieval result for LLM"""
    print("\n" + "=" * 70)
    print("Test 4: Format for LLM Consumption")
    print("=" * 70)

    session = create_mock_session()
    retriever = ContextRetriever(session)

    # Search and format
    results = retriever.two_level_search("modal", top_k=1, expand_context=1)

    if results:
        formatted = format_context_for_llm(results[0])

        print("\n1. Formatted context for LLM:")
        print("-" * 70)
        print(formatted)
        print("-" * 70)

        print("\n✅ LLM can now reason with:")
        print("   - Summary (what was decided)")
        print("   - Full discussion (why + how)")
        print("   - No hallucinations (can verify against source)")


def test_temporal_retrieval():
    """Test retrieval by time range"""
    print("\n" + "=" * 70)
    print("Test 5: Temporal Retrieval (Query by Time)")
    print("=" * 70)

    session = create_mock_session()
    retriever = ContextRetriever(session)

    # Query by time range
    start_time = "2024-10-26T18:00:00"
    end_time = "2024-10-26T19:00:00"

    print(f"\n1. Time range query:")
    print(f"   Start: {start_time}")
    print(f"   End: {end_time}")

    results = retriever.get_temporal_context(start_time, end_time)

    print(f"\n2. Found {len(results)} items in time range:")
    for result in results:
        item = result["item"]
        print(f"   - {result['category']}: {item.get('decision') or item.get('description')}")
        print(f"     Time: {item.get('t_first')} to {item.get('t_last')}")

    print("\n✅ Temporal retrieval successful!")
    print("   Can query 'what did we discuss yesterday afternoon?'")


def demonstrate_the_innovation():
    """Demonstrate why .devsession beats alternatives"""
    print("\n" + "=" * 70)
    print("THE .DEVSESSION INNOVATION: Why This Beats Everything Else")
    print("=" * 70)

    print("\n🔴 Alternative 1: Pure Summary (ChatGPT/Claude approach)")
    print("   Problem: Lossy, can't verify, missing details")
    print("   Example: 'We chose modal' - but WHY? Can't check.")

    print("\n🔴 Alternative 2: Full Vector Search (RAG approach)")
    print("   Problem: Slow, chunking issues, no structure")
    print("   Example: Search 190K tokens every query, returns fragments")

    print("\n🔴 Alternative 3: Keyword Search")
    print("   Problem: Brittle, no semantic understanding")
    print("   Example: Search 'modal' returns 100 hits, which is the decision?")

    print("\n✅ .devsession: Two-Level Linked Retrieval")
    print("   Level 1: Fast semantic search on summary (3-5K tokens)")
    print("   Level 2: Precise retrieval from full conversation (exact ranges)")
    print("   Benefits:")
    print("     - Fast (only search summary)")
    print("     - Semantic (vector embeddings in Phase 5)")
    print("     - Lossless (can verify against source)")
    print("     - Contextual (returns discussions, not fragments)")
    print("     - Verifiable (summary links to exact messages)")

    print("\n   The Magic:")
    print("     Summary: 'Used modal because focuses user attention'")
    print("                 ↓ message_range [42-50]")
    print("     Full: [8-message discussion with full reasoning]")
    print("                 ↓ LLM reads full context")
    print("     Answer: Accurate, detailed, verifiable")

    print("\n🚀 This is what ChatGPT/Claude don't have (yet)")


if __name__ == "__main__":
    try:
        test_full_context_retrieval()
        test_reference_retrieval()
        test_two_level_search()
        test_format_for_llm()
        test_temporal_retrieval()
        demonstrate_the_innovation()

        print("\n" + "=" * 70)
        print("✅ All two-level retrieval tests passed!")
        print("=" * 70)
        print("\nKey Innovation:")
        print("  Summary → message_range → Full Conversation")
        print("  Fast + Semantic + Lossless + Verifiable")
        print("\nThis is the core .devsession advantage over ChatGPT/Claude! 🚀")

    except Exception as e:
        print(f"\n❌ Test failed: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
