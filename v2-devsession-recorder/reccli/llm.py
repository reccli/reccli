"""
LLM - Native LLM CLI interface with .devsession recording
Supports Claude (Anthropic) and GPT (OpenAI)
"""

import sys
import time
import re
import threading
import itertools
from pathlib import Path
from typing import Optional, List, Dict, Any

from prompt_toolkit import prompt, PromptSession
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.shortcuts import radiolist_dialog
from prompt_toolkit.keys import Keys
from prompt_toolkit.application import run_in_terminal
from prompt_toolkit.patch_stdout import patch_stdout

from .devsession import DevSession
from .config import Config


class LLMSession:
    """Native LLM interface with automatic .devsession recording"""

    # Thinking synonyms for loading animation
    THINKING_STATES = [
        "pondering", "reasoning", "analyzing", "contemplating", "processing",
        "thinking", "considering", "deliberating", "reflecting", "cogitating"
    ]

    @staticmethod
    def _smart_wrap_text(text: str, width: int = 80) -> str:
        """
        Intelligently wrap text like Claude Code:
        - Preserve code blocks (triple backticks)
        - Break on word boundaries for prose
        - Allow breaking technical strings (URLs, paths, IDs with numbers/special chars)
        - Never break regular words mid-word
        """
        import shutil
        # Get actual terminal width
        term_width = shutil.get_terminal_size().columns
        # Leave some margin (like Claude Code does)
        width = term_width - 4

        lines = []
        in_code_block = False

        for line in text.split('\n'):
            # Detect code block boundaries
            if line.strip().startswith('```'):
                in_code_block = not in_code_block
                lines.append(line)
                continue

            # Don't wrap code blocks
            if in_code_block:
                lines.append(line)
                continue

            # Don't wrap if line already fits
            if len(line) <= width:
                lines.append(line)
                continue

            # Smart wrap long lines
            wrapped = []
            current = ""

            words = line.split(' ')
            for word in words:
                # Check if adding this word would exceed width
                test_line = current + (' ' if current else '') + word

                if len(test_line) <= width:
                    current = test_line
                else:
                    # If current line has content, save it
                    if current:
                        wrapped.append(current)
                        current = word
                    else:
                        # Word itself is longer than width
                        # Check if it's a technical string (has numbers, slashes, etc)
                        is_technical = bool(re.search(r'[0-9/\-_.:@]', word))

                        if is_technical:
                            # Break technical strings at natural boundaries
                            # Split on slashes, dots, but keep delimiter
                            parts = re.split(r'([/.\-_:])', word)
                            chunk = ""
                            for part in parts:
                                if len(chunk + part) <= width:
                                    chunk += part
                                else:
                                    if chunk:
                                        wrapped.append(chunk)
                                    chunk = part
                            current = chunk
                        else:
                            # Regular long word - just add it (don't break)
                            wrapped.append(word)
                            current = ""

            # Add remaining content
            if current:
                wrapped.append(current)

            lines.extend(wrapped)

        return '\n'.join(lines)

    # Available models with descriptions
    AVAILABLE_MODELS = [
        ('claude-sonnet', 'Claude Sonnet 4.5 (Fast, capable - recommended)'),
        ('claude-haiku', 'Claude Haiku 4.5 (Fastest, cheapest)'),
        ('claude-opus', 'Claude Opus 4.1 (Most capable, expensive)'),
        ('gpt5', 'GPT-5 (OpenAI, most capable)'),
        ('gpt5-mini', 'GPT-5 Mini (OpenAI, balanced)'),
        ('gpt5-nano', 'GPT-5 Nano (OpenAI, fastest/cheapest)'),
        ('gpt4o', 'GPT-4o (OpenAI, legacy but capable)'),
    ]

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

        # Paste state tracking
        self.paste_content = None
        self.paste_placeholder = None

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

    def _select_model_interactive(self) -> Optional[str]:
        """Show interactive model selection menu"""
        result = radiolist_dialog(
            title="Select Model",
            text="Use arrow keys to navigate, Space/Enter to select:",
            values=self.AVAILABLE_MODELS
        ).run()
        return result

    def _thinking_spinner(self, stop_event: threading.Event):
        """Animated thinking spinner with synonym rotation"""
        # Start on a new line to avoid overwriting user input
        print()

        spinner_chars = itertools.cycle(['⠋', '⠙', '⠹', '⠸', '⠼', '⠴', '⠦', '⠧', '⠇', '⠏'])
        thinking_words = itertools.cycle(self.THINKING_STATES)
        current_word = next(thinking_words)
        word_timer = time.time()

        while not stop_event.is_set():
            # Rotate thinking word every 0.8 seconds
            if time.time() - word_timer > 0.8:
                current_word = next(thinking_words)
                word_timer = time.time()

            # Update spinner on its own line
            sys.stdout.write(f'\r{self.model.title()}: {next(spinner_chars)} {current_word}...')
            sys.stdout.flush()
            time.sleep(0.08)

        # Clear the spinner line completely
        sys.stdout.write('\r' + ' ' * 80 + '\r')
        sys.stdout.flush()

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
        # Map model names to current Claude models
        model_map = {
            # Primary models (4.5 and 4.1)
            'claude': 'claude-sonnet-4-5-20250929',           # Default: Sonnet 4.5
            'claude-sonnet': 'claude-sonnet-4-5-20250929',   # Sonnet 4.5 (fast, capable)
            'claude-haiku': 'claude-haiku-4-5-20251001',     # Haiku 4.5 (fastest, cheapest)
            'claude-opus': 'claude-opus-4-1-20250805',       # Opus 4.1 (most capable, rare use)
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

    def chat_loop(self, enable_compaction: bool = True):
        """
        Interactive chat loop with optional preemptive compaction

        Args:
            enable_compaction: Enable automatic compaction at 190K tokens
        """
        # Welcome header with spacing
        print("\n" + "═" * 60)
        print(f"  \033[91m●\033[0m RecCli Chat - {self.model}")
        print(f"  Session: {self.session_path.name}")

        # Initialize compactor if enabled
        compactor = None
        if enable_compaction:
            try:
                from .preemptive_compaction import PreemptiveCompactor
                sessions_dir = self.session_path.parent
                compactor = PreemptiveCompactor(
                    self.session,
                    sessions_dir,
                    llm_client=self.client,
                    model=self.model
                )
                print(f"  Context: Auto-compact at 190K tokens")
            except Exception as e:
                print(f"  Warning: Compaction disabled - {e}")

        print("═" * 60)
        print("  Type '/model' to switch models, 'exit' to quit")
        print("=" * 60 + "\n")

        # Setup key bindings for smart Enter behavior
        from prompt_toolkit.keys import Keys
        from prompt_toolkit.key_binding import KeyBindings

        kb = KeyBindings()

        @kb.add(Keys.Enter, eager=True)
        def _(event):
            """Smart Enter: submit if single line or if it looks like a paste"""
            buffer = event.current_buffer
            text = buffer.text

            # Count actual newlines in buffer
            line_count = text.count('\n')

            # If single line OR looks like a paste (many lines), submit
            # Otherwise, insert newline for manual multi-line input
            if line_count == 0 or line_count > 3:
                buffer.validate_and_handle()
            else:
                buffer.insert_text('\n')

        # Create prompt session with multiline for preserving pastes
        session = PromptSession(multiline=True, key_bindings=kb)

        try:
            with patch_stdout(raw=True):
                while True:
                    # Check for compaction BEFORE getting user input
                    if compactor:
                        compacted_context = compactor.check_and_compact()
                        if compacted_context:
                            # TODO: In future, inject compacted_context into LLM
                            # For now, just continue - full session is saved
                            pass

                    # Get user input using PromptSession
                    try:
                        user_input = session.prompt("> ").strip()

                        # Immediately check if it was a paste and clear if needed
                        if len(user_input) > 400 or user_input.count('\n') > 5:
                            # Move up and clear the pasted lines from display
                            lines_to_clear = min(20, user_input.count('\n') + 2)
                            for _ in range(lines_to_clear):
                                print("\033[A\033[2K", end='')
                            sys.stdout.flush()
                    except (EOFError, KeyboardInterrupt):
                        print("\n")
                        break

                    if not user_input:
                        continue

                    # Detect if this is a long paste (>400 chars or multiple lines)
                    char_count = len(user_input)
                    line_count = user_input.count('\n') + 1

                    # If it's a long paste, show ONLY annotation (like Claude Code)
                    if char_count > 400 or line_count > 5:
                        # Show paste annotation with gray background (no content)
                        print(f"\033[48;5;240m> [pasted +{line_count} lines, {char_count:,} chars]\033[0m")
                        print("─" * 60)
                    else:
                        # Normal short input - echo it with gray background
                        print(f"\033[48;5;240m> {user_input}\033[0m")
                        print("─" * 60)

                    if user_input.lower() in ['exit', 'quit', 'bye']:
                        break

                    # Handle /model command for interactive model switching
                    if user_input.lower() == '/model':
                        new_model = self._select_model_interactive()
                        if new_model:
                            # Reinitialize with new model
                            old_model = self.model
                            self.model = new_model
                            if new_model.startswith('claude'):
                                self.provider = 'anthropic'
                                try:
                                    self.client = self._init_anthropic(None)
                                    print(f"✓ Switched from {old_model} to {new_model}\n")
                                except Exception as e:
                                    print(f"✗ Failed to switch to {new_model}: {e}")
                                    self.model = old_model
                            elif new_model.startswith('gpt'):
                                self.provider = 'openai'
                                try:
                                    self.client = self._init_openai(None)
                                    print(f"✓ Switched from {old_model} to {new_model}\n")
                                except Exception as e:
                                    print(f"✗ Failed to switch to {new_model}: {e}")
                                    self.model = old_model
                        continue

                    # Get response with animated thinking spinner
                    stop_spinner = threading.Event()
                    spinner_thread = threading.Thread(target=self._thinking_spinner, args=(stop_spinner,))
                    spinner_thread.start()

                    response = self.send_message(user_input)

                    stop_spinner.set()
                    spinner_thread.join()

                    # Print response (let terminal handle wrapping naturally)
                    print(f"{response}\n")
                    print("─" * 60 + "\n")

        except KeyboardInterrupt:
            print("\n\n")

        # Final save
        self._finalize(compactor)

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

    def _finalize(self, compactor=None):
        """
        Finalize session

        Args:
            compactor: Optional PreemptiveCompactor instance
        """
        duration = time.time() - self.start_time
        message_count = len(self.session.conversation)

        self.session.save(self.session_path)

        # Session summary with spacing
        print("\n" + "═" * 60)
        print("  Session Complete")
        print("═" * 60)
        print(f"  File: {self.session_path}")
        print(f"  Messages: {message_count}")
        print(f"  Duration: {duration:.1f}s")

        # Show compaction status if compactor was used
        if compactor:
            status = compactor.get_status()
            if status['compaction_count'] > 0:
                print(f"  Compactions: {status['compaction_count']}")
            print(f"  Tokens: {status['current_tokens']:,} ({status['percentage']:.1f}% of limit)")

        print("═" * 60 + "\n")


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
