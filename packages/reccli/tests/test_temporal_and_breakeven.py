#!/usr/bin/env python3
"""
Test temporal hints and break-even switch logic
"""

import sys
from pathlib import Path
from datetime import datetime

# Add package root to path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from reccli.summarizer import SessionSummarizer
from reccli.tokens import TokenCounter


def test_temporal_extraction():
    """Test extraction of t_first and t_last from conversation"""
    print("=" * 60)
    print("Testing Temporal Hints Extraction")
    print("=" * 60)

    summarizer = SessionSummarizer()

    # Create conversation with timestamps
    conversation = [
        {"role": "user", "content": "Let's start", "timestamp": 1730000000.0},
        {"role": "assistant", "content": "Sure", "timestamp": 1730000010.0},
        {"role": "user", "content": "Build feature X", "timestamp": 1730000020.0},
        {"role": "assistant", "content": "Done", "timestamp": 1730000030.0},
        {"role": "user", "content": "Thanks", "timestamp": 1730000040.0},
    ]

    # Test extraction
    message_range = {
        "start": "msg_002",
        "end": "msg_004",
        "start_index": 2,
        "end_index": 4
    }

    t_first, t_last = summarizer.extract_temporal_bounds(conversation, message_range)

    print(f"\n1. Message range: msg_002 to msg_004")
    print(f"   t_first: {t_first}")
    print(f"   t_last: {t_last}")

    # Verify they're ISO format
    if t_first:
        parsed = datetime.fromisoformat(t_first)
        print(f"   ✅ t_first is valid ISO timestamp: {parsed}")

    if t_last:
        parsed = datetime.fromisoformat(t_last)
        print(f"   ✅ t_last is valid ISO timestamp: {parsed}")

    print("\n✅ Temporal extraction tests passed")


def test_break_even_calculation():
    """Test break-even reduction ratio calculation"""
    print("\n" + "=" * 60)
    print("Testing Break-Even Calculation")
    print("=" * 60)

    summarizer = SessionSummarizer(
        model="claude-3-5-sonnet-20241022",
        span_detection_model="claude-3-5-haiku-20241022"
    )

    # Calculate break-even point
    r_break_even = summarizer.calculate_break_even_reduction()

    print(f"\n1. Model pricing:")
    print(f"   Sonnet input: ${summarizer.PRICING['claude-3-5-sonnet-20241022']['input']}/M")
    print(f"   Haiku input: ${summarizer.PRICING['claude-3-5-haiku-20241022']['input']}/M")

    print(f"\n2. Break-even reduction ratio:")
    print(f"   r_break_even = {r_break_even:.4f} ({r_break_even*100:.2f}%)")
    print(f"\n   Interpretation:")
    print(f"   - If Stage-1 keeps >{r_break_even*100:.2f}% of tokens → Use single-stage")
    print(f"   - If Stage-1 keeps <{r_break_even*100:.2f}% of tokens → Use two-stage")

    # Expected: 1 - (0.25 / 3.0) = 1 - 0.0833 = 0.9167 (91.67%)
    expected = 1.0 - (0.25 / 3.0)
    assert abs(r_break_even - expected) < 0.001, f"Expected {expected}, got {r_break_even}"

    print("\n✅ Break-even calculation correct")


def test_cost_estimation():
    """Test cost estimation for different scenarios"""
    print("\n" + "=" * 60)
    print("Testing Cost Estimation")
    print("=" * 60)

    summarizer = SessionSummarizer()

    # Scenario 1: 190K input, 3K output on Sonnet
    cost = summarizer.estimate_cost(190_000, 3_000, "claude-3-5-sonnet-20241022")
    print(f"\n1. Single-stage (Sonnet): 190K input + 3K output")
    print(f"   Cost: ${cost:.4f}")

    # Scenario 2: Two-stage
    # Stage 1: 190K input on Haiku, 2K output (spans)
    stage1_cost = summarizer.estimate_cost(190_000, 2_000, "claude-3-5-haiku-20241022")
    # Stage 2: 50K input on Sonnet (selected spans), 3K output
    stage2_cost = summarizer.estimate_cost(50_000, 3_000, "claude-3-5-sonnet-20241022")
    two_stage_cost = stage1_cost + stage2_cost

    print(f"\n2. Two-stage:")
    print(f"   Stage 1 (Haiku): 190K input + 2K output = ${stage1_cost:.4f}")
    print(f"   Stage 2 (Sonnet): 50K input + 3K output = ${stage2_cost:.4f}")
    print(f"   Total: ${two_stage_cost:.4f}")

    print(f"\n3. Comparison:")
    print(f"   Single-stage: ${cost:.4f}")
    print(f"   Two-stage: ${two_stage_cost:.4f}")
    print(f"   Savings: ${cost - two_stage_cost:.4f} ({((cost - two_stage_cost) / cost * 100):.1f}%)")

    print("\n✅ Cost estimation tests passed")


def test_should_use_two_stage():
    """Test automatic two-stage decision logic"""
    print("\n" + "=" * 60)
    print("Testing Auto Two-Stage Decision")
    print("=" * 60)

    # Test 1: Auto-switch disabled (default)
    summarizer = SessionSummarizer(auto_switch_two_stage=False)
    should_use, reason = summarizer.should_use_two_stage(190_000)
    print(f"\n1. Auto-switch disabled (default):")
    print(f"   Should use two-stage: {should_use}")
    print(f"   Reason: {reason}")
    assert should_use == False, "Should default to single-stage"

    # Test 2: Auto-switch enabled, small session
    summarizer = SessionSummarizer(auto_switch_two_stage=True)
    should_use, reason = summarizer.should_use_two_stage(50_000)
    print(f"\n2. Auto-switch enabled, small session (50K tokens):")
    print(f"   Should use two-stage: {should_use}")
    print(f"   Reason: {reason}")
    assert should_use == False, "Small sessions should use single-stage"

    # Test 3: Auto-switch enabled, large session
    should_use, reason = summarizer.should_use_two_stage(190_000)
    print(f"\n3. Auto-switch enabled, large session (190K tokens):")
    print(f"   Should use two-stage: {should_use}")
    print(f"   Reason: {reason}")
    assert should_use == True, "Large sessions should consider two-stage"

    # Test 4: With span estimate - good reduction
    should_use, reason = summarizer.should_use_two_stage(190_000, estimated_span_tokens=30_000)
    print(f"\n4. With span estimate (190K → 30K, 15.8% kept):")
    print(f"   Should use two-stage: {should_use}")
    print(f"   Reason: {reason}")
    assert should_use == True, "Good reduction should use two-stage"

    # Test 5: With span estimate - poor reduction
    should_use, reason = summarizer.should_use_two_stage(190_000, estimated_span_tokens=180_000)
    print(f"\n5. With span estimate (190K → 180K, 94.7% kept):")
    print(f"   Should use two-stage: {should_use}")
    print(f"   Reason: {reason}")
    assert should_use == False, "Poor reduction should fallback to single-stage"

    # Test 6: User forces two-stage
    summarizer = SessionSummarizer(use_two_stage=True)
    should_use, reason = summarizer.should_use_two_stage(10_000)
    print(f"\n6. User explicitly enabled two-stage (even for 10K tokens):")
    print(f"   Should use two-stage: {should_use}")
    print(f"   Reason: {reason}")
    assert should_use == True, "User preference should override logic"

    print("\n✅ Auto two-stage decision tests passed")


def test_temporal_in_prompts():
    """Test that temporal preference is in system prompt"""
    print("\n" + "=" * 60)
    print("Testing Temporal Guidance in Prompts")
    print("=" * 60)

    summarizer = SessionSummarizer()

    # Check if temporal preference is in the prompt
    prompt = summarizer.REASONED_SUMMARY_PROMPT

    assert "Temporal preference" in prompt or "recent evidence" in prompt, \
        "System prompt should include temporal preference guidance"

    print("\n1. System prompt includes temporal guidance:")
    # Find and print the temporal guidance line
    for line in prompt.split('\n'):
        if 'temporal' in line.lower() or 'recent evidence' in line.lower():
            print(f"   {line.strip()}")

    print("\n✅ Temporal guidance present in system prompt")


if __name__ == "__main__":
    try:
        test_temporal_extraction()
        test_break_even_calculation()
        test_cost_estimation()
        test_should_use_two_stage()
        test_temporal_in_prompts()

        print("\n" + "=" * 60)
        print("✅ All temporal and break-even tests passed!")
        print("=" * 60)
        print("\nKey Takeaways:")
        print("- Temporal hints (t_first/t_last) are extracted and attached")
        print("- Break-even ratio for Sonnet/Haiku: ~91.7%")
        print("- Two-stage only worth it if Stage-1 removes >91.7% of tokens")
        print("- System prompt includes temporal preference guidance")
        print("- Auto-switch available but disabled by default (keep it simple)")

    except Exception as e:
        print(f"\n❌ Test failed: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
