"""
Memory Middleware for RecCli Phase 6

Intelligent context loading from .devsession files.
Replaces 200K tokens with 2K intelligent tokens through:
- Summary layer (high-level overview)
- Recent messages (conversational continuity)
- Vector search (relevant history)
- Conditional project overview
"""

from typing import List, Dict, Optional, Iterator
from pathlib import Path
from datetime import datetime, timedelta
import re
import asyncio


class MemoryMiddleware:
    """
    Intelligent context loading from .devsession files

    The breakthrough that replaces 200K tokens with 2K intelligent tokens.
    """

    def __init__(self, session, sessions_dir: Path):
        """
        Initialize MemoryMiddleware

        Args:
            session: Loaded DevSession object
            sessions_dir: Path to sessions directory
        """
        self.session = session
        self.sessions_dir = sessions_dir
        self.token_budget = 2000
        self.soft_cap = 1800

        # Token allocation (dynamic)
        self.allocation = {
            'summary': 500,
            'recent': 500,
            'vector': 700,
            'project_overview': 300
        }

        # Reranking weights
        self.reranking_weights = {
            'recency': 0.2,
            'decision': 1.3,
            'code': 1.2,
            'problem': 1.25,
            'summary_ref': 1.4
        }

    def hydrate_prompt(
        self,
        user_input: str,
        num_recent: int = 20,
        include_wpc: bool = False
    ) -> Dict:
        """
        Build context for LLM from .devsession memory

        Args:
            user_input: Current user query
            num_recent: Number of recent messages to include
            include_wpc: Include Work Package Continuity prefetch

        Returns:
            Context dict with allocated tokens
        """
        context = {}
        tokens_used = 0

        # Layer 1: Always load summary
        if self.session.summary:
            context['summary'] = self.session.summary
            tokens_used += self._count_tokens(str(self.session.summary))
        else:
            context['summary'] = None

        # Layer 2: Recent messages (conversational continuity)
        if self.session.conversation:
            recent = self.session.conversation[-num_recent:]
            context['recent'] = recent
            tokens_used += self._count_tokens_messages(recent)
        else:
            recent = []
            context['recent'] = []

        # Layer 3: Conditionally load project overview
        if self._should_load_project_overview(recent, user_input):
            project_overview = self._load_project_overview()
            if project_overview:
                context['project_overview'] = project_overview
                tokens_used += 300
                vector_budget = 700
            else:
                vector_budget = 1000
        else:
            vector_budget = 1000

        # Layer 4: Vector search using recent as implicit goal
        if self.session.conversation and len(self.session.conversation) > num_recent:
            earlier = self.session.conversation[:-num_recent]

            # Use Phase 5 search for vector retrieval
            from .embeddings import get_embedding_provider

            try:
                provider = get_embedding_provider()

                # Embed recent messages as query
                query_text = ' '.join([m.get('content', '')[:200] for m in recent])
                query_embedding = provider.embed(query_text)

                # Search earlier messages
                similar = self._vector_search_local(
                    earlier,
                    query_embedding,
                    top_k=min(15, vector_budget // 70),
                    threshold=0.7
                )

                # Rerank by importance
                similar = self._rerank_by_importance(similar)

                context['relevant_history'] = similar
                tokens_used += self._count_tokens_messages(similar)

            except Exception as e:
                # Fallback if embeddings not available
                print(f"⚠️  Vector search failed: {e}")
                context['relevant_history'] = []
        else:
            context['relevant_history'] = []

        # Layer 5: Work Package Continuity (if enabled)
        if include_wpc and hasattr(self, 'wpc'):
            staged = self.wpc.get_staged_context(budget=900)
            if staged:
                context['wpc_staged'] = staged
                tokens_used += sum(s['tokens'] for s in staged)

        # Build structured prompt
        prompt = self._build_prompt(context, user_input)

        return {
            'prompt': prompt,
            'context': context,
            'tokens_used': tokens_used,
            'budget': self.token_budget,
            'allocation': {
                'summary': self._count_tokens(str(context.get('summary', ''))) if context.get('summary') else 0,
                'recent': self._count_tokens_messages(context.get('recent', [])),
                'relevant': self._count_tokens_messages(context.get('relevant_history', [])),
                'project': 300 if 'project_overview' in context else 0,
                'wpc': sum(s['tokens'] for s in context.get('wpc_staged', []))
            }
        }

    async def hydrate_prompt_streaming(
        self,
        user_input: str,
        num_recent: int = 20,
        llm_client: Optional[object] = None
    ) -> Iterator[Dict]:
        """
        Streaming version of hydrate_prompt with progressive enhancement

        Returns results in stages:
        1. Instant (0ms): Recent messages
        2. Fast (50ms): Quick vector search
        3. Smart (250ms): LLM reasoning + refined search

        Args:
            user_input: User query
            num_recent: Number of recent messages
            llm_client: Optional LLM client for reasoning

        Yields:
            Progressive result dicts with stage info
        """
        from .streaming_retrieval import StreamingRetrieval

        # Create streaming retrieval instance
        retrieval = StreamingRetrieval(
            self.session,
            self.sessions_dir,
            llm_client
        )

        # Stream results
        async for stage_result in retrieval.retrieve_streaming(user_input, num_recent):
            # Enrich with summary and project overview
            enriched = self._enrich_stage_result(stage_result, user_input)
            yield enriched

    def _enrich_stage_result(self, stage_result: Dict, user_input: str) -> Dict:
        """
        Enrich streaming stage result with summary and project overview

        Args:
            stage_result: Stage result from streaming retrieval
            user_input: User query

        Returns:
            Enriched result with full context
        """
        results = stage_result['results']
        recent = results.get('recent_messages', [])

        # Add summary (always include)
        if self.session.summary:
            results['summary'] = self.session.summary

        # Add project overview conditionally
        if stage_result['stage'] in ['fast', 'smart']:
            if self._should_load_project_overview(recent, user_input):
                project_overview = self._load_project_overview()
                if project_overview:
                    results['project_overview'] = project_overview

        # Build prompt for this stage
        context = {
            'summary': results.get('summary'),
            'recent': recent,
            'relevant_history': results.get('vector_results', []),
            'project_overview': results.get('project_overview')
        }

        prompt = self._build_prompt(context, user_input)
        stage_result['prompt'] = prompt
        stage_result['tokens_used'] = self._estimate_tokens(context)

        return stage_result

    def _estimate_tokens(self, context: Dict) -> int:
        """Estimate token count for context"""
        total = 0

        if context.get('summary'):
            total += self._count_tokens(str(context['summary']))

        if context.get('recent'):
            total += self._count_tokens_messages(context['recent'])

        if context.get('relevant_history'):
            total += self._count_tokens_messages(context['relevant_history'])

        if context.get('project_overview'):
            total += 300

        return total

    def _should_load_project_overview(
        self,
        recent_messages: List[Dict],
        user_input: str
    ) -> bool:
        """
        Decide if project overview is relevant for current context

        Returns True when:
        - Session start
        - Macro questions
        - Context switch
        - Project overview changed recently
        - Long break (>7 days)

        Returns False when:
        - Deep in implementation
        - Continuing same task
        """
        # Check if macro query
        if self._is_macro_query(recent_messages, user_input):
            return True

        # Check if context switch
        if len(recent_messages) >= 10:
            if self._is_context_switch(recent_messages):
                return True

        # Check if deep implementation work
        if self._is_deep_implementation(recent_messages):
            return False

        # Check if continuing same task
        if self._is_continuing_same_task(recent_messages):
            return False

        # Check for long break
        if self._has_long_break():
            return True

        # Default: load it (safer)
        return True

    def _is_macro_query(self, recent_messages: List[Dict], user_input: str) -> bool:
        """Detect if user is asking project-level questions"""
        macro_keywords = [
            'project', 'architecture', 'what is', 'overview',
            'purpose', 'goals', 'decisions', 'tech stack',
            'how does', 'explain the', 'big picture'
        ]

        # Check user input
        user_lower = user_input.lower()
        if any(keyword in user_lower for keyword in macro_keywords):
            return True

        # Check recent messages
        if recent_messages:
            recent_text = ' '.join([m.get('content', '')[:100] for m in recent_messages[-3:]])
            if any(keyword in recent_text.lower() for keyword in macro_keywords):
                return True

        return False

    def _is_context_switch(self, recent_messages: List[Dict]) -> bool:
        """Detect if switching between different areas of work"""
        if len(recent_messages) < 10:
            return False

        # Simple heuristic: check if recent work differs from earlier work
        # by looking at vocabulary overlap

        earlier_text = ' '.join([m.get('content', '')[:100] for m in recent_messages[:5]])
        recent_text = ' '.join([m.get('content', '')[:100] for m in recent_messages[-5:]])

        earlier_words = set(earlier_text.lower().split())
        recent_words = set(recent_text.lower().split())

        if not earlier_words or not recent_words:
            return False

        # Jaccard similarity
        overlap = len(earlier_words & recent_words) / len(earlier_words | recent_words)

        # Low overlap suggests context switch
        return overlap < 0.3

    def _is_deep_implementation(self, recent_messages: List[Dict]) -> bool:
        """Detect if deep in implementation details"""
        implementation_patterns = [
            r'debug', r'error', r'fix', r'bug', r'typo',
            r'line \d+', r'function \w+\(',
            r'variable', r'import', r'syntax',
            r'def ', r'class ', r'const ', r'let ',
            r'\.py', r'\.js', r'\.ts', r'\.go'
        ]

        if not recent_messages:
            return False

        recent_text = ' '.join([m.get('content', '') for m in recent_messages[-10:]])

        # Count implementation patterns
        matches = sum(1 for pattern in implementation_patterns
                      if re.search(pattern, recent_text, re.I))

        # If 5+ implementation patterns in last 10 messages
        return matches >= 5

    def _is_continuing_same_task(self, recent_messages: List[Dict]) -> bool:
        """Detect if continuing same incremental work"""
        if len(recent_messages) < 5:
            return False

        # Check if recent messages have high vocabulary overlap
        all_text = ' '.join([m.get('content', '')[:100] for m in recent_messages])
        words = all_text.lower().split()

        if len(words) < 20:
            return False

        # Check for repeated words (sign of same task)
        word_counts = {}
        for word in words:
            if len(word) > 4:  # Ignore short words
                word_counts[word] = word_counts.get(word, 0) + 1

        # If many repeated meaningful words, likely same task
        repeated = sum(1 for count in word_counts.values() if count >= 3)

        return repeated >= 5

    def _has_long_break(self) -> bool:
        """Check if there's been a long break (>7 days) since last session"""
        if not self.session.metadata:
            return False

        created = self.session.metadata.get('created_at', '')
        if not created:
            return False

        try:
            created_time = datetime.fromisoformat(created.replace('Z', '+00:00'))
            now = datetime.now().astimezone()
            days_since = (now - created_time).total_seconds() / 86400

            return days_since > 7
        except:
            return False

    def _load_project_overview(self) -> Optional[Dict]:
        """Load .devproject file if it exists"""
        from .devproject import resolve_devproject_path

        project_root = self.session.metadata.get("project_root") if getattr(self.session, "metadata", None) else None
        if project_root:
            devproject_path = resolve_devproject_path(Path(project_root).expanduser())
            if devproject_path.exists():
                try:
                    import json
                    with open(devproject_path, 'r') as f:
                        return json.load(f)
                except:
                    pass

        # Look for .devproject in parent directories
        current = self.sessions_dir.parent

        for _ in range(5):  # Search up to 5 levels
            devproject_path = resolve_devproject_path(current)
            if devproject_path.exists():
                try:
                    import json
                    with open(devproject_path, 'r') as f:
                        return json.load(f)
                except:
                    pass

            # Go up one level
            parent = current.parent
            if parent == current:  # Reached root
                break
            current = parent

        return None

    def _vector_search_local(
        self,
        messages: List[Dict],
        query_embedding: List[float],
        top_k: int = 15,
        threshold: float = 0.7
    ) -> List[Dict]:
        """
        Local vector search on messages with embeddings

        Args:
            messages: Messages to search
            query_embedding: Query vector
            top_k: Number of results
            threshold: Minimum cosine similarity

        Returns:
            Top-k most similar messages
        """
        from .embeddings import cosine_similarity

        results = []

        if getattr(self.session, "embedding_storage", {}).get("mode") == "external" and not getattr(self.session, "embedding_storage", {}).get("loaded"):
            self.session.load_external_message_embeddings()

        for msg in messages:
            if msg.get("deleted"):
                continue
            if 'embedding' not in msg:
                continue

            similarity = cosine_similarity(query_embedding, msg['embedding'])

            if similarity >= threshold:
                msg_copy = {**msg}
                msg_copy['cosine_score'] = similarity
                results.append(msg_copy)

        # Sort by similarity
        results.sort(key=lambda x: x['cosine_score'], reverse=True)

        return results[:top_k]

    def _rerank_by_importance(
        self,
        messages: List[Dict],
        current_time: Optional[datetime] = None
    ) -> List[Dict]:
        """
        Rerank vector search results by importance factors

        Boosts:
        - Recency (up to 20%)
        - Decision messages (1.3×)
        - Code changes (1.2×)
        - Problem solutions (1.25×)
        - In summary (1.4×)
        """
        if current_time is None:
            current_time = datetime.now().astimezone()

        scored = []

        for msg in messages:
            score = msg.get('cosine_score', 0.5)  # Base: vector similarity

            # Boost recent messages
            timestamp = msg.get('timestamp', '')
            if timestamp:
                try:
                    msg_time = datetime.fromisoformat(timestamp.replace('Z', '+00:00'))
                    age_hours = (current_time - msg_time).total_seconds() / 3600
                    recency_boost = 1.0 / (1.0 + age_hours / 24)  # Decay over days
                    score *= (1 + recency_boost * self.reranking_weights['recency'])
                except:
                    pass

            # Boost important types
            kind = msg.get('kind', 'note')
            if kind == 'decision':
                score *= self.reranking_weights['decision']
            elif kind == 'code':
                score *= self.reranking_weights['code']
            elif kind == 'problem':
                score *= self.reranking_weights['problem']

            # Boost if in summary
            if self._is_in_summary(msg):
                score *= self.reranking_weights['summary_ref']

            scored.append((score, msg))

        # Sort by final score
        scored.sort(reverse=True, key=lambda x: x[0])

        return [msg for score, msg in scored]

    def _is_in_summary(self, msg: Dict) -> bool:
        """Check if message is referenced in summary"""
        msg_id = (
            msg.get('message_id')
            or msg.get('id')
            or msg.get('_message_id')
            or msg.get('_id')
            or ''
        )
        if not msg_id or not self.session.summary:
            return False

        # Check decisions
        for dec in self.session.summary.get('decisions', []):
            if msg_id in dec.get('references', []):
                return True

        # Check problems
        for prob in self.session.summary.get('problems_solved', []):
            if msg_id in prob.get('references', []):
                return True

        # Check code changes
        for change in self.session.summary.get('code_changes', []):
            if msg_id in change.get('references', []):
                return True

        return False

    def _build_prompt(self, context: Dict, user_input: str) -> str:
        """
        Build structured prompt from context

        Returns formatted prompt with all context layers
        """
        sections = []

        # Header
        sections.append("# RecCli Context")
        sections.append("")

        # Project overview (if loaded)
        if 'project_overview' in context:
            proj = context['project_overview']
            sections.append("## Project Overview")
            sections.append(f"**Name**: {proj.get('name', 'N/A')}")
            sections.append(f"**Purpose**: {proj.get('purpose', 'N/A')}")
            if 'tech_stack' in proj:
                sections.append(f"**Tech Stack**: {', '.join(proj.get('tech_stack', []))}")
            sections.append("")

        # Session summary (if exists)
        if context.get('summary'):
            summary = context['summary']
            sections.append("## Session Summary")

            if isinstance(summary, dict):
                if 'overview' in summary:
                    sections.append(f"**Overview**: {summary['overview']}")

                if summary.get('decisions'):
                    sections.append("\n**Key Decisions**:")
                    for i, dec in enumerate(summary['decisions'][:3], 1):  # Top 3
                        sections.append(f"{i}. {dec.get('description', 'N/A')}")

                if summary.get('problems_solved'):
                    sections.append("\n**Problems Solved**:")
                    for i, prob in enumerate(summary['problems_solved'][:3], 1):
                        sections.append(f"{i}. {prob.get('description', 'N/A')}")

                if summary.get('next_steps'):
                    sections.append("\n**Next Steps**:")
                    for step in summary['next_steps'][:3]:
                        sections.append(f"- {step}")
            else:
                sections.append(str(summary)[:500])

            sections.append("")

        # Relevant history from vector search
        if context.get('relevant_history'):
            sections.append("## Relevant History (Vector Search)")
            for msg in context['relevant_history'][:5]:  # Top 5
                role = msg.get('role', 'unknown')
                content = msg.get('content', '')[:200]  # Preview
                kind = msg.get('kind', 'note')
                score = msg.get('cosine_score', 0)

                sections.append(f"- [{kind}] {role}: {content}... (similarity: {score:.2f})")
            sections.append("")

        # Recent messages for continuity
        if context.get('recent'):
            sections.append("## Recent Messages (Continuity)")
            for msg in context['recent'][-5:]:  # Last 5
                role = msg.get('role', 'unknown')
                content = msg.get('content', '')[:200]
                sections.append(f"- {role}: {content}...")
            sections.append("")

        # Current query
        sections.append("## Current Query")
        sections.append(user_input)
        sections.append("")

        return '\n'.join(sections)

    def _count_tokens(self, text: str) -> int:
        """Estimate token count (rough approximation)"""
        if not text:
            return 0

        # Rough estimate: 1 token ≈ 0.75 words
        words = len(text.split())
        return int(words * 1.3)

    def _count_tokens_messages(self, messages: List[Dict]) -> int:
        """Count tokens in list of messages"""
        if not messages:
            return 0

        total = 0
        for msg in messages:
            content = msg.get('content', '')
            total += self._count_tokens(content)

        return total
