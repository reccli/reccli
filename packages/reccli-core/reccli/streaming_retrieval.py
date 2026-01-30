"""
Streaming Hybrid Retrieval for RecCli

Progressive enhancement: Instant → Fast → Smart
- Instant (0ms): Recent messages (always in memory)
- Fast (50ms): Quick vector search
- Smart (250ms): LLM reasoning + refined multi-search

Provides streaming results so user sees progress.
"""

from typing import List, Dict, Optional, Iterator, Any
from pathlib import Path
from datetime import datetime
import asyncio
import re


class QueryClassifier:
    """
    Classify queries to determine if LLM reasoning is needed

    Skip expensive LLM reasoning when query is already clear and specific.
    """

    @staticmethod
    def needs_reasoning(query: str, recent_messages: List[Dict] = None) -> bool:
        """
        Determine if query needs LLM reasoning

        Args:
            query: User query
            recent_messages: Recent conversation context

        Returns:
            True if LLM reasoning would help
        """
        query_lower = query.lower()

        # Pattern 1: Contains pronouns (needs resolution)
        pronouns = ['it', 'that', 'this', 'they', 'them', 'those', 'these']
        if any(f' {p} ' in f' {query_lower} ' or query_lower.startswith(f'{p} ')
               for p in pronouns):
            return True

        # Pattern 2: Contains temporal words (needs date calculation)
        temporal = ['yesterday', 'today', 'recent', 'last', 'latest',
                   'current', 'now', 'earlier', 'before', 'after']
        if any(word in query_lower for word in temporal):
            return True

        # Pattern 3: Vague/short query (needs expansion)
        words = query.split()
        if len(words) < 5:
            # Short queries are often vague: "the bug", "auth", "what happened"
            return True

        # Pattern 4: Contains negation (needs careful filtering)
        negations = ['not', 'except', 'without', 'exclude', "don't", "doesn't"]
        if any(neg in query_lower for neg in negations):
            return True

        # Pattern 5: Contains definite article "the" (implies specific context)
        if query_lower.startswith('the ') or ' the ' in query_lower:
            # "the decision", "the bug" - needs recency/context
            return True

        # Pattern 6: Question without specifics
        question_words = ['what', 'why', 'how', 'when', 'where', 'which']
        starts_with_question = any(query_lower.startswith(q) for q in question_words)
        has_specific_noun = any(noun in query_lower for noun in [
            'function', 'class', 'file', 'error', 'bug', 'feature',
            'test', 'api', 'endpoint', 'schema', 'table'
        ])

        if starts_with_question and not has_specific_noun:
            # "What happened?" vs "What function handles auth?"
            return True

        # Clear and specific - direct search is fine
        return False

    @staticmethod
    def extract_context_from_recent(query: str, recent_messages: List[Dict]) -> Dict:
        """
        Extract context hints from recent messages

        Helps with pronoun resolution even without full LLM reasoning.

        Args:
            query: User query
            recent_messages: Recent conversation

        Returns:
            Context hints dict
        """
        hints = {
            'current_file': None,
            'current_topic': None,
            'recent_keywords': []
        }

        if not recent_messages:
            return hints

        # Get last 3 assistant messages for context
        assistant_messages = [
            msg for msg in recent_messages[-10:]
            if msg.get('role') == 'assistant'
        ][-3:]

        # Extract file mentions
        file_pattern = r'([a-zA-Z0-9_\-/]+\.(py|js|ts|go|java|cpp|c|h|md|txt|json|yaml|yml))'
        for msg in assistant_messages:
            content = msg.get('content', '')
            matches = re.findall(file_pattern, content, re.I)
            if matches:
                hints['current_file'] = matches[-1][0]  # Most recent file
                break

        # Extract frequent keywords (topic detection)
        text = ' '.join(msg.get('content', '') for msg in assistant_messages)
        words = text.lower().split()
        word_freq = {}
        for word in words:
            if len(word) > 4:  # Skip short words
                word_freq[word] = word_freq.get(word, 0) + 1

        # Get top 3 keywords
        top_keywords = sorted(word_freq.items(), key=lambda x: x[1], reverse=True)[:3]
        hints['recent_keywords'] = [word for word, count in top_keywords if count > 1]

        if hints['recent_keywords']:
            hints['current_topic'] = hints['recent_keywords'][0]

        return hints


class LLMReasoner:
    """
    LLM-based query reasoning and expansion

    Uses 100 tokens to understand user intent and generate better search queries.
    """

    def __init__(self, llm_client=None):
        """
        Initialize reasoner

        Args:
            llm_client: Optional LLM client for reasoning
        """
        self.llm_client = llm_client
        self.reasoning_budget = 100

    async def reason_about_query(
        self,
        user_query: str,
        recent_messages: List[Dict],
        summary: Optional[Dict] = None
    ) -> Dict:
        """
        Reason about user query to generate better searches

        Args:
            user_query: User's query
            recent_messages: Recent conversation
            summary: Session summary

        Returns:
            Reasoning result with expanded searches
        """
        if not self.llm_client:
            # Fallback to heuristic reasoning
            return self._heuristic_reasoning(user_query, recent_messages)

        # Build reasoning prompt
        prompt = self._build_reasoning_prompt(user_query, recent_messages, summary)

        try:
            # Use LLM for reasoning (100 tokens)
            reasoning = await self.llm_client.complete(
                prompt,
                max_tokens=self.reasoning_budget,
                temperature=0.3
            )

            # Parse reasoning into structured searches
            parsed = self._parse_reasoning(reasoning)
            return parsed

        except Exception as e:
            # Fallback to heuristic
            return self._heuristic_reasoning(user_query, recent_messages)

    def _build_reasoning_prompt(
        self,
        query: str,
        recent: List[Dict],
        summary: Optional[Dict]
    ) -> str:
        """Build compact reasoning prompt"""
        # Get context
        recent_text = "\n".join([
            f"{msg.get('role', 'unknown')}: {msg.get('content', '')[:100]}..."
            for msg in recent[-3:]
        ])

        return f"""
Recent conversation:
{recent_text}

User query: "{query}"

Analyze this query and suggest searches (max 100 tokens):

1. Intent: What does the user want to know?
2. Context: What implicit context from conversation?
3. Searches: 2-4 specific search queries
4. Time focus: Recent (24h), medium (week), or all time?
5. Categories: decisions, code_changes, problems, issues, next_steps?

Format:
Intent: [what user wants]
Searches: ["query 1", "query 2", "query 3"]
Time: [recent|medium|all]
Categories: [decision,code,problem]
"""

    def _parse_reasoning(self, reasoning: str) -> Dict:
        """Parse LLM reasoning into structured result"""
        result = {
            'intent': '',
            'searches': [],
            'time_focus': 'all',
            'categories': ['decisions', 'code_changes', 'problems_solved'],
            'raw_reasoning': reasoning
        }

        lines = reasoning.strip().split('\n')

        for line in lines:
            line_lower = line.lower()

            # Extract intent
            if 'intent:' in line_lower:
                result['intent'] = line.split(':', 1)[1].strip()

            # Extract searches
            elif 'searches:' in line_lower or 'queries:' in line_lower:
                # Look for quoted strings
                import re
                quotes = re.findall(r'"([^"]+)"', line)
                if quotes:
                    result['searches'] = quotes
                else:
                    # Fallback: comma-separated
                    parts = line.split(':', 1)
                    if len(parts) > 1:
                        result['searches'] = [
                            s.strip() for s in parts[1].split(',')
                            if s.strip()
                        ]

            # Extract time focus
            elif 'time:' in line_lower:
                if 'recent' in line_lower:
                    result['time_focus'] = 'recent'
                elif 'medium' in line_lower or 'week' in line_lower:
                    result['time_focus'] = 'medium'
                else:
                    result['time_focus'] = 'all'

            # Extract categories
            elif 'categories:' in line_lower or 'category:' in line_lower:
                parts = line.split(':', 1)
                if len(parts) > 1:
                    cat_text = parts[1].lower()
                    result['categories'] = []
                    if 'decision' in cat_text:
                        result['categories'].append('decisions')
                    if 'code' in cat_text:
                        result['categories'].append('code_changes')
                    if 'problem' in cat_text:
                        result['categories'].append('problems_solved')
                    if 'issue' in cat_text:
                        result['categories'].append('open_issues')
                    if 'next' in cat_text or 'step' in cat_text:
                        result['categories'].append('next_steps')

        # Fallback: if no searches extracted, use original query
        if not result['searches']:
            result['searches'] = [reasoning[:100]]  # Use first part of reasoning

        return result

    def _heuristic_reasoning(
        self,
        query: str,
        recent_messages: List[Dict]
    ) -> Dict:
        """
        Fallback heuristic reasoning (no LLM)

        Simple query expansion based on patterns
        """
        result = {
            'intent': query,
            'searches': [query],  # Start with original
            'time_focus': 'all',
            'categories': ['decisions', 'code_changes', 'problems_solved'],
            'raw_reasoning': 'heuristic'
        }

        query_lower = query.lower()

        # Pattern 1: "How does X work?" → search for X + architecture/design
        if 'how' in query_lower and 'work' in query_lower:
            # Extract topic
            topic = re.search(r'how (?:does |do )?(\w+)', query_lower)
            if topic:
                topic_word = topic.group(1)
                result['searches'].append(f"{topic_word} architecture")
                result['searches'].append(f"{topic_word} design")

        # Pattern 2: "Why did we choose X?" → search for decisions + alternatives
        if 'why' in query_lower and ('choose' in query_lower or 'decide' in query_lower):
            result['searches'].append(query + " alternatives")
            result['searches'].append(query + " comparison")
            result['categories'] = ['decisions']
            result['time_focus'] = 'medium'

        # Pattern 3: Temporal queries → filter by time
        if any(word in query_lower for word in ['yesterday', 'today', 'recent', 'last']):
            result['time_focus'] = 'recent'

        # Pattern 4: Error/bug queries → search problems
        if any(word in query_lower for word in ['error', 'bug', 'issue', 'problem', 'fix']):
            result['categories'] = ['problems_solved', 'open_issues']
            result['searches'].append(query + " traceback")
            result['searches'].append(query + " error message")

        # Pattern 5: Code queries → search code changes
        if any(word in query_lower for word in ['function', 'class', 'code', 'implement']):
            result['categories'] = ['code_changes']

        return result


class StreamingRetrieval:
    """
    Streaming hybrid retrieval with progressive enhancement

    Returns results in stages:
    1. Instant: Recent messages (0ms)
    2. Fast: Quick vector search (50ms)
    3. Smart: LLM-reasoned multi-search (250ms)
    """

    def __init__(self, session, sessions_dir: Path, llm_client=None):
        """
        Initialize streaming retrieval

        Args:
            session: DevSession object
            sessions_dir: Path to sessions directory
            llm_client: Optional LLM client for reasoning
        """
        self.session = session
        self.sessions_dir = sessions_dir
        self.classifier = QueryClassifier()
        self.reasoner = LLMReasoner(llm_client)

    async def retrieve_streaming(
        self,
        user_query: str,
        num_recent: int = 20
    ) -> Iterator[Dict]:
        """
        Streaming retrieval with progressive results

        Args:
            user_query: User query
            num_recent: Number of recent messages

        Yields:
            Progressive result dicts with 'stage' and 'results'
        """
        # Stage 1: INSTANT - Recent messages (always available)
        recent = self.session.conversation[-num_recent:] if self.session.conversation else []

        yield {
            'stage': 'instant',
            'status': 'partial',
            'results': {
                'recent_messages': recent,
                'count': len(recent)
            },
            'latency_ms': 0
        }

        # Stage 2: FAST - Quick vector search
        start_fast = datetime.now()

        # Get context hints
        context_hints = self.classifier.extract_context_from_recent(user_query, recent)

        # Quick vector search (if Phase 5 implemented)
        quick_results = await self._quick_vector_search(user_query, top_k=5)

        fast_latency = (datetime.now() - start_fast).total_seconds() * 1000

        yield {
            'stage': 'fast',
            'status': 'fast',
            'results': {
                'recent_messages': recent,
                'quick_vector_results': quick_results,
                'context_hints': context_hints,
                'count': len(recent) + len(quick_results)
            },
            'latency_ms': fast_latency
        }

        # Stage 3: SMART - LLM reasoning + refined search (if needed)
        needs_reasoning = self.classifier.needs_reasoning(user_query, recent)

        if needs_reasoning:
            start_smart = datetime.now()

            # LLM reasoning
            reasoning = await self.reasoner.reason_about_query(
                user_query,
                recent,
                self.session.summary
            )

            # Refined multi-search based on reasoning
            refined_results = await self._refined_search(reasoning, user_query)

            # Merge and rerank
            final_results = self._merge_and_rerank(
                quick_results,
                refined_results,
                reasoning
            )

            smart_latency = (datetime.now() - start_smart).total_seconds() * 1000

            yield {
                'stage': 'smart',
                'status': 'complete',
                'results': {
                    'recent_messages': recent,
                    'vector_results': final_results,
                    'reasoning': reasoning,
                    'context_hints': context_hints,
                    'count': len(recent) + len(final_results)
                },
                'latency_ms': smart_latency,
                'reasoning_used': True
            }
        else:
            # Skip reasoning - quick results are good enough
            yield {
                'stage': 'smart',
                'status': 'complete',
                'results': {
                    'recent_messages': recent,
                    'vector_results': quick_results,
                    'context_hints': context_hints,
                    'count': len(recent) + len(quick_results)
                },
                'latency_ms': 0,  # No extra work
                'reasoning_used': False,
                'reasoning_skipped': 'Query was clear and specific'
            }

    async def _quick_vector_search(self, query: str, top_k: int = 5) -> List[Dict]:
        """
        Quick vector search (Phase 5 integration)

        Args:
            query: Search query
            top_k: Number of results

        Returns:
            Quick vector results
        """
        # TODO: Integrate with Phase 5 vector search when implemented
        # For now, return empty list

        # Placeholder: Would do actual vector search here
        # from .search import search
        # results = search(self.sessions_dir, query, top_k=top_k)

        return []

    async def _refined_search(
        self,
        reasoning: Dict,
        original_query: str
    ) -> List[Dict]:
        """
        Refined multi-search based on LLM reasoning

        Args:
            reasoning: Reasoning result from LLM
            original_query: Original user query

        Returns:
            Refined search results
        """
        all_results = []

        # Execute each search query from reasoning
        for search_query in reasoning.get('searches', [original_query]):
            # TODO: Integrate with Phase 5 vector search
            # results = await self._quick_vector_search(search_query, top_k=3)
            # all_results.extend(results)
            pass

        # Deduplicate
        seen = set()
        unique_results = []
        for result in all_results:
            result_id = result.get('id') or str(result)
            if result_id not in seen:
                seen.add(result_id)
                unique_results.append(result)

        return unique_results

    def _merge_and_rerank(
        self,
        quick_results: List[Dict],
        refined_results: List[Dict],
        reasoning: Dict
    ) -> List[Dict]:
        """
        Merge quick and refined results, then rerank

        Args:
            quick_results: Results from quick vector search
            refined_results: Results from refined search
            reasoning: LLM reasoning context

        Returns:
            Merged and reranked results
        """
        # Combine results
        all_results = quick_results + refined_results

        # Deduplicate
        seen = set()
        unique_results = []
        for result in all_results:
            result_id = result.get('id') or str(result)
            if result_id not in seen:
                seen.add(result_id)
                unique_results.append(result)

        # Rerank based on reasoning intent
        # TODO: Implement sophisticated reranking
        # For now, keep order from search

        return unique_results[:10]  # Top 10
