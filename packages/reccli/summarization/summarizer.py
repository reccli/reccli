"""
Summarizer - Two-stage AI-powered session summarization
Stage 1: Span detection (cheap model)
Stage 2: Reasoned summary (better model)
"""

import json
import re
from copy import deepcopy
from datetime import datetime
from typing import Dict, List, Optional, Tuple, Any

from .summary_schema import (
    SUMMARY_TEXT_FIELDS,
    SPAN_KIND_BY_CATEGORY,
    create_span,
    create_summary_skeleton,
    create_decision_item,
    create_code_change_item,
    create_problem_solved_item,
    create_open_issue_item,
    create_next_step_item,
    add_causal_edge,
    ensure_summary_span_links,
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
    "start_index": 41,
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
        "start_index": 41,
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

    DELTA_SUMMARY_PATCH_PROMPT = """You update an existing session summary using only new messages since the compaction frontier.

Return JSON only with this shape:
{
  "operations": [
    {
      "op": "add_item",
      "category": "decisions" | "open_issues" | "next_steps",
      "item": { ... }
    },
    {
      "op": "update_item",
      "category": "decisions" | "open_issues" | "next_steps",
      "item_id": "existing_summary_item_id",
      "changes": { ... }
    },
    {
      "op": "merge_items",
      "category": "decisions" | "open_issues" | "next_steps",
      "source_ids": ["old_id_a", "old_id_b"],
      "target_item": { ... }
    },
    {
      "op": "close_span",
      "span_id": "open_span_id",
      "end_message_id": "msg_123",
      "message_ids": ["msg_101", "msg_103", "msg_123"]
    },
    {
      "op": "no_change",
      "reason": "why no update is needed"
    }
  ]
}

Rules:
- Do NOT regenerate the whole summary.
- Do NOT remove existing summary items implicitly.
- Only emit surgical operations based on the new messages.
- Prefer `update_item` over rewording an item from scratch.
- `update_item` MUST NOT stretch an existing item beyond its linked spans. If new evidence falls outside the linked spans, emit a new item instead of stretching the old one.
- `merge_items` should combine the source items' span_ids. Do not invent a merged range that ignores the source spans.
- Only operate on decisions, open_issues, and next_steps.
- Code changes are handled by deterministic tooling elsewhere.
- Use message IDs, not computed indices. The runtime will resolve indices from canonical message IDs.
- For any `message_range`, emit only `start` and `end` message IDs.
- For `close_span`, emit `end_message_id` and optional `message_ids`, but do not emit `end_index`.
- For `add_item` and `merge_items`, every item MUST include:
  - canonical category field only:
    - `decisions`: `decision` and `reasoning`
    - `open_issues`: `issue`
    - `next_steps`: `action`
  - `references`
  - `message_range.start`
  - `message_range.end`
- Do NOT use placeholder fields like `title`, `description`, or invented `span_ids`.
- If you cannot ground an item in specific message IDs, emit `no_change` instead of a malformed item.
- If nothing changed, emit exactly one `no_change` op.
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
        model: str = "claude-sonnet-4-6",
        use_two_stage: bool = False,
        span_detection_model: str = "claude-haiku-4-5",
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
        include_indices: bool = True,
        base_index: int = 0,
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
            msg_id = f"msg_{base_index + i + 1:03d}"
            role = msg.get("role", "unknown")
            content = msg.get("content", "")

            if include_indices:
                lines.append(f"{msg_id} (0-based index: {base_index + i}, {role}): {content}")
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
            return [{
                "category": "general",
                "start": "msg_001",
                "end": f"msg_{len(conversation):03d}",
                "start_index": 0,
                "end_index": len(conversation),
                "topic": "full session"
            }]

        formatted = self.format_conversation_for_llm(conversation)

        # Resolve span detection model
        span_model = self.span_detection_model
        if hasattr(self.llm_client, "messages"):
            anthropic_map = {
                "claude-haiku": "claude-haiku-4-5-20251001",
            }
            span_model = anthropic_map.get(span_model, span_model)
        elif hasattr(self.llm_client, "chat"):
            openai_map = {
                "gpt5-mini": "gpt-5-mini",
            }
            span_model = openai_map.get(span_model, span_model)

        try:
            if hasattr(self.llm_client, "messages"):
                response = self.llm_client.messages.create(
                    model=span_model,
                    max_tokens=max_tokens,
                    temperature=0,
                    system=self.SPAN_DETECTION_PROMPT,
                    messages=[{"role": "user", "content": formatted}],
                )
                raw_text = response.content[0].text
            elif hasattr(self.llm_client, "chat"):
                response = self.llm_client.chat.completions.create(
                    model=span_model,
                    max_tokens=max_tokens,
                    temperature=0,
                    messages=[
                        {"role": "system", "content": self.SPAN_DETECTION_PROMPT},
                        {"role": "user", "content": formatted},
                    ],
                )
                raw_text = response.choices[0].message.content
            else:
                raw_text = "[]"

            spans = self._extract_json_payload(raw_text)
            if isinstance(spans, list):
                return spans
            return spans.get("spans", [spans]) if isinstance(spans, dict) else []
        except Exception as e:
            print(f"⚠️  Span detection failed: {e}")
            return [{
                "category": "general",
                "start": "msg_001",
                "end": f"msg_{len(conversation):03d}",
                "start_index": 0,
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
            return {
                "overview": "Session summarized without LLM",
                "decisions": [],
                "code_changes": [],
                "problems_solved": [],
                "open_issues": [],
                "next_steps": []
            }

        formatted = self.format_conversation_for_llm(conversation)
        span_context = "\n".join(
            f"- [{s.get('category', 'general')}] messages {s.get('start', '?')}-{s.get('end', '?')}: {s.get('topic', '')}"
            for s in spans
        )

        user_message = (
            f"Here are the discussion spans detected:\n{span_context}\n\n"
            f"Here is the full conversation:\n{formatted}"
        )

        model = self._resolve_model_name_for_client()

        try:
            if hasattr(self.llm_client, "messages"):
                # Anthropic client
                response = self.llm_client.messages.create(
                    model=model,
                    max_tokens=max_tokens,
                    temperature=0,
                    system=self.REASONED_SUMMARY_PROMPT,
                    messages=[{"role": "user", "content": user_message}],
                )
                raw_text = response.content[0].text
            elif hasattr(self.llm_client, "chat"):
                # OpenAI client
                response = self.llm_client.chat.completions.create(
                    model=model,
                    max_tokens=max_tokens,
                    temperature=0,
                    messages=[
                        {"role": "system", "content": self.REASONED_SUMMARY_PROMPT},
                        {"role": "user", "content": user_message},
                    ],
                )
                raw_text = response.choices[0].message.content
            else:
                raw_text = "{}"

            return self._extract_json_payload(raw_text)
        except Exception as e:
            print(f"⚠️  LLM summarization failed: {e}")
            return {
                "overview": f"Summarization failed: {e}",
                "decisions": [],
                "code_changes": [],
                "problems_solved": [],
                "open_issues": [],
                "next_steps": []
            }

    def _normalize_range_indices(self, llm_summary: Dict[str, Any], conversation_len: int) -> None:
        """Fix common LLM mistakes: end_index should be exclusive (0-based).

        LLMs consistently return end_index as inclusive (the index OF the last message).
        The spec requires exclusive (one past the last message), matching Python slice semantics.
        """
        categories = ["decisions", "code_changes", "problems_solved", "open_issues", "next_steps"]
        for cat in categories:
            for item in llm_summary.get(cat, []):
                mr = item.get("message_range")
                if not mr or not isinstance(mr, dict):
                    continue
                start_idx = mr.get("start_index")
                end_idx = mr.get("end_index")
                end_msg = mr.get("end")
                if isinstance(end_idx, int) and isinstance(start_idx, int):
                    # If end_index == start_index for a single-message range, bump to exclusive
                    # If end_index looks inclusive (matches msg number - 1), bump by 1
                    if end_msg and isinstance(end_msg, str):
                        try:
                            msg_num = int(end_msg.split("_")[1])
                            expected_exclusive = msg_num  # msg_004 → exclusive end_index=4
                            if end_idx == expected_exclusive - 1:
                                mr["end_index"] = expected_exclusive
                        except (IndexError, ValueError):
                            pass
                    # Clamp to conversation length
                    if mr["end_index"] > conversation_len:
                        mr["end_index"] = conversation_len

    def _message_id_for_index(self, index: int) -> str:
        return f"msg_{index + 1:03d}"

    def _shift_message_id(self, message_id: str, offset: int) -> str:
        try:
            number = int(message_id.split("_")[1])
        except (IndexError, ValueError):
            return message_id
        return f"msg_{number + offset:03d}"

    def _shift_message_range(self, message_range: Dict[str, Any], offset: int) -> Dict[str, Any]:
        shifted = dict(message_range)
        if isinstance(shifted.get("start"), str):
            shifted["start"] = self._shift_message_id(shifted["start"], offset)
        if isinstance(shifted.get("end"), str):
            shifted["end"] = self._shift_message_id(shifted["end"], offset)
        if isinstance(shifted.get("start_index"), int):
            shifted["start_index"] = shifted["start_index"] + offset
        if isinstance(shifted.get("end_index"), int):
            shifted["end_index"] = shifted["end_index"] + offset
        return shifted

    def _shift_summary_item_links(self, item: Dict[str, Any], offset: int) -> Dict[str, Any]:
        shifted = deepcopy(item)
        shifted["references"] = [
            self._shift_message_id(ref, offset) if isinstance(ref, str) else ref
            for ref in shifted.get("references", [])
        ]
        if "message_range" in shifted:
            shifted["message_range"] = self._shift_message_range(shifted["message_range"], offset)
        shifted.pop("span_ids", None)
        return shifted

    def _canonicalize_item(self, category: str, item: Dict[str, Any]) -> Dict[str, Any]:
        canonical_text_fields = {
            "decisions": "decision",
            "code_changes": "description",
            "problems_solved": "problem",
            "open_issues": "issue",
            "next_steps": "action",
        }
        text_field = canonical_text_fields.get(category)
        normalized_item = self._normalize_category_aliases(category, item)

        message_range = normalized_item.get("message_range")
        if (
            normalized_item.get("id")
            and "span_ids" in normalized_item
            and text_field in normalized_item
            and self._normalize_semantic_text(normalized_item.get(text_field, ""))
            and isinstance(message_range, dict)
            and "start_index" in message_range
            and "end_index" in message_range
        ):
            return normalized_item

        common = {
            "references": normalized_item.get("references", []),
            "message_range": normalized_item.get("message_range", {}),
            "span_ids": normalized_item.get("span_ids", []),
            "confidence": normalized_item.get("confidence", "medium"),
            "t_first": normalized_item.get("t_first"),
            "t_last": normalized_item.get("t_last"),
        }

        if category == "decisions":
            canonical = create_decision_item(
                decision=normalized_item.get("decision", ""),
                reasoning=normalized_item.get("reasoning", ""),
                impact=normalized_item.get("impact", "medium"),
                alternatives_considered=normalized_item.get("alternatives_considered", []),
                quote=normalized_item.get("quote"),
                **common,
            )
        elif category == "code_changes":
            canonical = create_code_change_item(
                files=normalized_item.get("files", []),
                description=normalized_item.get("description", ""),
                change_type=normalized_item.get("type", "feature"),
                lines_added=normalized_item.get("lines_added"),
                lines_removed=normalized_item.get("lines_removed"),
                source_of_truth=normalized_item.get("source_of_truth", "llm_inferred"),
                **common,
            )
        elif category == "problems_solved":
            canonical = create_problem_solved_item(
                problem=normalized_item.get("problem", ""),
                solution=normalized_item.get("solution", ""),
                **common,
            )
        elif category == "open_issues":
            canonical = create_open_issue_item(
                issue=normalized_item.get("issue", ""),
                severity=normalized_item.get("severity", "medium"),
                **common,
            )
        else:
            canonical = create_next_step_item(
                action=normalized_item.get("action", ""),
                priority=normalized_item.get("priority", 3),
                estimated_time=normalized_item.get("estimated_time"),
                **common,
            )

        # Preserve any extra metadata the constructors do not know about.
        for key, value in normalized_item.items():
            canonical.setdefault(key, value)
        return canonical

    def _merge_summary_items(self, existing: List[Dict[str, Any]], incoming: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        merged: Dict[str, Dict[str, Any]] = {}
        ordered_ids: List[str] = []

        for item in existing + incoming:
            item_id = item.get("id")
            if not item_id:
                continue
            if item_id not in merged:
                ordered_ids.append(item_id)
                merged[item_id] = deepcopy(item)
                continue

            prior = merged[item_id]

            # Locked items are authoritative — skip incoming updates entirely.
            # Only allow range extension (t_last / message_range.end_index) so the
            # linked-message span stays correct as the conversation grows.
            if prior.get("locked"):
                preserved = deepcopy(prior)
                if "t_last" in item and item["t_last"]:
                    preserved["t_last"] = item["t_last"]
                if "message_range" in item and isinstance(item["message_range"], dict):
                    mr = preserved.get("message_range", {}) or {}
                    new_end = item["message_range"].get("end_index")
                    if new_end is not None and new_end > mr.get("end_index", 0):
                        mr["end_index"] = new_end
                        mr["end"] = item["message_range"].get("end", mr.get("end"))
                        preserved["message_range"] = mr
                merged[item_id] = preserved
                continue

            updated = deepcopy(prior)
            updated.update(item)

            # Preserve manual protections unless explicitly changed.
            if prior.get("pinned") and "pinned" not in item:
                updated["pinned"] = True
            if prior.get("locked") and "locked" not in item:
                updated["locked"] = True
            merged[item_id] = updated

        return [merged[item_id] for item_id in ordered_ids]

    def _apply_summary_patch(self, summary: Dict[str, Any], patch: Dict[str, Any]) -> Dict[str, Any]:
        updated = deepcopy(summary)
        if patch.get("overview"):
            existing_overview = updated.get("overview", "").strip()
            new_overview = patch["overview"].strip()
            if not existing_overview:
                updated["overview"] = new_overview
            elif new_overview and new_overview not in existing_overview:
                updated["overview"] = f"{existing_overview} {new_overview}".strip()

        for category in ["decisions", "code_changes", "problems_solved", "open_issues", "next_steps"]:
            updated[category] = self._merge_summary_items(
                updated.get(category, []),
                patch.get(category, []),
            )

        updated["created_at"] = datetime.now().isoformat()
        return updated

    def _build_summary_patch(
        self,
        conversation: List[Dict],
        base_index: int = 0,
        redact_secrets: bool = True,
    ) -> Dict[str, Any]:
        """
        Build a patch describing only the supplied conversation slice.
        The patch can be safely merged into an existing summary without dropping older items.
        """
        working = deepcopy(conversation)
        if redact_secrets:
            working, redaction_stats = redact_for_summarization(working)
            if redaction_stats:
                print(f"🔒 Redacted {sum(redaction_stats.values())} secrets: {redaction_stats}")

        detector = CodeChangeDetector()
        ground_truth = detector.analyze_conversation(working)
        ground_truth_changes = detector.build_code_changes_from_ground_truth(working)

        if self.use_two_stage:
            spans = self.stage1_detect_spans(working)
            print(f"📍 Detected {len(spans)} discussion spans")
            llm_summary = self.stage2_generate_summary(working, spans)
        else:
            print(f"📝 Single-stage summarization of {len(working)} messages")
            spans = [{
                "category": "full_session",
                "start": "msg_001",
                "end": f"msg_{len(working):03d}",
                "start_index": 0,
                "end_index": len(working),
                "topic": "complete session slice"
            }]
            llm_summary = self.stage2_generate_summary(working, spans)

        # Normalize LLM output: end_index must be exclusive (LLMs often return inclusive)
        self._normalize_range_indices(llm_summary, len(working))

        if llm_summary.get("code_changes"):
            llm_summary["code_changes"] = detector.augment_llm_code_changes(
                llm_summary["code_changes"],
                ground_truth
            )
        if ground_truth_changes and not llm_summary.get("code_changes"):
            llm_summary["code_changes"] = ground_truth_changes

        patch = {
            "overview": llm_summary.get("overview") or "",
            "decisions": [],
            "code_changes": [],
            "problems_solved": [],
            "open_issues": [],
            "next_steps": [],
        }
        for category in ["decisions", "code_changes", "problems_solved", "open_issues", "next_steps"]:
            patch[category] = [
                self._canonicalize_item(category, self._shift_summary_item_links(item, base_index))
                for item in llm_summary.get(category, [])
            ]
        return patch

    def _resolve_model_name_for_client(self) -> str:
        """Normalize shorthand model names to provider-specific runtime IDs."""
        anthropic_map = {
            "claude": "claude-sonnet-4-6",
            "claude-sonnet": "claude-sonnet-4-6",
            "claude-haiku": "claude-haiku-4-5",
            "claude-opus": "claude-opus-4-6",
        }
        openai_map = {
            "gpt-5.4": "gpt-5.4",
            "gpt-5.4-mini": "gpt-5.4-mini",
            "gpt-5.4-nano": "gpt-5.4-nano",
            "gpt5": "gpt-5.4",
            "gpt5-mini": "gpt-5.4-mini",
            "gpt5-nano": "gpt-5.4-nano",
            "gpt4o": "gpt-4o",
        }
        if hasattr(self.llm_client, "messages"):
            return anthropic_map.get(self.model, self.model)
        if hasattr(self.llm_client, "chat"):
            return openai_map.get(self.model, self.model)
        return self.model

    def _extract_json_payload(self, text: str) -> Dict[str, Any]:
        """Extract a JSON object from a model response."""
        text = (text or "").strip()
        fenced = re.search(r"```(?:json)?\s*(\{.*\})\s*```", text, re.DOTALL)
        if fenced:
            text = fenced.group(1)
        else:
            start = text.find("{")
            end = text.rfind("}")
            if start != -1 and end != -1 and end > start:
                text = text[start:end + 1]
        return json.loads(text)

    def _call_json_llm(self, system_prompt: str, user_prompt: str, max_tokens: int = 2000) -> Dict[str, Any]:
        """Call the configured LLM and parse a JSON object from the response."""
        if not self.llm_client:
            raise RuntimeError("No LLM client configured")

        model_id = self._resolve_model_name_for_client()
        if hasattr(self.llm_client, "messages"):
            # Use streaming for large requests to avoid Anthropic timeout
            with self.llm_client.messages.stream(
                model=model_id,
                max_tokens=max_tokens,
                system=system_prompt,
                messages=[{"role": "user", "content": user_prompt}],
            ) as stream:
                text = stream.get_final_text()
            return self._extract_json_payload(text)

        if hasattr(self.llm_client, "chat"):
            response = self.llm_client.chat.completions.create(
                model=model_id,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                temperature=0,
            )
            text = response.choices[0].message.content or ""
            return self._extract_json_payload(text)

        raise RuntimeError("Unsupported LLM client surface for summarization")

    def _snapshot_summary_ids(self, summary: Dict[str, Any]) -> Dict[str, set]:
        categories = ["decisions", "code_changes", "problems_solved", "open_issues", "next_steps"]
        return {
            category: {item.get("id") for item in summary.get(category, []) if item.get("id")}
            for category in categories
        }

    def _assert_no_silent_item_loss(
        self,
        before: Dict[str, Any],
        after: Dict[str, Any],
        operations: List[Dict[str, Any]],
    ) -> None:
        """Every pre-existing item must survive unless explicitly marked as merged/superseded."""
        before_ids = self._snapshot_summary_ids(before)
        after_ids = self._snapshot_summary_ids(after)

        explicitly_handled = {category: set() for category in before_ids}
        for op in operations:
            category = op.get("category")
            if category not in explicitly_handled:
                continue
            if op.get("op") == "merge_items":
                explicitly_handled[category].update(op.get("source_ids", []))

        silent_loss = {}
        for category, ids in before_ids.items():
            lost = ids - after_ids.get(category, set()) - explicitly_handled[category]
            if lost:
                silent_loss[category] = sorted(lost)

        if silent_loss:
            raise ValueError(f"Silent summary item loss detected: {silent_loss}")

    def _format_open_spans_for_delta(self, spans: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        return [
            {
                "id": span.get("id"),
                "kind": span.get("kind"),
                "topic": span.get("topic"),
                "start_message_id": span.get("start_message_id"),
                "start_index": span.get("start_index"),
                "latest_message_id": span.get("latest_message_id"),
                "latest_index": span.get("latest_index"),
                "message_ids": span.get("message_ids", []),
            }
            for span in spans
            if span.get("status") == "open"
        ]

    def _normalize_semantic_text(self, value: str) -> str:
        return " ".join((value or "").strip().lower().split())

    def _text_field_for_category(self, category: str) -> str:
        return SUMMARY_TEXT_FIELDS.get(category, "summary")

    def _message_id_to_index(self, conversation: List[Dict[str, Any]]) -> Dict[str, int]:
        return {
            message.get("_message_id"): index
            for index, message in enumerate(conversation)
            if message.get("_message_id")
        }

    def _resolve_message_range_from_ids(
        self,
        message_range: Optional[Dict[str, Any]],
        message_index: Dict[str, int],
    ) -> Optional[Dict[str, Any]]:
        if not isinstance(message_range, dict):
            return message_range

        start_id = message_range.get("start")
        end_id = message_range.get("end")
        if not isinstance(start_id, str) or not isinstance(end_id, str):
            return message_range
        if start_id not in message_index or end_id not in message_index:
            return message_range

        start_index = message_index[start_id]
        end_index = message_index[end_id] + 1
        resolved = dict(message_range)
        resolved["start_index"] = start_index
        resolved["end_index"] = end_index
        return resolved

    def _expand_message_range_to_references(
        self,
        item: Dict[str, Any],
        conversation: List[Dict[str, Any]],
        message_index: Dict[str, int],
    ) -> Dict[str, Any]:
        expanded = deepcopy(item)
        references = [ref for ref in expanded.get("references", []) if ref in message_index]
        if not references:
            return expanded

        range_info = expanded.get("message_range")
        indices = [message_index[ref] for ref in references]
        if isinstance(range_info, dict):
            start_id = range_info.get("start")
            end_id = range_info.get("end")
            if start_id in message_index:
                indices.append(message_index[start_id])
            if end_id in message_index:
                indices.append(message_index[end_id])

        start_index = min(indices)
        end_index = max(indices) + 1
        expanded["message_range"] = {
            "start": conversation[start_index].get("_message_id"),
            "end": conversation[end_index - 1].get("_message_id"),
            "start_index": start_index,
            "end_index": end_index,
        }
        return expanded

    def _normalize_category_aliases(self, category: str, item: Dict[str, Any]) -> Dict[str, Any]:
        normalized = deepcopy(item)

        if category == "decisions":
            if not normalized.get("decision") and normalized.get("title"):
                normalized["decision"] = normalized["title"]
            if not normalized.get("reasoning") and normalized.get("description"):
                normalized["reasoning"] = normalized["description"]
        elif category == "open_issues":
            if not normalized.get("issue"):
                normalized["issue"] = normalized.get("title") or normalized.get("description", "")
        elif category == "next_steps":
            if not normalized.get("action"):
                normalized["action"] = (
                    normalized.get("step")
                    or normalized.get("title")
                    or normalized.get("description", "")
                )
            if isinstance(normalized.get("priority"), str):
                priority_map = {"high": 1, "medium": 3, "low": 5}
                normalized["priority"] = priority_map.get(
                    normalized["priority"].strip().lower(),
                    3,
                )

        return normalized

    def _canonical_field_missing(self, category: str, item: Dict[str, Any]) -> bool:
        text_field = self._text_field_for_category(category)
        return not self._normalize_semantic_text(item.get(text_field, ""))

    def _span_bounds(self, span: Dict[str, Any]) -> Tuple[int, int]:
        start_index = span.get("start_index", 0)
        end_index = span.get("end_index")
        if not isinstance(end_index, int):
            latest_index = span.get("latest_index")
            if isinstance(latest_index, int):
                end_index = latest_index + 1
        if not isinstance(end_index, int):
            end_index = start_index
        return start_index, end_index

    def _span_contains_index(self, span: Dict[str, Any], index: int) -> bool:
        start_index, end_index = self._span_bounds(span)
        return start_index <= index < end_index

    def _item_required_indices(
        self,
        item: Dict[str, Any],
        message_index: Dict[str, int],
    ) -> List[int]:
        indices = set()
        message_range = item.get("message_range") or {}
        start_id = message_range.get("start")
        end_id = message_range.get("end")
        if isinstance(start_id, str) and start_id in message_index:
            indices.add(message_index[start_id])
        if isinstance(end_id, str) and end_id in message_index:
            indices.add(message_index[end_id])
        for ref in item.get("references", []):
            if ref in message_index:
                indices.add(message_index[ref])
        return sorted(indices)

    def _infer_support_span(
        self,
        category: str,
        item: Dict[str, Any],
        projected_spans: List[Dict[str, Any]],
        message_index: Dict[str, int],
        conversation: List[Dict[str, Any]],
    ) -> Optional[Dict[str, Any]]:
        required_indices = self._item_required_indices(item, message_index)
        if not required_indices:
            return None

        existing_span_ids = set(item.get("span_ids", []))
        covered_indices = set()
        for span in projected_spans:
            if span.get("id") not in existing_span_ids:
                continue
            for idx in required_indices:
                if self._span_contains_index(span, idx):
                    covered_indices.add(idx)

        uncovered = [idx for idx in required_indices if idx not in covered_indices]
        if not uncovered:
            return None

        start_index = min(uncovered)
        end_index = max(uncovered) + 1
        start_message_id = conversation[start_index].get("_message_id")
        end_message_id = conversation[end_index - 1].get("_message_id")
        if not start_message_id or not end_message_id:
            return None

        inferred = create_span(
            kind=SPAN_KIND_BY_CATEGORY.get(category, "supporting_discussion"),
            start_message_id=start_message_id,
            start_index=start_index,
            end_message_id=end_message_id,
            end_index=end_index,
            topic=item.get(self._text_field_for_category(category), "") or item.get("id", category),
            references=item.get("references", []),
            t_first=conversation[start_index].get("timestamp"),
            t_last=conversation[end_index - 1].get("timestamp"),
            message_ids=[
                conversation[idx].get("_message_id")
                for idx in range(start_index, end_index)
                if conversation[idx].get("_message_id")
            ],
        )
        if inferred.get("id") not in {span.get("id") for span in projected_spans}:
            projected_spans.append(inferred)
        return inferred

    def _reconcile_item_spans(
        self,
        category: str,
        item: Dict[str, Any],
        projected_spans: List[Dict[str, Any]],
        message_index: Dict[str, int],
        conversation: List[Dict[str, Any]],
    ) -> Dict[str, Any]:
        reconciled = deepcopy(item)
        reconciled.setdefault("span_ids", [])
        inferred = self._infer_support_span(
            category=category,
            item=reconciled,
            projected_spans=projected_spans,
            message_index=message_index,
            conversation=conversation,
        )
        if inferred and inferred.get("id") not in reconciled["span_ids"]:
            reconciled["span_ids"] = list(reconciled["span_ids"]) + [inferred["id"]]
        return reconciled

    def _is_semantic_duplicate(
        self,
        category: str,
        item: Dict[str, Any],
        existing_items: List[Dict[str, Any]],
    ) -> bool:
        text_field = self._text_field_for_category(category)
        candidate = self._normalize_semantic_text(item.get(text_field, ""))
        if not candidate:
            return False

        candidate_refs = set(item.get("references", []))
        for existing in existing_items:
            if existing.get("status") == "merged":
                continue
            existing_text = self._normalize_semantic_text(existing.get(text_field, ""))
            if candidate != existing_text:
                continue
            existing_refs = set(existing.get("references", []))
            if not candidate_refs or not existing_refs or candidate_refs & existing_refs:
                return True
        return False

    def _item_range_bounds(self, item: Dict[str, Any]) -> Optional[Tuple[int, int]]:
        message_range = item.get("message_range") or {}
        start_index = message_range.get("start_index")
        end_index = message_range.get("end_index")
        if not isinstance(start_index, int) or not isinstance(end_index, int):
            return None
        return start_index, end_index

    def _range_gap(self, left: Dict[str, Any], right: Dict[str, Any]) -> int:
        left_bounds = self._item_range_bounds(left)
        right_bounds = self._item_range_bounds(right)
        if not left_bounds or not right_bounds:
            return 10**9
        left_start, left_end = left_bounds
        right_start, right_end = right_bounds
        if left_end < right_start:
            return right_start - left_end
        if right_end < left_start:
            return left_start - right_end
        return 0

    def _semantic_tokens(self, category: str, item: Dict[str, Any]) -> set:
        stopwords = {
            "the", "and", "for", "with", "that", "this", "from", "into", "also",
            "need", "needs", "using", "use", "should", "still", "item", "flow",
            "track", "tracked", "work", "queue", "next", "step", "issue", "decision",
            "handling", "implement", "implementation", "add", "update", "merge",
            "close", "change", "keep", "instead", "than", "while", "during",
        }
        text_parts = [item.get(self._text_field_for_category(category), "")]
        if category == "decisions":
            text_parts.append(item.get("reasoning", ""))
        normalized = self._normalize_semantic_text(" ".join(text_parts))
        return {
            token for token in re.findall(r"[a-z0-9_]+", normalized)
            if len(token) > 2 and token not in stopwords
        }

    def _should_fold_new_item_into_existing(
        self,
        category: str,
        existing_item: Dict[str, Any],
        new_item: Dict[str, Any],
    ) -> bool:
        existing_tokens = self._semantic_tokens(category, existing_item)
        new_tokens = self._semantic_tokens(category, new_item)
        shared_tokens = existing_tokens & new_tokens
        if not shared_tokens:
            return False

        range_gap = self._range_gap(existing_item, new_item)
        if len(shared_tokens) >= 2:
            return True
        return range_gap <= 6

    def _merge_references(
        self,
        left: Dict[str, Any],
        right: Dict[str, Any],
        message_index: Dict[str, int],
    ) -> List[str]:
        refs = list(dict.fromkeys(list(left.get("references", [])) + list(right.get("references", []))))
        return sorted(refs, key=lambda ref: message_index.get(ref, 10**9))

    def _expand_item_range(
        self,
        left: Dict[str, Any],
        right: Dict[str, Any],
        conversation: List[Dict[str, Any]],
        message_index: Dict[str, int],
    ) -> Dict[str, Any]:
        candidate = deepcopy(left)
        indices = []
        for item in [left, right]:
            bounds = self._item_range_bounds(item)
            if bounds:
                start_index, end_index = bounds
                indices.extend([start_index, end_index - 1])
            for ref in item.get("references", []):
                if ref in message_index:
                    indices.append(message_index[ref])
        if not indices:
            return candidate

        start_index = min(indices)
        end_index = max(indices) + 1
        candidate["message_range"] = {
            "start": conversation[start_index].get("_message_id"),
            "end": conversation[end_index - 1].get("_message_id"),
            "start_index": start_index,
            "end_index": end_index,
        }
        return candidate

    def _fold_new_item_into_existing(
        self,
        category: str,
        existing_item: Dict[str, Any],
        new_item: Dict[str, Any],
        conversation: List[Dict[str, Any]],
        message_index: Dict[str, int],
    ) -> Dict[str, Any]:
        folded = deepcopy(existing_item)
        text_field = self._text_field_for_category(category)
        if new_item.get(text_field):
            folded[text_field] = new_item[text_field]
        if category == "decisions" and new_item.get("reasoning"):
            folded["reasoning"] = new_item["reasoning"]
        if category == "open_issues" and new_item.get("severity"):
            folded["severity"] = new_item["severity"]

        folded["references"] = self._merge_references(existing_item, new_item, message_index)
        folded["span_ids"] = list(dict.fromkeys(list(existing_item.get("span_ids", [])) + list(new_item.get("span_ids", []))))
        folded = self._expand_item_range(folded, new_item, conversation, message_index)
        if new_item.get("t_first") and (not folded.get("t_first") or new_item["t_first"] < folded["t_first"]):
            folded["t_first"] = new_item["t_first"]
        if new_item.get("t_last"):
            folded["t_last"] = new_item["t_last"]
        return self._canonicalize_item(category, folded)

    def _post_op_deduplicate_summary(
        self,
        before_summary: Dict[str, Any],
        summary: Dict[str, Any],
        conversation: List[Dict[str, Any]],
    ) -> Dict[str, Any]:
        updated = deepcopy(summary)
        message_index = self._message_id_to_index(conversation)

        for category in ["decisions", "open_issues"]:
            prior_ids = {
                item.get("id")
                for item in before_summary.get(category, [])
                if item.get("id")
            }
            items = list(updated.get(category, []))
            existing_items = [item for item in items if item.get("id") in prior_ids]
            existing_items = [item for item in existing_items if item.get("status") != "merged"]
            new_items = [item for item in items if item.get("id") not in prior_ids]
            dropped_ids = set()

            for new_item in new_items:
                match = next(
                    (
                        existing_item for existing_item in existing_items
                        if existing_item.get("id") not in dropped_ids
                        and self._should_fold_new_item_into_existing(category, existing_item, new_item)
                    ),
                    None,
                )
                if not match:
                    continue

                folded = self._fold_new_item_into_existing(
                    category=category,
                    existing_item=match,
                    new_item=new_item,
                    conversation=conversation,
                    message_index=message_index,
                )
                for idx, item in enumerate(existing_items):
                    if item.get("id") == match.get("id"):
                        existing_items[idx] = folded
                        break
                dropped_ids.add(new_item.get("id"))

            updated[category] = existing_items + [
                item for item in new_items
                if item.get("id") not in dropped_ids
            ]

        return updated

    def _validate_delta_operations(
        self,
        operations: List[Dict[str, Any]],
        current_summary: Dict[str, Any],
        current_spans: List[Dict[str, Any]],
        conversation: List[Dict[str, Any]],
    ) -> Tuple[List[Dict[str, Any]], List[str]]:
        """Reject malformed or obviously unsafe LLM patch ops before mutating state."""
        allowed_categories = {"decisions", "open_issues", "next_steps"}
        allowed_ops = {"add_item", "update_item", "merge_items", "close_span", "no_change"}
        warnings: List[str] = []
        validated_ops: List[Dict[str, Any]] = []

        message_index = self._message_id_to_index(conversation)
        items_by_category = {
            category: {
                item.get("id"): item
                for item in current_summary.get(category, [])
                if item.get("id")
            }
            for category in allowed_categories
        }
        open_spans = {
            span.get("id"): span
            for span in current_spans
            if span.get("id") and span.get("status") == "open"
        }
        projected_spans = [deepcopy(span) for span in current_spans]
        projected_items = {
            category: [deepcopy(item) for item in current_summary.get(category, [])]
            for category in allowed_categories
        }

        for index, raw_op in enumerate(operations):
            op = deepcopy(raw_op)
            op_type = op.get("op")

            if op_type not in allowed_ops:
                warnings.append(f"op[{index}] rejected: unsupported op '{op_type}'")
                continue

            if op_type == "no_change":
                if len(operations) > 1:
                    warnings.append(f"op[{index}] ignored: no_change cannot be combined with other ops")
                    continue
                return ([{"op": "no_change", "reason": op.get("reason", "validated_no_change")}], warnings)

            if op_type == "close_span":
                span_id = op.get("span_id")
                span = open_spans.get(span_id)
                if not span:
                    warnings.append(f"op[{index}] rejected: close_span target '{span_id}' is not open")
                    continue

                end_message_id = op.get("end_message_id")
                if not isinstance(end_message_id, str) or end_message_id not in message_index:
                    warnings.append(f"op[{index}] rejected: close_span missing valid end_message_id")
                    continue
                end_index = message_index[end_message_id] + 1
                if end_index <= span.get("start_index", -1) or end_index > len(conversation):
                    warnings.append(f"op[{index}] rejected: close_span end_index {end_index} out of bounds")
                    continue

                expected_end_message_id = conversation[end_index - 1].get("_message_id")

                member_ids = list(op.get("message_ids") or [])
                if member_ids:
                    if any(member_id not in message_index for member_id in member_ids):
                        warnings.append(f"op[{index}] rejected: close_span references unknown message_ids")
                        continue
                    if any(
                        message_index[member_id] < span.get("start_index", 0)
                        or message_index[member_id] >= end_index
                        for member_id in member_ids
                    ):
                        warnings.append(f"op[{index}] rejected: close_span message_ids fall outside span bounds")
                        continue
                else:
                    member_ids = [
                        conversation[msg_index].get("_message_id")
                        for msg_index in range(span.get("start_index", 0), end_index)
                        if conversation[msg_index].get("_message_id")
                    ]

                op["end_message_id"] = expected_end_message_id
                op["end_index"] = end_index
                op["message_ids"] = member_ids
                validated_ops.append(op)
                for span_position, projected_span in enumerate(projected_spans):
                    if projected_span.get("id") != span_id:
                        continue
                    projected_spans[span_position] = {
                        **projected_span,
                        "status": "closed",
                        "end_message_id": expected_end_message_id,
                        "end_index": end_index,
                        "message_ids": member_ids,
                    }
                    break
                continue

            category = op.get("category")
            if category not in allowed_categories:
                warnings.append(f"op[{index}] rejected: unsupported category '{category}'")
                continue

            items = projected_items[category]

            if op_type == "update_item":
                item_id = op.get("item_id")
                if item_id not in items_by_category[category]:
                    warnings.append(f"op[{index}] rejected: update_item target '{item_id}' not found")
                    continue
                changes = op.get("changes")
                if not isinstance(changes, dict) or not changes:
                    warnings.append(f"op[{index}] rejected: update_item missing changes")
                    continue
                if "id" in changes:
                    changes = {key: value for key, value in changes.items() if key != "id"}
                    op["changes"] = changes
                if not changes:
                    warnings.append(f"op[{index}] rejected: update_item changes became empty after sanitization")
                    continue
                proposed_item = deepcopy(items_by_category[category][item_id])
                proposed_item.update(changes)
                if "message_range" in proposed_item:
                    proposed_item["message_range"] = self._resolve_message_range_from_ids(
                        proposed_item.get("message_range"),
                        message_index,
                    )
                proposed_item = self._expand_message_range_to_references(
                    proposed_item,
                    conversation,
                    message_index,
                )
                proposed_item = self._normalize_category_aliases(category, proposed_item)
                proposed_item["span_ids"] = list(
                    proposed_item.get("span_ids") or items_by_category[category][item_id].get("span_ids", [])
                )
                proposed_item = self._canonicalize_item(category, proposed_item)
                if self._canonical_field_missing(category, proposed_item):
                    warnings.append(
                        f"op[{index}] rejected: update_item missing canonical {self._text_field_for_category(category)} field"
                    )
                    continue
                proposed_item = self._reconcile_item_spans(
                    category=category,
                    item=proposed_item,
                    projected_spans=projected_spans,
                    message_index=message_index,
                    conversation=conversation,
                )
                verifier = SummaryVerifier(conversation, projected_spans)
                valid, errors = verifier.verify_decision(proposed_item)
                if not valid:
                    warnings.append(
                        f"op[{index}] rejected: update_item would create invalid {category[:-1]}: {', '.join(errors)}"
                    )
                    continue
                op["changes"] = proposed_item
                validated_ops.append(op)
                target = proposed_item
                items_by_category[category][item_id] = target
                for item_position, item in enumerate(items):
                    if item.get("id") == item_id:
                        items[item_position] = target
                        break
                continue

            if op_type == "add_item":
                item = deepcopy(op.get("item") or {})
                if not item:
                    warnings.append(f"op[{index}] rejected: add_item missing item payload")
                    continue
                if "message_range" in item:
                    item["message_range"] = self._resolve_message_range_from_ids(
                        item.get("message_range"),
                        message_index,
                    )
                item = self._normalize_category_aliases(category, item)
                canonical = self._canonicalize_item(category, item)
                if self._canonical_field_missing(category, canonical):
                    warnings.append(
                        f"op[{index}] rejected: add_item missing canonical {self._text_field_for_category(category)} field"
                    )
                    continue
                canonical = self._reconcile_item_spans(
                    category=category,
                    item=canonical,
                    projected_spans=projected_spans,
                    message_index=message_index,
                    conversation=conversation,
                )
                if self._is_semantic_duplicate(category, canonical, items):
                    warnings.append(f"op[{index}] rejected: add_item duplicates existing {category[:-1]} content")
                    continue
                verifier = SummaryVerifier(conversation, projected_spans)
                valid, errors = verifier.verify_decision(canonical)
                if not valid:
                    warnings.append(
                        f"op[{index}] rejected: add_item produced invalid {category[:-1]}: {', '.join(errors)}"
                    )
                    continue
                op["item"] = canonical
                validated_ops.append(op)
                items.append(canonical)
                items_by_category[category][canonical["id"]] = canonical
                continue

            if op_type == "merge_items":
                source_ids = list(op.get("source_ids") or [])
                if not source_ids:
                    warnings.append(f"op[{index}] rejected: merge_items missing source_ids")
                    continue
                if any(source_id not in items_by_category[category] for source_id in source_ids):
                    warnings.append(f"op[{index}] rejected: merge_items references unknown source_ids")
                    continue
                target_item = deepcopy(op.get("target_item") or {})
                if not target_item:
                    warnings.append(f"op[{index}] rejected: merge_items missing target_item")
                    continue
                if "message_range" in target_item:
                    target_item["message_range"] = self._resolve_message_range_from_ids(
                        target_item.get("message_range"),
                        message_index,
                    )
                target_item = self._normalize_category_aliases(category, target_item)
                source_span_ids = []
                for source_id in source_ids:
                    source_item = items_by_category[category].get(source_id, {})
                    source_span_ids.extend(source_item.get("span_ids", []))
                target_item["span_ids"] = list(dict.fromkeys(list(target_item.get("span_ids", [])) + source_span_ids))
                canonical_target = self._canonicalize_item(category, target_item)
                if self._canonical_field_missing(category, canonical_target):
                    warnings.append(
                        f"op[{index}] rejected: merge_items missing canonical {self._text_field_for_category(category)} field"
                    )
                    continue
                canonical_target = self._reconcile_item_spans(
                    category=category,
                    item=canonical_target,
                    projected_spans=projected_spans,
                    message_index=message_index,
                    conversation=conversation,
                )
                comparison_items = [
                    item for item in items
                    if item.get("id") not in set(source_ids)
                ]
                if self._is_semantic_duplicate(category, canonical_target, comparison_items):
                    warnings.append(f"op[{index}] rejected: merge_items target duplicates an existing item")
                    continue
                verifier = SummaryVerifier(conversation, projected_spans)
                valid, errors = verifier.verify_decision(canonical_target)
                if not valid:
                    warnings.append(
                        f"op[{index}] rejected: merge_items target invalid for {category}: {', '.join(errors)}"
                    )
                    continue
                op["target_item"] = canonical_target
                validated_ops.append(op)
                items.append(canonical_target)
                items_by_category[category][canonical_target["id"]] = canonical_target

        if not validated_ops:
            return ([{"op": "no_change", "reason": "validation_rejected"}], warnings)
        return validated_ops, warnings

    def generate_delta_operations(
        self,
        current_summary: Dict[str, Any],
        conversation_slice: List[Dict[str, Any]],
        open_spans: Optional[List[Dict[str, Any]]] = None,
        base_index: int = 0,
    ) -> List[Dict[str, Any]]:
        """Ask the model for constrained patch operations instead of a full summary rewrite."""
        if not self.llm_client or not conversation_slice:
            return [{"op": "no_change", "reason": "No LLM client or empty conversation slice"}]

        summary_view = {
            "overview": current_summary.get("overview", ""),
            "decisions": current_summary.get("decisions", []),
            "open_issues": current_summary.get("open_issues", []),
            "next_steps": current_summary.get("next_steps", []),
        }
        user_prompt = "\n\n".join([
            "Current summary state:",
            json.dumps(summary_view, indent=2, ensure_ascii=False),
            "New messages since the frontier:",
            self.format_conversation_for_llm(conversation_slice, base_index=base_index),
            "Open spans that may need closing:",
            json.dumps(self._format_open_spans_for_delta(open_spans or []), indent=2, ensure_ascii=False),
        ])

        try:
            payload = self._call_json_llm(
                self.DELTA_SUMMARY_PATCH_PROMPT,
                user_prompt,
                max_tokens=3000,
            )
        except Exception as exc:
            print(f"⚠️  Delta op generation failed: {exc}")
            return [{"op": "no_change", "reason": f"llm_error: {exc}"}]

        operations = payload.get("operations", [])
        if not operations:
            return [{"op": "no_change", "reason": "empty_operation_list"}]
        return operations

    def _update_existing_item(self, category: str, item: Dict[str, Any], changes: Dict[str, Any]) -> Dict[str, Any]:
        updated = deepcopy(item)
        updated.update(changes)
        return self._canonicalize_item(category, updated)

    def _apply_delta_operations(
        self,
        summary: Dict[str, Any],
        spans: List[Dict[str, Any]],
        operations: List[Dict[str, Any]],
        conversation: List[Dict[str, Any]],
    ) -> Tuple[Dict[str, Any], List[Dict[str, Any]]]:
        updated_summary = deepcopy(summary)
        updated_spans = deepcopy(spans)
        categories = ["decisions", "open_issues", "next_steps"]

        for op in operations:
            op_type = op.get("op")
            if op_type == "no_change":
                continue

            if op_type == "close_span":
                span_id = op.get("span_id")
                for span in updated_spans:
                    if span.get("id") != span_id:
                        continue
                    span["status"] = "closed"
                    span["end_message_id"] = op.get("end_message_id")
                    span["end_index"] = op.get("end_index")
                    if "message_ids" in op:
                        span["message_ids"] = list(op.get("message_ids") or [])
                    end_index = span.get("end_index")
                    if isinstance(end_index, int) and 0 < end_index <= len(conversation):
                        span["t_last"] = conversation[end_index - 1].get("timestamp")
                    break
                continue

            category = op.get("category")
            if category not in categories:
                continue

            items = list(updated_summary.get(category, []))

            if op_type == "add_item":
                item = self._canonicalize_item(category, deepcopy(op.get("item", {})))
                items = self._merge_summary_items(items, [item])
            elif op_type == "update_item":
                item_id = op.get("item_id")
                changes = op.get("changes", {})
                next_items = []
                for item in items:
                    if item.get("id") == item_id:
                        next_items.append(self._update_existing_item(category, item, changes))
                    else:
                        next_items.append(item)
                items = next_items
            elif op_type == "merge_items":
                source_ids = set(op.get("source_ids", []))
                target_item = self._canonicalize_item(category, deepcopy(op.get("target_item", {})))
                next_items = []
                for item in items:
                    if item.get("id") in source_ids:
                        merged = deepcopy(item)
                        merged["merged_into"] = target_item.get("id")
                        merged["status"] = "merged"
                        next_items.append(merged)
                    else:
                        next_items.append(item)
                items = self._merge_summary_items(next_items, [target_item])

            updated_summary[category] = items

        return updated_summary, updated_spans

    def update_summary_state_incrementally(
        self,
        conversation: List[Dict],
        existing_summary: Optional[Dict[str, Any]],
        existing_spans: Optional[List[Dict[str, Any]]],
        start_index: int,
        end_index: Optional[int] = None,
        session_hash: Optional[str] = None,
        redact_secrets: bool = True,
    ) -> Dict[str, Any]:
        """Update summary/spans together using constrained delta ops plus deterministic patches."""
        end_index = len(conversation) if end_index is None else min(end_index, len(conversation))
        start_index = max(0, min(start_index, end_index))
        current_spans = deepcopy(existing_spans or [])

        if existing_summary is None:
            summary = self.summarize_session(
                conversation[:end_index],
                session_hash=session_hash,
                redact_secrets=redact_secrets,
            )
            spans = ensure_summary_span_links(summary, current_spans)
            return {"summary": summary, "spans": spans, "operations": []}

        if start_index >= end_index:
            return {"summary": deepcopy(existing_summary), "spans": current_spans, "operations": []}

        if not self.llm_client:
            updated_summary = self.update_summary_incrementally(
                conversation=conversation,
                existing_summary=existing_summary,
                start_index=start_index,
                end_index=end_index,
                session_hash=session_hash,
                redact_secrets=redact_secrets,
            )
            spans = ensure_summary_span_links(updated_summary, current_spans)
            return {"summary": updated_summary, "spans": spans, "operations": []}

        operations = self.generate_delta_operations(
            current_summary=existing_summary,
            conversation_slice=conversation[start_index:end_index],
            open_spans=[span for span in current_spans if span.get("status") == "open"],
            base_index=start_index,
        )
        operations, op_warnings = self._validate_delta_operations(
            operations=operations,
            current_summary=existing_summary,
            current_spans=current_spans,
            conversation=conversation,
        )
        if op_warnings:
            print("⚠️  Rejected invalid delta ops:")
            for warning in op_warnings:
                print(f"  - {warning}")

        updated_summary, updated_spans = self._apply_delta_operations(
            summary=existing_summary,
            spans=current_spans,
            operations=operations,
            conversation=conversation,
        )

        deterministic_patch = self._build_summary_patch(
            conversation[start_index:end_index],
            base_index=start_index,
            redact_secrets=redact_secrets,
        )
        updated_summary["code_changes"] = self._merge_summary_items(
            updated_summary.get("code_changes", []),
            deterministic_patch.get("code_changes", []),
        )
        if deterministic_patch.get("overview"):
            updated_summary = self._apply_summary_patch(updated_summary, {"overview": deterministic_patch["overview"]})

        updated_summary = self._post_op_deduplicate_summary(
            before_summary=existing_summary,
            summary=updated_summary,
            conversation=conversation,
        )

        self.enrich_with_temporal_data(updated_summary, conversation)
        updated_spans = ensure_summary_span_links(updated_summary, updated_spans)

        verifier = SummaryVerifier(conversation, updated_spans)
        is_valid, errors = verifier.verify_summary(updated_summary)
        if not is_valid:
            print("⚠️  Incremental summary-state validation errors:")
            for category, errs in errors.items():
                if errs:
                    print(f"  {category}:")
                    for err in errs:
                        print(f"    - {err}")
            updated_summary, warnings = verifier.auto_fix_summary(updated_summary)
            updated_spans = verifier.spans
            if warnings:
                print("🔧 Auto-fixed incremental summary-state issues:")
                for warning in warnings:
                    print(f"  - {warning}")

        self._assert_no_silent_item_loss(existing_summary, updated_summary, operations)
        return {"summary": updated_summary, "spans": updated_spans, "operations": operations}

    def update_summary_incrementally(
        self,
        conversation: List[Dict],
        existing_summary: Optional[Dict[str, Any]],
        start_index: int,
        end_index: Optional[int] = None,
        session_hash: Optional[str] = None,
        redact_secrets: bool = True,
    ) -> Dict[str, Any]:
        """Update an existing summary using only messages beyond the summary frontier."""
        end_index = len(conversation) if end_index is None else min(end_index, len(conversation))
        start_index = max(0, min(start_index, end_index))

        if existing_summary is None:
            return self.summarize_session(
                conversation[:end_index],
                session_hash=session_hash,
                redact_secrets=redact_secrets,
            )

        if start_index >= end_index:
            return deepcopy(existing_summary)

        patch = self._build_summary_patch(
            conversation[start_index:end_index],
            base_index=start_index,
            redact_secrets=redact_secrets,
        )
        updated_summary = self._apply_summary_patch(existing_summary, patch)
        self.enrich_with_temporal_data(updated_summary, conversation)
        spans = ensure_summary_span_links(updated_summary)
        verifier = SummaryVerifier(conversation, spans)
        is_valid, errors = verifier.verify_summary(updated_summary)
        if not is_valid:
            print("⚠️  Incremental summary validation errors:")
            for category, errs in errors.items():
                if errs:
                    print(f"  {category}:")
                    for err in errs:
                        print(f"    - {err}")
            updated_summary, warnings = verifier.auto_fix_summary(updated_summary)
            if warnings:
                print("🔧 Auto-fixed incremental summary issues:")
                for warning in warnings:
                    print(f"  - {warning}")
        return updated_summary

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
        summary = create_summary_skeleton(
            model=self.model,
            session_hash=session_hash
        )
        patch = self._build_summary_patch(
            conversation,
            base_index=0,
            redact_secrets=redact_secrets,
        )
        summary = self._apply_summary_patch(summary, patch)

        # Step 6.5: Enrich with temporal data (t_first, t_last) for two-level retrieval
        self.enrich_with_temporal_data(summary, conversation)

        # Step 6.6: Ensure summary items are linked to first-class semantic spans
        spans = ensure_summary_span_links(summary)

        # Step 7: Verify references
        verifier = SummaryVerifier(conversation, spans)
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
