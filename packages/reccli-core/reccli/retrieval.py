"""
Retrieval - Two-level retrieval from .devsession files
Level 1: Fast vector search on summary layer (3-5K tokens)
Level 2: Precise context retrieval from full conversation (190K tokens)

This is the core innovation of .devsession format:
- Summary provides fast semantic search
- References link back to exact conversation sections
- Lossless: Can always verify summary against source
"""

from typing import List, Dict, Optional, Tuple, Any


class ContextRetriever:
    """Two-level retrieval: Summary search → Full context extraction"""

    def __init__(self, devsession):
        """
        Initialize retriever

        Args:
            devsession: DevSession instance with summary and conversation
        """
        self.devsession = devsession
        self.summary = devsession.summary
        self.conversation = devsession.conversation
        self.spans = getattr(devsession, "spans", []) or []
        self.span_lookup = {
            span["id"]: span for span in self.spans
            if isinstance(span, dict) and span.get("id")
        }

    def retrieve_full_context(
        self,
        summary_item: Dict[str, Any],
        expand_context: int = 5
    ) -> Dict[str, Any]:
        """
        Retrieve full conversation context for a summary item

        Range semantics: [start_index, end_index) - inclusive-exclusive, 0-based

        Args:
            summary_item: Summary item (decision, code_change, etc.) with message_range
            expand_context: Number of messages to include before/after range for context

        Returns:
            Dict with full discussion messages + metadata
        """
        start_idx, end_idx, core_member_indices, resolved_span_ids, resolved_via = self._resolve_item_bounds(summary_item)
        if start_idx is None or end_idx is None:
            return {
                "messages": [],
                "error": "Summary item missing span_ids and message_range"
            }

        start_idx = max(0, min(start_idx, len(self.conversation)))
        end_idx = max(start_idx, min(end_idx, len(self.conversation)))

        # Expand context (include N messages before/after for better understanding)
        expanded_start = max(0, start_idx - expand_context)
        expanded_end = min(len(self.conversation), end_idx + expand_context)

        # Extract messages
        messages = self.conversation[expanded_start:expanded_end]

        # Mark which messages are in the core range vs expanded context
        for i, msg in enumerate(messages):
            actual_idx = expanded_start + i
            if core_member_indices:
                msg["_in_core_range"] = actual_idx in core_member_indices
            else:
                msg["_in_core_range"] = (start_idx <= actual_idx < end_idx)
            msg["_message_id"] = f"msg_{actual_idx + 1:03d}"

        return {
            "summary_item": summary_item,
            "messages": messages,
            "core_range": {
                "start": start_idx,
                "end": end_idx,
                "count": len(core_member_indices) if core_member_indices else end_idx - start_idx
            },
            "expanded_range": {
                "start": expanded_start,
                "end": expanded_end,
                "count": expanded_end - expanded_start
            },
            "temporal_bounds": {
                "t_first": summary_item.get("t_first"),
                "t_last": summary_item.get("t_last")
            },
            "resolved_span_ids": resolved_span_ids,
            "resolved_via": resolved_via,
        }

    def retrieve_by_reference(
        self,
        message_id: str,
        context_window: int = 10
    ) -> List[Dict]:
        """
        Retrieve a specific message and surrounding context

        Args:
            message_id: Message ID (e.g., "msg_042")
            context_window: Number of messages before/after to include

        Returns:
            List of messages with context
        """
        msg_idx = self._resolve_message_index(message_id)
        if msg_idx is None:
            return []

        # Get surrounding context
        start = max(0, msg_idx - context_window)
        end = min(len(self.conversation), msg_idx + context_window + 1)

        messages = self.conversation[start:end]

        # Mark the target message
        for i, msg in enumerate(messages):
            actual_idx = start + i
            msg["_is_target"] = (actual_idx == msg_idx)
            msg["_message_id"] = f"msg_{actual_idx + 1:03d}"

        return messages

    def _resolve_message_index(self, message_id: Optional[str]) -> Optional[int]:
        """Resolve a message ID against stored message metadata or numeric suffix."""
        if not message_id:
            return None

        for idx, msg in enumerate(self.conversation):
            if msg.get("_message_id") == message_id or msg.get("_id") == message_id:
                return idx

        try:
            msg_num = int(message_id.split("_")[1])
        except (IndexError, ValueError):
            return None

        msg_idx = msg_num - 1
        if 0 <= msg_idx < len(self.conversation):
            return msg_idx
        return None

    def _resolve_span_bounds(self, span: Dict[str, Any]) -> Tuple[Optional[int], Optional[int]]:
        start_idx = span.get("start_index")
        end_idx = span.get("end_index")

        if not isinstance(start_idx, int):
            start_idx = self._resolve_message_index(span.get("start_message_id"))
        if not isinstance(end_idx, int):
            latest_index = span.get("latest_index")
            if isinstance(latest_index, int):
                end_idx = latest_index + 1
            else:
                end_ref = self._resolve_message_index(span.get("end_message_id") or span.get("latest_message_id"))
                end_idx = end_ref + 1 if end_ref is not None else None

        if start_idx is None or end_idx is None:
            return None, None

        return start_idx, end_idx

    def _resolve_item_bounds(
        self,
        summary_item: Dict[str, Any],
    ) -> Tuple[Optional[int], Optional[int], List[int], List[str], str]:
        span_ids = [
            span_id for span_id in summary_item.get("span_ids", [])
            if isinstance(span_id, str) and span_id in self.span_lookup
        ]

        if span_ids:
            resolved = [
                self._resolve_span_bounds(self.span_lookup[span_id])
                for span_id in span_ids
            ]
            resolved = [(start, end) for start, end in resolved if start is not None and end is not None]
            if resolved:
                core_member_indices = sorted({
                    idx
                    for span_id in span_ids
                    for idx in self._resolve_span_member_indices(self.span_lookup[span_id])
                })
                return (
                    min(start for start, _ in resolved),
                    max(end for _, end in resolved),
                    core_member_indices,
                    span_ids,
                    "span_ids",
                )

        msg_range = summary_item.get("message_range") or {}
        start_idx = msg_range.get("start_index")
        end_idx = msg_range.get("end_index")

        if not isinstance(start_idx, int):
            start_idx = self._resolve_message_index(msg_range.get("start"))

        if not isinstance(end_idx, int):
            end_ref = self._resolve_message_index(msg_range.get("end"))
            end_idx = end_ref + 1 if end_ref is not None else None

        if start_idx is None:
            start_idx = 0 if msg_range else None
        if end_idx is None:
            end_idx = len(self.conversation) if msg_range else None

        if start_idx is None or end_idx is None:
            return None, None, [], [], "none"

        if (
            start_idx < 0
            or start_idx >= len(self.conversation)
            or end_idx <= start_idx
            or end_idx > len(self.conversation)
        ):
            resolved_start = self._resolve_message_index(msg_range.get("start"))
            resolved_end = self._resolve_message_index(msg_range.get("end"))
            if resolved_start is not None:
                start_idx = resolved_start
            if resolved_end is not None:
                end_idx = resolved_end + 1

        return start_idx, end_idx, [], [], "message_range"

    def _resolve_span_member_indices(self, span: Dict[str, Any]) -> List[int]:
        message_ids = [
            message_id for message_id in span.get("message_ids", [])
            if isinstance(message_id, str)
        ]
        if message_ids:
            resolved = [self._resolve_message_index(message_id) for message_id in message_ids]
            return [idx for idx in resolved if idx is not None]

        start_idx, end_idx = self._resolve_span_bounds(span)
        if start_idx is None or end_idx is None:
            return []
        return list(range(start_idx, end_idx))

    def search_summary(
        self,
        query: str,
        top_k: int = 5,
        category: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        """
        Search summary layer (would use vector search in real implementation)

        Args:
            query: Search query
            top_k: Number of results to return
            category: Filter by category (decisions, code_changes, etc.)

        Returns:
            List of matching summary items
        """
        if not self.summary:
            return []

        # In real implementation, this would use vector embeddings + cosine similarity
        # For now, simple keyword matching as placeholder

        results = []
        categories = [category] if category else [
            "decisions", "code_changes", "problems_solved", "open_issues", "next_steps"
        ]

        query_lower = query.lower()

        for cat in categories:
            for item in self.summary.get(cat, []):
                # Simple keyword match (replace with vector search in Phase 5)
                item_text = str(item).lower()
                if query_lower in item_text:
                    results.append({
                        "category": cat,
                        "item": item,
                        "relevance_score": 1.0  # Placeholder
                    })

        # Sort by relevance (placeholder - would use cosine similarity)
        results.sort(key=lambda x: x["relevance_score"], reverse=True)

        return results[:top_k]

    def two_level_search(
        self,
        query: str,
        top_k: int = 3,
        expand_context: int = 5
    ) -> List[Dict[str, Any]]:
        """
        Full two-level search: Summary search → Context retrieval

        This is the core .devsession retrieval pattern:
        1. Fast search on summary layer (3-5K tokens)
        2. Retrieve full context from conversation (exact sections)

        Args:
            query: User query
            top_k: Number of summary items to retrieve
            expand_context: Context expansion for full retrieval

        Returns:
            List of results with summary items + full conversation context
        """
        # Level 1: Search summary (fast, semantic)
        summary_results = self.search_summary(query, top_k=top_k)

        # Level 2: Retrieve full context for each result
        full_results = []
        for result in summary_results:
            item = result["item"]

            # Get full conversation context
            full_context = self.retrieve_full_context(item, expand_context=expand_context)

            full_results.append({
                "summary": {
                    "category": result["category"],
                    "item": item,
                    "relevance": result["relevance_score"]
                },
                "full_context": full_context,
                "preview": self._generate_preview(full_context)
            })

        return full_results

    def _generate_preview(self, full_context: Dict[str, Any], max_messages: int = 3) -> str:
        """
        Generate a preview of the full context

        Args:
            full_context: Full context dict from retrieve_full_context
            max_messages: Max messages to include in preview

        Returns:
            Preview string
        """
        messages = full_context.get("messages", [])
        if not messages:
            return ""

        # Get first few messages from core range
        core_messages = [m for m in messages if m.get("_in_core_range")][:max_messages]

        preview_lines = []
        for msg in core_messages:
            role = msg.get("role", "unknown")
            content = msg.get("content", "")
            msg_id = msg.get("_message_id", "???")

            # Truncate long content
            if len(content) > 100:
                content = content[:100] + "..."

            preview_lines.append(f"{msg_id} ({role}): {content}")

        return "\n".join(preview_lines)

    def get_temporal_context(
        self,
        start_time: str,
        end_time: str
    ) -> List[Dict[str, Any]]:
        """
        Retrieve summary items within a time range

        Args:
            start_time: ISO timestamp
            end_time: ISO timestamp

        Returns:
            List of summary items in that time range
        """
        results = []

        if not self.summary:
            return results

        categories = ["decisions", "code_changes", "problems_solved", "open_issues", "next_steps"]

        for cat in categories:
            for item in self.summary.get(cat, []):
                t_first = item.get("t_first")
                t_last = item.get("t_last")

                # Check if item overlaps with query range
                if t_first and t_last:
                    if start_time <= t_last and end_time >= t_first:
                        results.append({
                            "category": cat,
                            "item": item
                        })

        return results


def format_context_for_llm(retrieval_result: Dict[str, Any]) -> str:
    """
    Format retrieval result for LLM consumption

    Args:
        retrieval_result: Result from two_level_search()

    Returns:
        Formatted string for LLM prompt
    """
    summary = retrieval_result["summary"]
    full_context = retrieval_result["full_context"]

    lines = []

    # Summary item
    lines.append(f"## {summary['category'].title()}")
    lines.append(f"**Summary:** {summary['item'].get('decision') or summary['item'].get('description') or summary['item'].get('problem')}")

    if summary['item'].get('reasoning'):
        lines.append(f"**Reasoning:** {summary['item']['reasoning']}")

    # Full conversation context
    lines.append("\n## Full Discussion:")

    messages = full_context.get("messages", [])
    for msg in messages:
        role = msg.get("role", "unknown")
        content = msg.get("content", "")
        msg_id = msg.get("_message_id", "")
        is_core = msg.get("_in_core_range", False)

        marker = ">>>" if is_core else "   "
        lines.append(f"{marker} {msg_id} ({role}): {content}")

    return "\n".join(lines)
