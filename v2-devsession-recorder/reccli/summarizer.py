"""
Summarizer - Two-stage AI-powered session summarization
Stage 1: Span detection (cheap model)
Stage 2: Reasoned summary (better model)
"""

import json
from datetime import datetime
from typing import Dict, List, Optional, Tuple, Any

from .summary_schema import (
    create_summary_skeleton,
    create_decision_item,
    create_code_change_item,
    create_problem_solved_item,
    create_open_issue_item,
    create_next_step_item,
    add_causal_edge,
)
from .summary_verification import SummaryVerifier
from .redaction import redact_for_summarization
from .code_change_detector import CodeChangeDetector


class SessionSummarizer:
    """Two-stage session summarization with safety and verification"""

    # Stage 1: Span detection system prompt (cheap model)
    SPAN_DETECTION_PROMPT = """You are a development session analyzer. Your task is to identify key discussion spans in a coding conversation.

Find spans for these categories:
1. **Decisions** - Where technical choices were made
2. **Code changes** - Where code was written or modified
3. **Problems** - Where issues were solved
4. **Open issues** - Where problems were identified but not resolved
5. **Next steps** - Where future actions were discussed

For each span, output:
- Category
- Start message ID and index
- End message ID and index
- Brief topic (5-10 words)

Output JSON array of spans:
[
  {
    "category": "decision",
    "start": "msg_042",
    "end": "msg_050",
    "start_index": 42,
    "end_index": 50,
    "topic": "choosing modal vs sidebar for export dialog"
  },
  ...
]

Rules:
- Only include significant spans (skip pleasantries, small talk)
- Spans can overlap if topics interweave
- Keep topics brief and technical
"""

    # Stage 2: Reasoned summary system prompt (better model)
    REASONED_SUMMARY_PROMPT = """You are a development session summarizer. Your task is to analyze specific conversation spans and extract structured summaries.

Focus on:
- Key technical decisions made and **why**
- Code changes (what files were modified, what was added/changed)
- Problems that were solved and **how**
- Issues that remain open
- Next steps that were discussed

Output a JSON object with this structure:
{
  "overview": "1-2 sentence summary of what was accomplished",
  "decisions": [
    {
      "decision": "Clear statement of what was decided",
      "reasoning": "Why this approach was chosen",
      "impact": "low" | "medium" | "high",
      "alternatives_considered": ["other options discussed"],
      "references": ["msg_045", "msg_046"],  // Key messages (2-5 IDs)
      "message_range": {
        "start": "msg_042",
        "end": "msg_050",
        "start_index": 42,
        "end_index": 50
      },
      "confidence": "low" | "medium" | "high",
      "quote": "< 20 word quote from a referenced message evidencing the decision"
    }
  ],
  "code_changes": [
    {
      "files": ["path/to/file.js"],
      "description": "What was changed and why",
      "type": "feature" | "bugfix" | "refactor" | "test" | "docs",
      "references": ["msg_089", "msg_090"],
      "message_range": {...},
      "confidence": "medium"
    }
  ],
  "problems_solved": [
    {
      "problem": "Clear description",
      "solution": "How it was resolved",
      "references": ["msg_134"],
      "message_range": {...},
      "confidence": "high"
    }
  ],
  "open_issues": [
    {
      "issue": "What needs attention",
      "severity": "low" | "medium" | "high",
      "references": ["msg_201"],
      "message_range": {...},
      "confidence": "medium"
    }
  ],
  "next_steps": [
    {
      "action": "What should be done next",
      "priority": 1-5,
      "estimated_time": "30m" | "2h" | etc,
      "references": ["msg_215"],
      "message_range": {...},
      "confidence": "medium"
    }
  ]
}

CRITICAL RULES:
- **Never fabricate file paths, line counts, or dates.** If unknown, set to null.
- **Every decision must include a quote** (≤20 words) from a referenced message.
- **If conflicting proposals exist**, include alternatives_considered with rejection reasons.
- **Prefer tool outputs** (git/file events) over text for code metrics.
- **Set confidence low** if you're inferring rather than directly quoting.
- **Temporal preference:** Prefer more recent evidence unless an earlier canonical decision contradicts it.
  When messages conflict, favor the most recent unless explicitly overruled.
- Use temperature 0 for determinism.
"""

    # Pricing (per 1M tokens) - Update these as models change
    PRICING = {
        "claude-3-5-sonnet-20241022": {"input": 3.0, "output": 15.0},
        "claude-3-5-haiku-20241022": {"input": 0.25, "output": 1.25},
        "claude-opus-4": {"input": 15.0, "output": 75.0},
    }

    def __init__(
        self,
        llm_client = None,
        model: str = "claude-3-5-sonnet-20241022",
        use_two_stage: bool = False,
        span_detection_model: str = "claude-3-5-haiku-20241022",
        auto_switch_two_stage: bool = False,
        reduction_threshold: float = 0.5  # If Stage-1 keeps >50% of tokens, use single-stage instead
    ):
        """
        Initialize summarizer

        Args:
            llm_client: LLM client (e.g., Anthropic or OpenAI)
            model: Model for summarization (default: Sonnet)
            use_two_stage: Enable two-stage pipeline for very large sessions (default: False)
            span_detection_model: Model for span detection if two_stage enabled
            auto_switch_two_stage: Automatically decide single vs two-stage based on cost (default: False)
            reduction_threshold: If Stage-1 reduction ratio > this, fallback to single-stage (default: 0.5)
        """
        self.llm_client = llm_client
        self.model = model
        self.use_two_stage = use_two_stage
        self.span_detection_model = span_detection_model
        self.auto_switch_two_stage = auto_switch_two_stage
        self.reduction_threshold = reduction_threshold

    def format_conversation_for_llm(
        self,
        conversation: List[Dict],
        include_indices: bool = True
    ) -> str:
        """
        Format conversation for LLM consumption

        Args:
            conversation: List of message dicts
            include_indices: Whether to include message indices

        Returns:
            Formatted string
        """
        lines = []
        for i, msg in enumerate(conversation):
            msg_id = f"msg_{i+1:03d}"
            role = msg.get("role", "unknown")
            content = msg.get("content", "")

            if include_indices:
                lines.append(f"{msg_id} (index: {i+1}, {role}): {content}")
            else:
                lines.append(f"{msg_id} ({role}): {content}")

        return "\n".join(lines)

    def stage1_detect_spans(
        self,
        conversation: List[Dict],
        max_tokens: int = 4000
    ) -> List[Dict]:
        """
        Stage 1: Detect discussion spans (cheap model)

        Args:
            conversation: Redacted conversation
            max_tokens: Max tokens for response

        Returns:
            List of span dicts
        """
        if not self.llm_client:
            # Fallback: Create dummy spans covering whole conversation
            return [{
                "category": "general",
                "start": "msg_001",
                "end": f"msg_{len(conversation):03d}",
                "start_index": 1,
                "end_index": len(conversation),
                "topic": "full session"
            }]

        # Format conversation
        formatted = self.format_conversation_for_llm(conversation)

        # Call cheap model for span detection
        # (Implementation depends on LLM client - anthropic vs openai)
        # For now, return placeholder
        # TODO: Implement actual LLM call

        # Placeholder: return whole conversation as one span
        return [{
            "category": "general",
            "start": "msg_001",
            "end": f"msg_{len(conversation):03d}",
            "start_index": 1,
            "end_index": len(conversation),
            "topic": "full session"
        }]

    def extract_span_messages(
        self,
        conversation: List[Dict],
        span: Dict
    ) -> List[Dict]:
        """
        Extract messages within a span

        Range semantics: [start_index, end_index) - inclusive-exclusive, 0-based
        Example: [41, 50) returns indices 41-49 (messages msg_042 to msg_050)

        Args:
            conversation: Full conversation
            span: Span dict with start_index (0-based, inclusive) and end_index (0-based, exclusive)

        Returns:
            Messages in span
        """
        # Indices are already 0-based, range is [start, end) inclusive-exclusive
        start_idx = span["start_index"]
        end_idx = span["end_index"]

        return conversation[start_idx:end_idx]

    def extract_temporal_bounds(
        self,
        conversation: List[Dict],
        message_range: Dict[str, Any]
    ) -> Tuple[Optional[str], Optional[str]]:
        """
        Extract ISO timestamps for first and last message in a range

        Range semantics: [start_index, end_index) - inclusive-exclusive, 0-based
        Returns timestamps for INCLUSIVE range (both start and end messages)

        Args:
            conversation: Full conversation
            message_range: Range with start_index (0-based, inclusive) and end_index (0-based, exclusive)

        Returns:
            (t_first, t_last) as ISO timestamp strings
        """
        # Indices are 0-based, range is [start, end) inclusive-exclusive
        start_idx = message_range.get("start_index", 0)
        end_idx = message_range.get("end_index", len(conversation))

        # Get timestamps
        t_first = None
        t_last = None

        # Get first message timestamp (inclusive start)
        if 0 <= start_idx < len(conversation):
            first_msg = conversation[start_idx]
            if "timestamp" in first_msg:
                # Convert Unix timestamp to ISO if needed
                ts = first_msg["timestamp"]
                if isinstance(ts, (int, float)):
                    from datetime import datetime
                    t_first = datetime.fromtimestamp(ts).isoformat()
                else:
                    t_first = ts

        # Get last message timestamp (inclusive end of range)
        # Since end_index is exclusive, the last included message is at end_idx - 1
        last_idx = end_idx - 1
        if 0 <= last_idx < len(conversation):
            last_msg = conversation[last_idx]
            if "timestamp" in last_msg:
                ts = last_msg["timestamp"]
                if isinstance(ts, (int, float)):
                    from datetime import datetime
                    t_last = datetime.fromtimestamp(ts).isoformat()
                else:
                    t_last = ts

        return t_first, t_last

    def enrich_with_temporal_data(
        self,
        summary: Dict[str, Any],
        conversation: List[Dict]
    ) -> None:
        """
        Enrich all summary items with temporal data (t_first, t_last)

        This implements the Two-Level Linked Retrieval by adding temporal
        bounds to each summary item, enabling O(1) lookup from summary
        layer to full conversation layer.

        Args:
            summary: Summary dict to enrich (modified in place)
            conversation: Full conversation for timestamp lookup
        """
        # Enrich decisions
        for decision in summary.get("decisions", []):
            if "message_range" in decision:
                t_first, t_last = self.extract_temporal_bounds(
                    conversation,
                    decision["message_range"]
                )
                if t_first:
                    decision["t_first"] = t_first
                if t_last:
                    decision["t_last"] = t_last

        # Enrich code changes
        for change in summary.get("code_changes", []):
            if "message_range" in change:
                t_first, t_last = self.extract_temporal_bounds(
                    conversation,
                    change["message_range"]
                )
                if t_first:
                    change["t_first"] = t_first
                if t_last:
                    change["t_last"] = t_last

        # Enrich problems solved
        for problem in summary.get("problems_solved", []):
            if "message_range" in problem:
                t_first, t_last = self.extract_temporal_bounds(
                    conversation,
                    problem["message_range"]
                )
                if t_first:
                    problem["t_first"] = t_first
                if t_last:
                    problem["t_last"] = t_last

        # Enrich open issues
        for issue in summary.get("open_issues", []):
            if "message_range" in issue:
                t_first, t_last = self.extract_temporal_bounds(
                    conversation,
                    issue["message_range"]
                )
                if t_first:
                    issue["t_first"] = t_first
                if t_last:
                    issue["t_last"] = t_last

        # Enrich next steps
        for step in summary.get("next_steps", []):
            if "message_range" in step:
                t_first, t_last = self.extract_temporal_bounds(
                    conversation,
                    step["message_range"]
                )
                if t_first:
                    step["t_first"] = t_first
                if t_last:
                    step["t_last"] = t_last

    def calculate_break_even_reduction(self) -> float:
        """
        Calculate the reduction ratio where two-stage breaks even with single-stage

        Returns:
            r_break_even: Maximum reduction ratio where two-stage is worth it
                         If Stage-1 keeps more than this fraction, use single-stage instead

        Formula: r < 1 - (cost_haiku_input / cost_sonnet_input)
        """
        haiku_price = self.PRICING.get(self.span_detection_model, {}).get("input", 0.25)
        sonnet_price = self.PRICING.get(self.model, {}).get("input", 3.0)

        # Break-even point: two-stage is worth it only if Stage-1 removes enough
        r_break_even = 1.0 - (haiku_price / sonnet_price)

        return r_break_even

    def estimate_cost(
        self,
        input_tokens: int,
        output_tokens: int,
        model: str
    ) -> float:
        """
        Estimate cost for an LLM call

        Args:
            input_tokens: Number of input tokens
            output_tokens: Number of output tokens
            model: Model name

        Returns:
            Estimated cost in dollars
        """
        pricing = self.PRICING.get(model, {"input": 3.0, "output": 15.0})

        input_cost = (input_tokens / 1_000_000) * pricing["input"]
        output_cost = (output_tokens / 1_000_000) * pricing["output"]

        return input_cost + output_cost

    def should_use_two_stage(
        self,
        total_tokens: int,
        estimated_span_tokens: Optional[int] = None
    ) -> Tuple[bool, str]:
        """
        Decide whether to use two-stage based on break-even analysis

        Args:
            total_tokens: Total tokens in conversation
            estimated_span_tokens: Estimated tokens after span detection (if known)

        Returns:
            (should_use, reason)
        """
        # If user forced two-stage, honor it
        if self.use_two_stage:
            return True, "User explicitly enabled two-stage"

        # If auto-switch not enabled, use single-stage
        if not self.auto_switch_two_stage:
            return False, "Auto-switch disabled, using single-stage (default)"

        # Calculate break-even point
        r_break_even = self.calculate_break_even_reduction()

        # If we have estimated span tokens, check if reduction is good enough
        if estimated_span_tokens is not None:
            reduction_ratio = estimated_span_tokens / total_tokens

            if reduction_ratio > r_break_even:
                return False, (
                    f"Stage-1 reduction ratio ({reduction_ratio:.2f}) exceeds break-even "
                    f"({r_break_even:.2f}). Single-stage is more cost-effective."
                )
            else:
                return True, (
                    f"Stage-1 reduction ratio ({reduction_ratio:.2f}) is below break-even "
                    f"({r_break_even:.2f}). Two-stage saves cost."
                )

        # Without span estimates, use heuristic: two-stage only for very large sessions
        # where we expect good reduction (>100K tokens)
        if total_tokens > 100_000:
            return True, f"Large session ({total_tokens:,} tokens), two-stage likely beneficial"
        else:
            return False, f"Session size ({total_tokens:,} tokens) too small for two-stage overhead"

    def stage2_generate_summary(
        self,
        conversation: List[Dict],
        spans: List[Dict],
        max_tokens: int = 8000
    ) -> Dict[str, Any]:
        """
        Stage 2: Generate reasoned summary (better model)

        Args:
            conversation: Redacted conversation
            spans: Detected spans from stage 1
            max_tokens: Max tokens for response

        Returns:
            Summary dict
        """
        if not self.llm_client:
            # Fallback: Create minimal summary
            return {
                "overview": "Session summarized without LLM",
                "decisions": [],
                "code_changes": [],
                "problems_solved": [],
                "open_issues": [],
                "next_steps": []
            }

        # For each span category, extract relevant messages and summarize
        # (Implementation depends on LLM client)
        # TODO: Implement actual LLM call

        # Placeholder: return empty summary
        return {
            "overview": "Placeholder summary",
            "decisions": [],
            "code_changes": [],
            "problems_solved": [],
            "open_issues": [],
            "next_steps": []
        }

    def summarize_session(
        self,
        conversation: List[Dict],
        session_hash: Optional[str] = None,
        redact_secrets: bool = True
    ) -> Dict[str, Any]:
        """
        Full summarization pipeline (single-stage by default, two-stage optional)

        Args:
            conversation: Raw conversation (will be redacted)
            session_hash: Hash of session for provenance
            redact_secrets: Whether to redact secrets before summarization

        Returns:
            Complete summary with metadata
        """
        # Step 1: Redact secrets
        if redact_secrets:
            conversation, redaction_stats = redact_for_summarization(conversation)
            if redaction_stats:
                print(f"🔒 Redacted {sum(redaction_stats.values())} secrets: {redaction_stats}")

        # Step 2: Detect ground truth code changes
        detector = CodeChangeDetector()
        ground_truth = detector.analyze_conversation(conversation)
        ground_truth_changes = detector.build_code_changes_from_ground_truth(conversation)

        # Step 3: Choose pipeline based on use_two_stage flag
        if self.use_two_stage:
            # Two-stage: Span detection → Reasoned summary
            spans = self.stage1_detect_spans(conversation)
            print(f"📍 Detected {len(spans)} discussion spans")
            llm_summary = self.stage2_generate_summary(conversation, spans)
        else:
            # Single-stage: Direct summarization (default, simpler, more reliable)
            print(f"📝 Single-stage summarization of {len(conversation)} messages")
            spans = [{
                "category": "full_session",
                "start": "msg_001",
                "end": f"msg_{len(conversation):03d}",
                "start_index": 1,
                "end_index": len(conversation),
                "topic": "complete session"
            }]
            llm_summary = self.stage2_generate_summary(conversation, spans)

        # Step 4: Augment LLM code changes with ground truth
        if llm_summary.get("code_changes"):
            llm_summary["code_changes"] = detector.augment_llm_code_changes(
                llm_summary["code_changes"],
                ground_truth
            )

        # Step 5: Merge ground truth changes with LLM summary
        # Prefer ground truth for code changes
        if ground_truth_changes and not llm_summary.get("code_changes"):
            llm_summary["code_changes"] = ground_truth_changes

        # Step 6: Create full summary with metadata
        summary = create_summary_skeleton(
            model=self.model,
            session_hash=session_hash
        )
        summary.update(llm_summary)

        # Step 6.5: Enrich with temporal data (t_first, t_last) for two-level retrieval
        self.enrich_with_temporal_data(summary, conversation)

        # Step 7: Verify references
        verifier = SummaryVerifier(conversation)
        is_valid, errors = verifier.verify_summary(summary)

        if not is_valid:
            print("⚠️  Summary validation errors:")
            for category, errs in errors.items():
                if errs:
                    print(f"  {category}:")
                    for err in errs:
                        print(f"    - {err}")

            # Auto-fix if possible
            summary, warnings = verifier.auto_fix_summary(summary)
            if warnings:
                print("🔧 Auto-fixed summary issues:")
                for warning in warnings:
                    print(f"  - {warning}")

        return summary
