"""
Post-Answer Reasoning for RecCli Phase 6

Layer 2 of predictive stack: Predict next query after answering.
Uses 100 tokens to reason about what user will ask next, then pre-fetches
that context for instant follow-up.
"""

from typing import List, Dict, Optional
import re


class PostAnswerReasoning:
    """
    Layer 2 of predictive stack: Predict next query after answering

    Uses 100 tokens to reason about what user will ask next,
    then pre-fetches that context for instant follow-up.
    """

    def __init__(self, llm_client=None):
        """
        Initialize Post-Answer Reasoning

        Args:
            llm_client: Optional LLM client for predictions
        """
        self.llm_client = llm_client
        self.reasoning_budget = 100
        self.prediction_history = []

    async def predict_next_query(
        self,
        conversation_history: List[Dict],
        last_answer: str
    ) -> Dict:
        """
        After answering, predict what user will ask next

        Args:
            conversation_history: Recent conversation
            last_answer: The answer we just gave

        Returns:
            Prediction dict with likely next query and artifacts
        """
        # If no LLM client, use heuristic fallback
        if not self.llm_client:
            return self._heuristic_prediction(
                conversation_history,
                last_answer
            )

        # Build reasoning prompt
        prompt = self._build_reasoning_prompt(
            conversation_history,
            last_answer
        )

        try:
            # Use 100 tokens for prediction
            prediction = await self.llm_client.complete(
                prompt,
                max_tokens=self.reasoning_budget,
                temperature=0.3  # Lower for more focused prediction
            )

            # Parse prediction
            parsed = self._parse_prediction(prediction)

            # Track prediction
            self.prediction_history.append({
                'answer': last_answer[:100],
                'prediction': parsed,
                'timestamp': None  # Would need datetime
            })

            return parsed

        except Exception as e:
            # Fallback to heuristic
            return self._heuristic_prediction(
                conversation_history,
                last_answer
            )

    def _build_reasoning_prompt(
        self,
        conversation: List[Dict],
        answer: str
    ) -> str:
        """Build compact reasoning prompt"""
        # Get last few messages for context
        recent = conversation[-3:] if conversation else []

        context_str = ""
        for msg in recent:
            role = msg.get('role', 'unknown')
            content = msg.get('content', '')[:100]
            context_str += f"{role}: {content}...\n"

        return f"""
Recent conversation:
{context_str}

You just answered: "{answer[:200]}..."

Based on this conversation flow, what will the user likely ask next?

Reasoning (max 100 tokens):
- Most likely next question: [specific question]
- Artifacts needed: [list specific files/context]
- Confidence: [high/medium/low]

Keep response under 100 tokens.
"""

    def _parse_prediction(self, prediction: str) -> Dict:
        """Extract structured prediction from LLM response"""
        lines = prediction.strip().split('\n')

        result = {
            'query': '',
            'artifacts': [],
            'confidence': 'medium'
        }

        for line in lines:
            line_lower = line.lower()

            if 'next question:' in line_lower or 'likely ask:' in line_lower:
                parts = line.split(':', 1)
                if len(parts) > 1:
                    result['query'] = parts[1].strip()

            elif 'artifacts needed:' in line_lower or 'artifacts:' in line_lower:
                parts = line.split(':', 1)
                if len(parts) > 1:
                    artifacts_str = parts[1].strip()
                    # Split by comma or semicolon
                    result['artifacts'] = [
                        a.strip() for a in re.split(r'[,;]', artifacts_str)
                        if a.strip()
                    ]

            elif 'confidence:' in line_lower:
                parts = line.split(':', 1)
                if len(parts) > 1:
                    conf = parts[1].strip().lower()
                    if conf in ['high', 'medium', 'low']:
                        result['confidence'] = conf

        return result

    def _heuristic_prediction(
        self,
        conversation_history: List[Dict],
        last_answer: str
    ) -> Dict:
        """
        Heuristic-based prediction (no LLM)

        Analyzes conversation patterns to predict next query
        """
        result = {
            'query': '',
            'artifacts': [],
            'confidence': 'low'
        }

        # Analyze last answer for clues
        answer_lower = last_answer.lower()

        # Pattern 1: If answer mentions "next step", predict follow-up
        if 'next step' in answer_lower or 'next, ' in answer_lower:
            result['query'] = 'What should I do next?'
            result['confidence'] = 'medium'

        # Pattern 2: If answer explains concept, predict implementation question
        elif any(word in answer_lower for word in ['how', 'works', 'architecture', 'design']):
            result['query'] = 'How do I implement this?'
            result['artifacts'] = self._extract_files_from_text(last_answer)
            result['confidence'] = 'medium'

        # Pattern 3: If answer shows code, predict test question
        elif '```' in last_answer or 'def ' in last_answer or 'function' in last_answer:
            result['query'] = 'How should I test this?'
            result['artifacts'] = self._extract_files_from_text(last_answer)
            result['confidence'] = 'medium'

        # Pattern 4: If answer fixes error, predict "what else?"
        elif any(word in answer_lower for word in ['error', 'fix', 'bug', 'issue']):
            result['query'] = 'Are there similar issues elsewhere?'
            result['confidence'] = 'low'

        # Pattern 5: If answer about auth, predict session question
        elif 'auth' in answer_lower or 'authentication' in answer_lower:
            result['query'] = 'How does session management work?'
            result['artifacts'] = ['session.py', 'auth.py', 'middleware.py']
            result['confidence'] = 'medium'

        # Pattern 6: If answer about database, predict migration question
        elif 'database' in answer_lower or 'schema' in answer_lower:
            result['query'] = 'How do I run migrations?'
            result['artifacts'] = ['migrations/', 'schema.sql']
            result['confidence'] = 'medium'

        return result

    def _extract_files_from_text(self, text: str) -> List[str]:
        """Extract file paths from text"""
        file_pattern = r'([a-zA-Z0-9_\-/]+\.(py|js|ts|go|java|cpp|c|h|md|txt|json|yaml|yml))'
        matches = re.findall(file_pattern, text, re.I)

        files = []
        for match in matches:
            if isinstance(match, tuple):
                files.append(match[0])
            else:
                files.append(match)

        return list(set(files))[:5]

    def get_prediction_accuracy(self) -> Dict:
        """Calculate prediction accuracy metrics"""
        if not self.prediction_history:
            return {
                'total': 0,
                'high_confidence': 0,
                'medium_confidence': 0,
                'low_confidence': 0
            }

        total = len(self.prediction_history)
        by_confidence = {
            'high': 0,
            'medium': 0,
            'low': 0
        }

        for pred in self.prediction_history:
            conf = pred['prediction'].get('confidence', 'low')
            by_confidence[conf] = by_confidence.get(conf, 0) + 1

        return {
            'total': total,
            'high_confidence': by_confidence['high'],
            'medium_confidence': by_confidence['medium'],
            'low_confidence': by_confidence['low']
        }
