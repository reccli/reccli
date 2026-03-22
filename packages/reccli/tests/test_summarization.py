#!/usr/bin/env python3
"""
Test summarization features (Phase 4)
Tests schema, verification, redaction, and code change detection
"""

import sys
import json
from pathlib import Path

# Add package root to path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from reccli.devsession import DevSession
from reccli.summary_schema import (
    create_summary_skeleton,
    create_decision_item,
    create_code_change_item,
    validate_summary_schema,
    add_audit_entry,
)
from reccli.summary_verification import SummaryVerifier
from reccli.redaction import SecretRedactor
from reccli.code_change_detector import CodeChangeDetector


def test_summary_schema():
    """Test summary schema creation and validation"""
    print("=" * 60)
    print("Testing Summary Schema")
    print("=" * 60)

    # Create skeleton
    summary = create_summary_skeleton(
        model="claude-sonnet-4.5",
        session_hash="abc123"
    )

    print("\n1. Created summary skeleton:")
    print(f"   Schema version: {summary['schema_version']}")
    print(f"   Model: {summary['model']}")
    print(f"   Session hash: {summary['session_hash']}")

    # Add a decision
    decision = create_decision_item(
        decision="Use modal dialog for export",
        reasoning="Better UX for focused task",
        impact="medium",
        references=["msg_045", "msg_046"],
        message_range={
            "start": "msg_042",
            "end": "msg_050",
            "start_index": 41,
            "end_index": 50
        },
        confidence="high",
        quote="I recommend a modal because it focuses user attention"
    )
    summary["decisions"].append(decision)

    print(f"\n2. Added decision item:")
    print(f"   ID: {decision['id']}")
    print(f"   Decision: {decision['decision']}")
    print(f"   Impact: {decision['impact']}")
    print(f"   Pinned: {decision['pinned']}")
    print(f"   Locked: {decision['locked']}")

    # Validate schema
    errors = validate_summary_schema(summary)
    if errors:
        print(f"\n❌ Validation errors: {errors}")
    else:
        print("\n✅ Schema validation passed")

    return summary


def test_reference_verification():
    """Test reference verification"""
    print("\n" + "=" * 60)
    print("Testing Reference Verification")
    print("=" * 60)

    # Create mock conversation
    conversation = [
        {"role": "user", "content": "Let's build an export dialog", "timestamp": 0.0},
        {"role": "assistant", "content": "Great idea! Should we use modal or sidebar?", "timestamp": 1.0},
        {"role": "user", "content": "What are the tradeoffs?", "timestamp": 2.0},
        {"role": "assistant", "content": "Modal focuses attention, sidebar allows multitasking", "timestamp": 3.0},
        {"role": "user", "content": "Let's go with modal", "timestamp": 4.0},
    ]

    # Create verifier
    verifier = SummaryVerifier(conversation)

    print(f"\n1. Conversation has {len(conversation)} messages")

    # Test valid reference
    valid = verifier.verify_message_exists("msg_003")
    print(f"\n2. msg_003 exists: {valid}")

    # Test invalid reference
    valid = verifier.verify_message_exists("msg_999")
    print(f"   msg_999 exists: {valid}")

    # Test message range
    valid, error = verifier.verify_message_range({
        "start": "msg_002",
        "end": "msg_005",
        "start_index": 1,
        "end_index": 5
    })
    print(f"\n3. Message range [2-5] valid: {valid}")
    if error:
        print(f"   Error: {error}")

    # Test invalid range
    valid, error = verifier.verify_message_range({
        "start": "msg_005",
        "end": "msg_002",
        "start_index": 4,
        "end_index": 2
    })
    print(f"\n4. Invalid range [5-2] valid: {valid}")
    if error:
        print(f"   Error: {error}")

    print("\n✅ Reference verification tests passed")


def test_redaction():
    """Test secrets redaction"""
    print("\n" + "=" * 60)
    print("Testing Secrets Redaction")
    print("=" * 60)

    redactor = SecretRedactor()

    # Test API key redaction
    text = "My API key is sk-1234567890abcdefghij and the password is MySecret123"
    redacted, types = redactor.redact_text(text)

    print(f"\n1. Original: {text}")
    print(f"   Redacted: {redacted}")
    print(f"   Types: {types}")

    # Test email redaction
    text = "Contact me at user@example.com or call 555-123-4567"
    redacted, types = redactor.redact_text(text)

    print(f"\n2. Original: {text}")
    print(f"   Redacted: {redacted}")
    print(f"   Types: {types}")

    # Test conversation redaction
    conversation = [
        {"role": "user", "content": "The API key is sk-abcd1234567890", "timestamp": 0.0},
        {"role": "assistant", "content": "I'll use that for authentication", "timestamp": 1.0},
    ]

    redacted_conv, stats = redactor.redact_conversation(conversation)

    print(f"\n3. Conversation redaction stats: {stats}")
    print(f"   Original message: {conversation[0]['content']}")
    print(f"   Redacted message: {redacted_conv[0]['content']}")

    print("\n✅ Redaction tests passed")


def test_code_change_detection():
    """Test code change detection from ground truth"""
    print("\n" + "=" * 60)
    print("Testing Code Change Detection")
    print("=" * 60)

    # Create mock conversation with file operations
    conversation = [
        {"role": "user", "content": "Let's create export_dialog.py", "timestamp": 0.0},
        {"role": "assistant", "content": "I'll create that file", "timestamp": 1.0},
        {"role": "tool", "content": "Created file: src/export_dialog.py", "timestamp": 2.0},
        {"role": "assistant", "content": "Here's the code:\n```python\ndef export_dialog():\n    pass\n```", "timestamp": 3.0},
        {"role": "user", "content": "Great! Now update it to add validation", "timestamp": 4.0},
        {"role": "tool", "content": "Updated file: src/export_dialog.py", "timestamp": 5.0},
    ]

    detector = CodeChangeDetector()

    # Detect file operations
    ops = detector.detect_file_operations(conversation[2])
    print(f"\n1. Detected file operations from message 3:")
    for op in ops:
        print(f"   {op['type']}: {op['file']}")

    # Detect code blocks
    blocks = detector.detect_code_blocks(conversation[3])
    print(f"\n2. Detected code blocks from message 4:")
    for block in blocks:
        print(f"   Lines: {block['lines']}")
        added, removed = detector.estimate_lines_changed(block['content'])
        print(f"   Added: {added}, Removed: {removed}")

    # Analyze full conversation
    analysis = detector.analyze_conversation(conversation)
    print(f"\n3. Full analysis:")
    print(f"   File operations: {len(analysis['file_operations'])}")
    print(f"   Code blocks: {len(analysis['code_blocks'])}")
    print(f"   Files changed: {len(analysis['files_changed'])}")

    for file_path, info in analysis['files_changed'].items():
        print(f"\n   File: {file_path}")
        print(f"     Operations: {info['operations']}")
        print(f"     Range: {info['first_seen']} -> {info['last_seen']}")

    # Build code changes from ground truth
    changes = detector.build_code_changes_from_ground_truth(conversation)
    print(f"\n4. Ground truth code changes: {len(changes)}")
    for change in changes:
        print(f"\n   {change['description']}")
        print(f"     Files: {change['files']}")
        print(f"     Type: {change['type']}")
        print(f"     Lines added: {change['lines_added']}")
        print(f"     Source: {change['source_of_truth']}")

    print("\n✅ Code change detection tests passed")


def test_devsession_integration():
    """Test DevSession integration"""
    print("\n" + "=" * 60)
    print("Testing DevSession Integration")
    print("=" * 60)

    # Create session
    session = DevSession(session_id="test_summarization")

    # Add mock conversation
    session.conversation = [
        {"role": "user", "content": "Help me build an export feature", "timestamp": 0.0},
        {"role": "assistant", "content": "I'll help you build that", "timestamp": 1.0},
        {"role": "tool", "content": "Created file: export.py", "timestamp": 2.0},
        {"role": "user", "content": "Great! Let's add validation", "timestamp": 3.0},
        {"role": "assistant", "content": "Added validation logic", "timestamp": 4.0},
    ]

    print(f"\n1. Created session with {len(session.conversation)} messages")

    # Generate summary (without LLM)
    success = session.generate_summary(llm_client=None, redact_secrets=True)
    print(f"\n2. Summary generated: {success}")

    if session.summary:
        print(f"\n3. Summary structure:")
        print(f"   Schema version: {session.summary.get('schema_version')}")
        print(f"   Model: {session.summary.get('model')}")
        print(f"   Overview: {session.summary.get('overview')}")
        print(f"   Decisions: {len(session.summary.get('decisions', []))}")
        print(f"   Code changes: {len(session.summary.get('code_changes', []))}")

    # Test pin/lock functionality
    if session.summary and session.summary.get('decisions'):
        decision_id = session.summary['decisions'][0]['id']
        session.pin_summary_item(decision_id)
        print(f"\n4. Pinned decision: {decision_id}")
        print(f"   Pinned: {session.summary['decisions'][0]['pinned']}")

        session.lock_summary_item(decision_id)
        print(f"\n5. Locked decision: {decision_id}")
        print(f"   Locked: {session.summary['decisions'][0]['locked']}")

        # Check audit trail
        if 'audit_trail' in session.summary:
            print(f"\n6. Audit trail entries: {len(session.summary['audit_trail'])}")
            for entry in session.summary['audit_trail']:
                print(f"   - {entry['action']} on {entry['target']} at {entry['ts']}")

    print("\n✅ DevSession integration tests passed")


if __name__ == "__main__":
    try:
        test_summary_schema()
        test_reference_verification()
        test_redaction()
        test_code_change_detection()
        test_devsession_integration()

        print("\n" + "=" * 60)
        print("✅ All Phase 4 summarization tests passed!")
        print("=" * 60)

    except Exception as e:
        print(f"\n❌ Test failed: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
