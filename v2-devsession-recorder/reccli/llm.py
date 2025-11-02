"""
LLM - Native LLM CLI interface with .devsession recording
Supports Claude (Anthropic) and GPT (OpenAI)
"""

import sys
import time
from pathlib import Path
from typing import Optional, List, Dict, Any

from .devsession import DevSession
from .config import Config


class LLMSession:
    """Native LLM interface with automatic .devsession recording"""

    def __init__(self, model: str, session_path: Path, api_key: Optional[str] = None):
        """
        Initialize LLM session

        Args:
            model: Model name ("claude", "gpt4", "gpt4o")
            session_path: Path to .devsession file
            api_key: API key (if None, loads from config)
        """
        self.model = model
        self.session_path = Path(session_path)
        self.session = DevSession()
        self.start_time = time.time()

        # Determine provider and initialize client
        if model.startswith('claude'):
            self.provider = 'anthropic'
            self.client = self._init_anthropic(api_key)
        elif model.startswith('gpt'):
            self.provider = 'openai'
            self.client = self._init_openai(api_key)
        else:
            raise ValueError(f"Unknown model: {model}. Use 'claude', 'gpt4', or 'gpt4o'")

        # Conversation history for context
        self.messages = []

    def _init_anthropic(self, api_key: Optional[str]):
        """Initialize Anthropic client"""
        try:
            import anthropic
        except ImportError:
            print("❌ anthropic package not installed. Run: pip install anthropic", file=sys.stderr)
            sys.exit(1)

        if not api_key:
            config = Config()
            api_key = config.get_api_key('anthropic')

        if not api_key:
            print("❌ Anthropic API key not found. Set it with:", file=sys.stderr)
            print("   reccli config --anthropic-key YOUR_KEY", file=sys.stderr)
            sys.exit(1)

        return anthropic.Anthropic(api_key=api_key)

    def _init_openai(self, api_key: Optional[str]):
        """Initialize OpenAI client"""
        try:
            from openai import OpenAI
        except ImportError:
            print("❌ openai package not installed. Run: pip install openai", file=sys.stderr)
            sys.exit(1)

        if not api_key:
            config = Config()
            api_key = config.get_api_key('openai')

        if not api_key:
            print("❌ OpenAI API key not found. Set it with:", file=sys.stderr)
            print("   reccli config --openai-key YOUR_KEY", file=sys.stderr)
            sys.exit(1)

        return OpenAI(api_key=api_key)

    def send_message(self, user_message: str) -> str:
        """
        Send message to LLM and get response

        Args:
            user_message: User's message

        Returns:
            Assistant's response
        """
        # Record user message
        timestamp = time.time() - self.start_time
        self.session.conversation.append({
            'role': 'user',
            'content': user_message,
            'timestamp': timestamp
        })
        self.messages.append({'role': 'user', 'content': user_message})

        # Call LLM
        if self.provider == 'anthropic':
            response = self._call_anthropic()
        else:
            response = self._call_openai()

        # Record assistant response
        timestamp = time.time() - self.start_time
        self.session.conversation.append({
            'role': 'assistant',
            'content': response,
            'timestamp': timestamp
        })
        self.messages.append({'role': 'assistant', 'content': response})

        # Auto-save
        self.session.save(self.session_path)

        return response

    def _call_anthropic(self) -> str:
        """Call Anthropic API"""
        # Map model names
        model_map = {
            'claude': 'claude-3-5-sonnet-20241022',
            'claude-sonnet': 'claude-3-5-sonnet-20241022',
            'claude-opus': 'claude-3-opus-20240229',
            'claude-haiku': 'claude-3-5-haiku-20241022'
        }
        model_id = model_map.get(self.model, self.model)

        try:
            response = self.client.messages.create(
                model=model_id,
                max_tokens=4096,
                messages=self.messages
            )
            return response.content[0].text
        except Exception as e:
            return f"❌ Error calling Claude: {e}"

    def _call_openai(self) -> str:
        """Call OpenAI API"""
        # Map model names
        model_map = {
            # GPT-5 (current)
            'gpt5': 'gpt-5',
            'gpt5-chat': 'gpt-5-chat-latest',
            'gpt5-mini': 'gpt-5-mini',
            'gpt5-nano': 'gpt-5-nano',
            'gpt5-codex': 'gpt-5-codex',
            # GPT-4 (legacy)
            'gpt4': 'gpt-4-turbo',
            'gpt4o': 'gpt-4o',
            'gpt4-turbo': 'gpt-4-turbo'
        }
        model_id = model_map.get(self.model, self.model)

        try:
            response = self.client.chat.completions.create(
                model=model_id,
                messages=self.messages
            )
            return response.choices[0].message.content
        except Exception as e:
            return f"❌ Error calling OpenAI: {e}"

    def chat_loop(self):
        """Interactive chat loop"""
        print(f"🤖 RecCli Chat - {self.model}")
        print(f"📝 Recording to: {self.session_path.name}")
        print("Type 'exit' or press Ctrl+D to quit\n")

        try:
            while True:
                # Get user input
                try:
                    user_input = input("You: ").strip()
                except EOFError:
                    print("\n")
                    break

                if not user_input:
                    continue

                if user_input.lower() in ['exit', 'quit', 'bye']:
                    break

                # Get response
                print(f"\n{self.model.title()}: ", end='', flush=True)
                response = self.send_message(user_input)
                print(response + "\n")

        except KeyboardInterrupt:
            print("\n\n")

        # Final save
        self._finalize()

    def one_shot(self, message: str) -> str:
        """
        Send single message and exit

        Args:
            message: User message

        Returns:
            Assistant response
        """
        response = self.send_message(message)
        self._finalize()
        return response

    def _finalize(self):
        """Finalize session"""
        duration = time.time() - self.start_time
        message_count = len(self.session.conversation)

        self.session.save(self.session_path)

        print(f"✅ Session saved")
        print(f"   File: {self.session_path}")
        print(f"   Messages: {message_count}")
        print(f"   Duration: {duration:.1f}s")


def chat_session(model: str, session_path: Path, api_key: Optional[str] = None):
    """
    Start interactive chat session

    Args:
        model: Model name
        session_path: Path to save .devsession
        api_key: Optional API key
    """
    session = LLMSession(model, session_path, api_key)
    session.chat_loop()


def one_shot_query(model: str, message: str, session_path: Path, api_key: Optional[str] = None) -> str:
    """
    Send single query and return response

    Args:
        model: Model name
        message: User message
        session_path: Path to save .devsession
        api_key: Optional API key

    Returns:
        Assistant response
    """
    session = LLMSession(model, session_path, api_key)
    return session.one_shot(message)
