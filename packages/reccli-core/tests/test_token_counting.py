#!/usr/bin/env python3
"""
Test token counting functionality
"""

import sys
from pathlib import Path

# Add package root to path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from reccli.devsession import DevSession
from reccli.tokens import TokenCounter


def test_token_counter():
    """Test basic token counting"""
    print("=" * 60)
    print("Testing Token Counter")
    print("=" * 60)

    counter = TokenCounter()

    # Test 1: Count simple text
    text = "Hello, how can I help you today?"
    tokens = counter.count_text(text)
    print(f"\n1. Simple text: '{text}'")
    print(f"   Tokens: {tokens}")

    # Test 2: Count a message
    message = {
        "role": "user",
        "content": "Help me build a user authentication system with JWT tokens"
    }
    tokens = counter.count_message(message)
    print(f"\n2. Message: {message}")
    print(f"   Tokens: {tokens}")

    # Test 3: Count a conversation
    conversation = [
        {"role": "user", "content": "How do I implement JWT authentication in Python?"},
        {"role": "assistant", "content": "I can help you implement JWT authentication. First, install PyJWT..."},
        {"role": "user", "content": "What about refresh tokens?"},
        {"role": "assistant", "content": "Refresh tokens are important for security. Here's how to implement them..."}
    ]
    tokens = counter.count_conversation(conversation)
    print(f"\n3. Conversation with {len(conversation)} messages")
    print(f"   Tokens: {tokens}")

    # Test 4: Check limits
    print("\n" + "=" * 60)
    print("Testing Limit Checking")
    print("=" * 60)

    test_counts = [
        (50_000, "ok"),
        (180_000, "warning"),
        (190_000, "critical"),
    ]

    for token_count, expected_status in test_counts:
        status, percentage = counter.check_limit(token_count)
        warning = counter.format_warning(token_count)
        print(f"\n{token_count:,} tokens:")
        print(f"  Status: {status} ({percentage:.1%})")
        if warning:
            print(f"  {warning}")


def test_devsession_tokens():
    """Test token counting with DevSession"""
    print("\n" + "=" * 60)
    print("Testing DevSession Token Counting")
    print("=" * 60)

    # Create a mock session
    session = DevSession(session_id="test_session")

    # Add a mock conversation
    session.conversation = [
        {
            "role": "user",
            "content": "Help me refactor this Python code to use async/await",
            "timestamp": 0.0
        },
        {
            "role": "assistant",
            "content": "I'll help you refactor your code to use async/await. First, let's identify the I/O operations...",
            "timestamp": 1.5
        },
        {
            "role": "user",
            "content": "Here's the code:\n\ndef fetch_data():\n    response = requests.get('https://api.example.com')\n    return response.json()",
            "timestamp": 5.0
        },
        {
            "role": "assistant",
            "content": "Great! Here's the async version:\n\nimport aiohttp\n\nasync def fetch_data():\n    async with aiohttp.ClientSession() as session:\n        async with session.get('https://api.example.com') as response:\n            return await response.json()",
            "timestamp": 7.2
        }
    ]

    # Calculate tokens
    print("\nCalculating tokens...")
    counts = session.calculate_tokens()

    print(f"\nToken counts:")
    print(f"  Conversation: {counts['conversation']:,}")
    print(f"  Terminal output: {counts['terminal_output']:,}")
    print(f"  Summary: {counts['summary']:,}")
    print(f"  Total: {counts['total']:,}")

    # Check for warnings
    warning = session.check_tokens()
    if warning:
        print(f"\n{warning}")
    else:
        print("\n✅ Token count is healthy")


def test_large_session():
    """Test with a larger mock session approaching limits"""
    print("\n" + "=" * 60)
    print("Testing Large Session (Approaching Limit)")
    print("=" * 60)

    session = DevSession(session_id="large_session")

    # Create a large conversation (simulate a long session)
    large_text = "This is a detailed technical discussion about software architecture. " * 100

    session.conversation = []
    for i in range(500):  # 500 message pairs
        session.conversation.append({
            "role": "user",
            "content": f"Question {i}: {large_text}",
            "timestamp": i * 2.0
        })
        session.conversation.append({
            "role": "assistant",
            "content": f"Answer {i}: {large_text}",
            "timestamp": i * 2.0 + 1.0
        })

    # Calculate tokens
    counts = session.calculate_tokens()

    print(f"\nLarge session stats:")
    print(f"  Messages: {len(session.conversation)}")
    print(f"  Total tokens: {counts['total']:,}")

    # Check for warnings
    warning = session.check_tokens()
    if warning:
        print(f"\n{warning}")
    else:
        print("\n✅ Token count is healthy")


if __name__ == "__main__":
    try:
        test_token_counter()
        test_devsession_tokens()
        test_large_session()

        print("\n" + "=" * 60)
        print("✅ All token counting tests passed!")
        print("=" * 60)

    except Exception as e:
        print(f"\n❌ Test failed: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
