"""
Preemptive Compaction for RecCli Phase 7

Auto-compact at 190K tokens (before Claude Code's 200K limit)
Replaces live context with intelligent summary + recent + relevant spans.

The breakthrough: Beat Claude Code to compaction, use OUR custom prompt.
"""

from typing import Dict, List, Optional, Tuple, Any
from datetime import datetime
from pathlib import Path
import json

from .tokens import TokenCounter
from .summarizer import SessionSummarizer
from .embeddings import OpenAIEmbeddings
from .memory_middleware import MemoryMiddleware
from .wpc import WorkPackageContinuity
from .search import search
from .compaction_log import CompactionLog


class PreemptiveCompactor:
    """
    Monitor token usage and trigger intelligent compaction at 190K tokens

    Flow:
    1. Watch token_counts during chat
    2. Warn at 180K tokens
    3. Compact at 190K tokens:
       - Generate summary (custom prompt)
       - Use vector search for relevant context
       - Use WPC for likely-next artifacts
       - Build 25-30K token compacted context
    4. Continue chat seamlessly
    """

    WARN_THRESHOLD = 180_000
    COMPACT_THRESHOLD = 190_000
    TARGET_POST_COMPACTION = 28_000  # ~25-30K target

    def __init__(
        self,
        session,
        sessions_dir: Path,
        llm_client=None,
        model: str = "claude-3-5-sonnet-20241022"
    ):
        """
        Initialize preemptive compactor

        Args:
            session: DevSession object
            sessions_dir: Path to sessions directory
            llm_client: LLM client for summary generation (Anthropic or OpenAI)
            model: Model to use for summarization
        """
        self.session = session
        self.sessions_dir = sessions_dir
        self.llm_client = llm_client
        self.model = model

        # Components
        self.token_counter = TokenCounter()
        self.summarizer = SessionSummarizer(llm_client=llm_client, model=model)
        self.middleware = MemoryMiddleware(session, sessions_dir)
        self.wpc = WorkPackageContinuity(session, sessions_dir)
        self.compaction_log = CompactionLog(session.session_id)

        # State
        self.compaction_triggered = False
        self.last_warning_at = 0
        self.compaction_count = 0

    def check_and_compact(self) -> Optional[Dict]:
        """
        Check token count and trigger compaction if needed

        Returns:
            Compacted context dict if compaction triggered, None otherwise
        """
        # Calculate current token count
        tokens = self._calculate_total_tokens()

        # Update session token_counts
        self.session.token_counts = {
            "conversation": self.token_counter.count_conversation(self.session.conversation),
            "terminal_output": 0,  # Compacted away
            "summary": self.token_counter.count_text(str(self.session.summary)) if self.session.summary else 0,
            "total": tokens,
            "last_updated": datetime.now().isoformat()
        }

        # Check thresholds
        if tokens >= self.COMPACT_THRESHOLD:
            if not self.compaction_triggered:
                return self.trigger_compaction(tokens)

        elif tokens >= self.WARN_THRESHOLD:
            # Warn every 5K tokens to avoid spam
            if tokens - self.last_warning_at >= 5000:
                self._warn_approaching_limit(tokens)
                self.last_warning_at = tokens

        return None

    def trigger_compaction(self, tokens_before: int) -> Dict:
        """
        Execute preemptive compaction

        Args:
            tokens_before: Token count before compaction

        Returns:
            Compacted context dict
        """
        print("\n" + "="*60)
        print("🔄 PREEMPTIVE COMPACTION TRIGGERED")
        print("="*60)
        print(f"📊 Context approaching limit: {tokens_before:,} tokens")
        print("📦 Compacting with .devsession strategy...")
        print()

        # Log compaction start
        operation_id = self.compaction_log.log_compaction_start(
            session_data_before=self.session.to_dict(),
            metadata={
                'reason': 'token_limit',
                'tokens_before': tokens_before,
                'threshold': self.COMPACT_THRESHOLD
            }
        )

        # Create backup
        backup_path = self.compaction_log.create_backup(operation_id)
        print(f"💾 Backup created: {backup_path.name}")

        try:
            # Step 1: Generate fresh summary with custom prompt
            print("📝 Generating summary with custom prompt...")
            summary = self._generate_summary()

            if summary:
                self.session.summary = summary
                print(f"   ✓ Summary generated ({len(summary.get('decisions', []))} decisions, "
                      f"{len(summary.get('code_changes', []))} code changes)")
            else:
                print("   ⚠️  Summary generation skipped (no LLM client)")
                # Keep existing summary if any
                summary = self.session.summary

            # Step 2: Generate embeddings if not already done
            print("🔢 Ensuring embeddings are up to date...")
            self._ensure_embeddings()
            print("   ✓ Embeddings ready")

            # Step 3: Extract recent messages (implicit goal)
            recent_messages = self.session.conversation[-20:]
            print(f"📌 Extracted {len(recent_messages)} recent messages as implicit goal")

            # Step 4: Use vector search to find ≤3 key spans for continuity
            print("🔍 Searching for relevant context spans...")
            relevant_spans = self._find_relevant_spans(recent_messages)
            print(f"   ✓ Found {len(relevant_spans)} relevant spans")

            # Step 5: Use WPC predictions for likely-next artifacts
            print("🔮 Generating Work Package Continuity predictions...")
            wpc_predictions = self._get_wpc_predictions()
            print(f"   ✓ {len(wpc_predictions)} artifacts predicted")

            # Step 6: Build compacted context
            print("🎯 Building compacted context...")
            compacted_context = self._build_compacted_context(
                summary=summary,
                recent=recent_messages,
                relevant_spans=relevant_spans,
                wpc_predictions=wpc_predictions
            )

            tokens_after = compacted_context['token_count']
            print(f"   ✓ Compacted context: {tokens_after:,} tokens")

            # Step 7: Persist compaction event
            compaction_event = {
                'timestamp': datetime.now().isoformat(),
                'tokens_before': tokens_before,
                'tokens_after': tokens_after,
                'summary_id': summary.get('id'),
                'retained_spans': [s['id'] for s in relevant_spans],
                'wpc_predictions': [p['id'] for p in wpc_predictions],
                'compaction_number': self.compaction_count + 1
            }

            if not hasattr(self.session, 'compaction_history'):
                self.session.compaction_history = []
            self.session.compaction_history.append(compaction_event)

            # Step 8: Save session
            print("💾 Saving .devsession file...")
            self.session.save()
            print(f"   ✓ Saved to {self.session.session_id}.devsession")

            # Log completion
            self.compaction_log.log_compaction_complete(
                operation_id=operation_id,
                session_data_after=self.session.to_dict(),
                success=True,
                metadata={
                    'tokens_after': tokens_after,
                    'reduction_ratio': tokens_before / tokens_after if tokens_after > 0 else 0
                }
            )

            # Update state
            self.compaction_triggered = True
            self.compaction_count += 1

            # Print summary
            print()
            print("="*60)
            print("✅ COMPACTION COMPLETE")
            print("="*60)
            print(f"📉 Reduction: {tokens_before:,} → {tokens_after:,} tokens")
            print(f"💰 Saved: {tokens_before - tokens_after:,} tokens ({(1 - tokens_after/tokens_before)*100:.1f}% reduction)")
            print(f"📄 Full session saved with {len(self.session.conversation)} messages")
            print(f"🔍 Context ready: Summary + {len(recent_messages)} recent + {len(relevant_spans)} relevant spans")
            print("💬 Continuing conversation with focused context...")
            print("="*60)
            print()

            return compacted_context

        except Exception as e:
            print(f"\n❌ Compaction failed: {e}")
            print("🔄 Rolling back to backup...")

            # Log failure
            self.compaction_log.log_compaction_complete(
                operation_id=operation_id,
                session_data_after=None,
                success=False,
                metadata={'error': str(e)}
            )

            # Rollback
            self.compaction_log.rollback_to_backup(operation_id)
            raise

    def _calculate_total_tokens(self) -> int:
        """Calculate total tokens in conversation"""
        if not self.session.conversation:
            return 0
        return self.token_counter.count_conversation(self.session.conversation)

    def _warn_approaching_limit(self, tokens: int):
        """Warn user that compaction is approaching"""
        percentage = (tokens / self.COMPACT_THRESHOLD) * 100
        remaining = self.COMPACT_THRESHOLD - tokens

        print(f"\n⚠️  Context Warning: {tokens:,} tokens ({percentage:.1f}% of limit)")
        print(f"   Compaction will trigger at {self.COMPACT_THRESHOLD:,} tokens ({remaining:,} remaining)")
        print()

    def _generate_summary(self) -> Dict:
        """Generate summary using custom prompt"""
        if not self.llm_client:
            print("⚠️  No LLM client available - cannot generate summary")
            print("   Compaction will proceed without AI-generated summary")
            return None

        return self.summarizer.summarize_session(
            conversation=self.session.conversation
        )

    def _ensure_embeddings(self):
        """Ensure embeddings are generated for all messages"""
        # Check if embeddings already exist
        if self.session.vector_index and len(self.session.vector_index.get('unified_vectors', [])) == len(self.session.conversation):
            return  # Already done

        # Generate embeddings
        from .vector_index import build_unified_index
        index = build_unified_index(
            sessions_dir=self.sessions_dir,
            session_files=[self.session.session_id]
        )
        self.session.vector_index = index

    def _find_relevant_spans(self, recent_messages: List[Dict]) -> List[Dict]:
        """
        Use vector search to find ≤3 key spans for continuity

        Args:
            recent_messages: Recent messages to use as query

        Returns:
            List of relevant span dicts
        """
        # Use recent messages as implicit goal
        query = " ".join([m.get('content', '')[:200] for m in recent_messages[-5:]])

        # Search for relevant context (top 3 spans)
        try:
            results = search(
                query=query,
                sessions_dir=self.sessions_dir,
                k=3,
                scope={'session': self.session.session_id}
            )

            return results
        except Exception as e:
            print(f"   ⚠️  Vector search failed: {e}")
            return []

    def _get_wpc_predictions(self) -> List[Dict]:
        """
        Get Work Package Continuity predictions for likely-next artifacts

        Returns:
            List of predicted artifact dicts
        """
        signal = {
            'recent_messages': self.session.conversation[-50:] if len(self.session.conversation) >= 50 else self.session.conversation,
            'summary': self.session.summary,
            'next_steps': self.session.summary.get('next_steps', []) if self.session.summary else []
        }

        try:
            predictions = self.wpc.predict_next(signal)
            return predictions[:3]  # Top 3 predictions
        except Exception as e:
            print(f"   ⚠️  WPC prediction failed: {e}")
            return []

    def _build_compacted_context(
        self,
        summary: Dict,
        recent: List[Dict],
        relevant_spans: List[Dict],
        wpc_predictions: List[Dict]
    ) -> Dict:
        """
        Build the compacted context for continuation

        Args:
            summary: Session summary
            recent: Recent messages
            relevant_spans: Relevant context spans
            wpc_predictions: WPC predictions

        Returns:
            Compacted context dict with token count
        """
        # Use MemoryMiddleware to build context
        context = self.middleware.hydrate_prompt(
            user_input="",  # Use recent as implicit goal
            num_recent=20,
            include_wpc=True
        )

        # Add WPC predictions
        context['wpc_predictions'] = wpc_predictions

        # Calculate total tokens
        token_count = (
            context.get('token_count', 0) +
            sum(self.token_counter.count_text(str(p)) for p in wpc_predictions)
        )

        context['token_count'] = token_count

        return context

    def manual_compact(self) -> Dict:
        """
        Manually trigger compaction regardless of token count

        Returns:
            Compacted context dict
        """
        tokens = self._calculate_total_tokens()
        print(f"🔧 Manual compaction triggered at {tokens:,} tokens")
        return self.trigger_compaction(tokens)

    def get_status(self) -> Dict:
        """
        Get current compaction status

        Returns:
            Status dict with token counts and thresholds
        """
        tokens = self._calculate_total_tokens()

        return {
            'current_tokens': tokens,
            'warn_threshold': self.WARN_THRESHOLD,
            'compact_threshold': self.COMPACT_THRESHOLD,
            'percentage': (tokens / self.COMPACT_THRESHOLD) * 100,
            'remaining': self.COMPACT_THRESHOLD - tokens,
            'compaction_triggered': self.compaction_triggered,
            'compaction_count': self.compaction_count,
            'status': self._get_status_level(tokens)
        }

    def _get_status_level(self, tokens: int) -> str:
        """Get status level based on token count"""
        if tokens >= self.COMPACT_THRESHOLD:
            return 'critical'
        elif tokens >= self.WARN_THRESHOLD:
            return 'warning'
        else:
            return 'ok'
