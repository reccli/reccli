"""
Work Package Continuity (WPC) for RecCli Phase 6

Layer 1 of predictive stack: Predictive pre-fetching of likely-next artifacts.
Pre-generates artifacts while user reviews, making multi-file edits feel instant.
"""

from typing import List, Dict, Optional
from pathlib import Path
from datetime import datetime, timedelta
import re


class WorkPackageContinuity:
    """
    Predictive pre-fetching of likely-next artifacts

    Layer 1 of predictive stack: Pre-generate artifacts while
    user reviews, making multi-file edits feel instant.
    """

    def __init__(self, session, sessions_dir: Path):
        """
        Initialize WPC

        Args:
            session: DevSession object
            sessions_dir: Path to sessions directory
        """
        self.session = session
        self.sessions_dir = sessions_dir
        self.prefetch_queue = []  # Max size 3-5
        self.max_queue_size = 5
        self.max_budget = 900  # tokens
        self.prediction_accuracy = []  # Track accuracy
        self.cooldown = 0  # Adaptive backoff (seconds)
        self.last_prefetch = None

        # Heuristic weights
        self.weights = {
            'recent_files': 1.0,
            'next_steps': 1.2,
            'failing_test': 1.5,
            'linked_docs': 0.8,
            'error_logs': 1.3
        }

    def predict_next(self, signal: Dict) -> List[str]:
        """
        Heuristic predictor for likely-next artifacts

        Args:
            signal: Recent events/context
                - recent_messages: Last 50-100 messages
                - section: Active section/episode
                - next_steps: From summary
                - cursor_file: Currently focused file
                - summary: Session summary

        Returns:
            List of artifact IDs to prefetch
        """
        predictions = []

        # Heuristic 1: Files touched in last 10 min + neighbors
        recent_files = self._extract_recent_files(signal.get('recent_messages', []))
        for file in recent_files:
            neighbors = self._get_file_neighbors(file)
            for neighbor in neighbors[:2]:  # Top 2 neighbors
                predictions.append({
                    'id': f"file:{neighbor}",
                    'type': 'file_neighbor',
                    'weight': self.weights['recent_files'],
                    'reason': f"Neighbor of recently touched {file}"
                })

        # Heuristic 2: Files in next_steps
        next_steps = signal.get('next_steps', [])
        for step in next_steps:
            files = self._extract_files_from_text(step)
            for file in files:
                predictions.append({
                    'id': f"file:{file}",
                    'type': 'next_step',
                    'weight': self.weights['next_steps'],
                    'reason': f"Mentioned in next step: {step[:50]}"
                })

        # Heuristic 3: Recent failing test → source under test
        failing_tests = self._find_failing_tests(signal.get('recent_messages', []))
        for test in failing_tests:
            source = self._infer_source_from_test(test)
            if source:
                predictions.append({
                    'id': f"file:{source}",
                    'type': 'test_source',
                    'weight': self.weights['failing_test'],
                    'reason': f"Source for failing test: {test}"
                })

        # Heuristic 4: Docs/specs linked recently
        linked_docs = self._extract_linked_docs(signal.get('recent_messages', []))
        for doc in linked_docs:
            predictions.append({
                'id': f"doc:{doc}",
                'type': 'linked_doc',
                'weight': self.weights['linked_docs'],
                'reason': f"Recently linked documentation: {doc}"
            })

        # Heuristic 5: Logs around last error
        last_error = self._find_last_error(signal.get('recent_messages', []))
        if last_error:
            error_context = self._get_error_context_ids(last_error, window_min=5)
            for ctx_id in error_context:
                predictions.append({
                    'id': ctx_id,
                    'type': 'error_context',
                    'weight': self.weights['error_logs'],
                    'reason': f"Context around error: {last_error[:50]}"
                })

        # Score and deduplicate
        scored = self._score_predictions(predictions)

        return [p['id'] for p in scored[:self.max_queue_size]]

    def prefetch(self, items: List[str], budget: int = 900):
        """
        Pre-retrieve artifacts and stage in queue

        Args:
            items: Artifact IDs to prefetch
            budget: Token budget for prefetch
        """
        from .search import expand_result

        staged = []
        tokens_used = 0

        for item_id in items:
            try:
                # Expand using Phase 5 (if it's a message/span ID)
                if item_id.startswith('file:') or item_id.startswith('doc:'):
                    # File/doc prediction - would need file reading logic
                    # For now, skip these
                    continue

                expanded = expand_result(
                    self.sessions_dir,
                    item_id,
                    context_window=5
                )

                if expanded:
                    item_tokens = self._count_tokens(str(expanded))

                    if tokens_used + item_tokens <= budget:
                        staged.append({
                            'id': item_id,
                            'content': expanded,
                            'tokens': item_tokens,
                            'timestamp': datetime.now(),
                            'used': False
                        })
                        tokens_used += item_tokens
            except Exception as e:
                # Skip items that fail to expand
                continue

        # Add to queue with LRU eviction
        self._add_to_queue(staged)
        self.last_prefetch = datetime.now()

    def get_staged_context(self, budget: int = 900) -> List[Dict]:
        """
        Get pre-fetched items within budget

        Args:
            budget: Token budget

        Returns:
            List of staged items
        """
        result = []
        tokens = 0

        for item in self.prefetch_queue:
            if tokens + item['tokens'] <= budget:
                result.append(item)
                tokens += item['tokens']

        return result

    def mark_prediction_used(self, item_id: str, used: bool):
        """
        Track prediction accuracy for adaptive learning

        Args:
            item_id: Prediction ID
            used: Whether it was actually used
        """
        self.prediction_accuracy.append({
            'id': item_id,
            'used': used,
            'timestamp': datetime.now()
        })

        # Mark in queue
        for item in self.prefetch_queue:
            if item['id'] == item_id:
                item['used'] = used

        # Adaptive cooldown: if last 3 unused, back off
        recent = self.prediction_accuracy[-3:]
        if len(recent) == 3 and not any(r['used'] for r in recent):
            self.cooldown = max(self.cooldown, 600)  # 10 min cooldown
            print("⚠️  WPC: 3 predictions unused, backing off for 10 min")

    def should_prefetch(self) -> bool:
        """Check if we should run prefetch now"""
        # Check cooldown
        if self.cooldown > 0:
            if self.last_prefetch:
                elapsed = (datetime.now() - self.last_prefetch).total_seconds()
                if elapsed < self.cooldown:
                    return False
                else:
                    # Cooldown expired
                    self.cooldown = 0

        return True

    def _add_to_queue(self, items: List[Dict]):
        """Add items to queue with LRU eviction"""
        for item in items:
            # Check if already in queue
            existing = [i for i in self.prefetch_queue if i['id'] == item['id']]
            if existing:
                # Update timestamp (refresh)
                existing[0]['timestamp'] = datetime.now()
                continue

            # Add to queue
            self.prefetch_queue.append(item)

        # Evict LRU if over max size
        if len(self.prefetch_queue) > self.max_queue_size:
            # Sort by timestamp (oldest first)
            self.prefetch_queue.sort(key=lambda x: x['timestamp'])

            # Remove oldest
            self.prefetch_queue = self.prefetch_queue[-self.max_queue_size:]

    def _extract_recent_files(self, messages: List[Dict]) -> List[str]:
        """Extract file paths mentioned in recent messages"""
        files = []
        file_pattern = r'[a-zA-Z0-9_\-/]+\.(py|js|ts|go|java|cpp|c|h|md|txt|json|yaml|yml)'

        for msg in messages[-20:]:  # Last 20 messages
            content = msg.get('content', '')
            matches = re.findall(file_pattern, content)
            files.extend([m[0] for m in matches if isinstance(m, tuple)])

        # Deduplicate
        return list(set(files))[:5]

    def _get_file_neighbors(self, file: str) -> List[str]:
        """Get neighboring files (same directory, test files, etc.)"""
        neighbors = []
        path = Path(file)

        # Test file for source file
        if not path.stem.endswith('_test') and not path.stem.startswith('test_'):
            test_file = f"test_{path.stem}{path.suffix}"
            neighbors.append(test_file)

            # Alternative test patterns
            neighbors.append(f"{path.stem}_test{path.suffix}")
            neighbors.append(f"{path.stem}.test{path.suffix}")

        # Source file for test file
        elif path.stem.endswith('_test'):
            source = path.stem[:-5] + path.suffix
            neighbors.append(source)
        elif path.stem.startswith('test_'):
            source = path.stem[5:] + path.suffix
            neighbors.append(source)

        # Helper/util files
        if not path.stem.endswith(('_helper', '_util', '_helpers', '_utils')):
            neighbors.append(f"{path.stem}_helper{path.suffix}")
            neighbors.append(f"{path.stem}_helpers{path.suffix}")

        return neighbors[:3]

    def _extract_files_from_text(self, text: str) -> List[str]:
        """Extract file paths from text"""
        file_pattern = r'[a-zA-Z0-9_\-/]+\.(py|js|ts|go|java|cpp|c|h|md|txt|json|yaml|yml)'
        matches = re.findall(file_pattern, text)
        return [m[0] if isinstance(m, tuple) else m for m in matches]

    def _find_failing_tests(self, messages: List[Dict]) -> List[str]:
        """Find failing tests mentioned in recent messages"""
        failing = []
        test_patterns = [
            r'test[_\s]+(\w+)',
            r'failing[:\s]+(\S+)',
            r'error in (\S+\.test\.\w+)',
            r'FAILED (\S+)'
        ]

        for msg in messages[-10:]:
            content = msg.get('content', '')

            for pattern in test_patterns:
                matches = re.findall(pattern, content, re.I)
                failing.extend(matches)

        return list(set(failing))[:3]

    def _infer_source_from_test(self, test: str) -> Optional[str]:
        """Infer source file from test name"""
        # Remove test_ prefix or _test suffix
        if test.startswith('test_'):
            source = test[5:]
        elif test.endswith('_test'):
            source = test[:-5]
        elif '.test.' in test:
            source = test.replace('.test.', '.')
        else:
            return None

        # Assume common extensions
        for ext in ['.py', '.js', '.ts', '.go']:
            if not source.endswith(ext):
                return f"{source}{ext}"

        return source

    def _extract_linked_docs(self, messages: List[Dict]) -> List[str]:
        """Extract documentation files linked in recent messages"""
        docs = []
        doc_pattern = r'([a-zA-Z0-9_\-/]+\.(md|txt|pdf|doc))'

        for msg in messages[-10:]:
            content = msg.get('content', '')
            matches = re.findall(doc_pattern, content, re.I)
            docs.extend([m[0] if isinstance(m, tuple) else m for m in matches])

        return list(set(docs))[:3]

    def _find_last_error(self, messages: List[Dict]) -> Optional[str]:
        """Find last error message in recent messages"""
        error_patterns = [
            r'error:?\s+(.{20,200})',
            r'exception:?\s+(.{20,200})',
            r'failed:?\s+(.{20,200})',
            r'traceback.*?(\w+Error:.*)',
        ]

        for msg in reversed(messages[-10:]):
            content = msg.get('content', '')

            for pattern in error_patterns:
                matches = re.findall(pattern, content, re.I | re.S)
                if matches:
                    return matches[0][:100]

        return None

    def _get_error_context_ids(self, error: str, window_min: int = 5) -> List[str]:
        """Get message IDs around error (±window_min minutes)"""
        # This would require timestamp-based search
        # For now, return empty list
        return []

    def _score_predictions(self, predictions: List[Dict]) -> List[Dict]:
        """Score and deduplicate predictions"""
        # Group by ID
        by_id = {}
        for pred in predictions:
            pred_id = pred['id']
            if pred_id not in by_id:
                by_id[pred_id] = pred
            else:
                # Combine weights if duplicate
                by_id[pred_id]['weight'] += pred['weight']
                by_id[pred_id]['reason'] += f"; {pred['reason']}"

        # Convert back to list and sort by weight
        scored = list(by_id.values())
        scored.sort(key=lambda x: x['weight'], reverse=True)

        return scored

    def _count_tokens(self, text: str) -> int:
        """Estimate token count"""
        if not text:
            return 0
        words = len(str(text).split())
        return int(words * 1.3)
