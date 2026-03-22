"""
LLM - Native LLM CLI interface with .devsession recording
Supports Claude (Anthropic) and GPT (OpenAI)
"""

import sys
import time
import re
import threading
import itertools
import os
import glob
from pathlib import Path
from typing import Optional, List, Dict, Any

from prompt_toolkit import prompt, PromptSession
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.shortcuts import radiolist_dialog
from prompt_toolkit.keys import Keys
from prompt_toolkit.application import run_in_terminal
from prompt_toolkit.patch_stdout import patch_stdout

from ..session.devsession import DevSession
from .config import Config
from ..project.devproject import initialize_session_project_metadata


# Phase 8: Retrieval System Prompt
RETRIEVAL_SYSTEM_PROMPT = """
# Context Retrieval System

You have access to conversation history through retrieval tools:

## Available Context Layers

**Layer 1: Project Overview** (if relevant) - Macro context about the project
**Layer 2: Session Summary** - What happened in this session
**Layer 3: Recent Messages** - Current conversation context
**Layer 4: Vector Search Results** - Relevant historical context

## Retrieval Tools

### retrieve_context
Fetch specific message ranges from the conversation:
```
retrieve_context({"ranges": [{"start": "msg_042", "end": "msg_050", "reason": "need full decision details"}]})
```

### search_history
Search semantically across the session:
```
search_history({"query": "authentication bug fix", "max_results": 5})
```

## Strategy
1. Check summary first for high-level overview
2. Use search_history for broad queries across session
3. Use retrieve_context for specific details when summary references message ranges
4. Be token-conscious: small(~3K), medium(~7K), large(~20K) tokens per range
"""


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
        initialize_session_project_metadata(self.session, Path.cwd())
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

        # Initialize memory middleware for .devproject + tree context
        self._project_context = self._load_project_context()

    def _load_project_context(self) -> str:
        """Load .devproject features + file tree as formatted context for the system message."""
        try:
            from ..retrieval.memory_middleware import MemoryMiddleware
            middleware = MemoryMiddleware(self.session, self.session_path.parent)
            overview = middleware._load_project_overview()
            if not overview:
                return ""
            return middleware._format_project_context(overview)
        except Exception:
            return ""

    def _build_system_message(self) -> str:
        """Build the full system message with project context and retrieval instructions."""
        base = "You are a helpful AI assistant with access to conversation history through retrieval tools. Use them when needed to provide accurate, detailed answers."

        parts = []
        if self._project_context:
            parts.append(self._project_context)

        if len(self.session.conversation) > 10:
            parts.append(RETRIEVAL_SYSTEM_PROMPT)

        parts.append(base)
        return "\n\n".join(parts)

    def refresh_project_context(self):
        """Reload .devproject context, e.g. after compaction."""
        self._project_context = self._load_project_context()

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

    def send_message_streaming(self, user_message: str, on_event):
        """
        Send message to LLM and stream events

        Args:
            user_message: User's message
            on_event: Callback function(event_type, data)
        """
        # Record user message
        timestamp = time.time() - self.start_time
        self.session.conversation.append({
            'role': 'user',
            'content': user_message,
            'timestamp': timestamp
        })
        self.messages.append({'role': 'user', 'content': user_message})

        # Call LLM with streaming
        if self.provider == 'anthropic':
            response = self._call_anthropic_streaming(on_event)
        else:
            response = self._call_openai_streaming(on_event)

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

    def _execute_tool(self, tool_name: str, tool_input: Dict[str, Any]) -> str:
        """Execute a tool and return the result as a string"""
        try:
            if tool_name == "read_file":
                path = Path(tool_input["path"])
                if not path.exists():
                    return f"Error: File not found: {path}"
                return path.read_text()

            elif tool_name == "write_file":
                path = Path(tool_input["path"])
                content = tool_input["content"]
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(content)
                return f"Successfully wrote {len(content)} characters to {path}"

            elif tool_name == "list_directory":
                path = Path(tool_input.get("path", "."))
                if not path.exists():
                    return f"Error: Directory not found: {path}"
                if not path.is_dir():
                    return f"Error: Not a directory: {path}"

                items = []
                for item in sorted(path.iterdir()):
                    if item.is_dir():
                        items.append(f"📁 {item.name}/")
                    else:
                        size = item.stat().st_size
                        items.append(f"📄 {item.name} ({size} bytes)")
                return "\n".join(items)

            elif tool_name == "glob_files":
                pattern = tool_input["pattern"]
                base_path = Path(tool_input.get("path", "."))
                matches = list(base_path.glob(pattern))
                if not matches:
                    return f"No files found matching pattern: {pattern}"
                return "\n".join(str(p) for p in sorted(matches))

            elif tool_name == "retrieve_context":
                # Phase 8: Retrieval tool
                result = self._execute_retrieve_context(tool_input)
                return f"Retrieved {result['retrieved_ranges']} context ranges with {result['total_messages']} messages:\n\n" + \
                       "\n\n".join([ctx['text'] for ctx in result['contexts']])

            elif tool_name == "search_history":
                # Phase 8: Search tool
                result = self._execute_search_history(tool_input)
                if result.get('error'):
                    return result['error']

                output = [f"Found {result['results_found']} results:\n"]
                for i, res in enumerate(result['results'], 1):
                    msg_range = res.get('message_range', {})
                    output.append(f"{i}. [{res['category']}] {res['summary']}")
                    output.append(f"   Range: {msg_range.get('start', '?')} to {msg_range.get('end', '?')}")
                    output.append(f"   Relevance: {res['relevance_score']:.2f}")
                    output.append(f"   Preview: {res['preview']}...\n")

                return "\n".join(output)

            else:
                return f"Error: Unknown tool: {tool_name}"

        except Exception as e:
            return f"Error executing {tool_name}: {str(e)}"

    # Phase 8: Retrieval tool helpers
    def _msg_id_to_index(self, msg_id: str) -> int:
        """Convert message ID to 0-based index (msg_042 -> 41)"""
        try:
            msg_num = int(msg_id.split("_")[1])
            return msg_num - 1
        except (IndexError, ValueError):
            return 0

    def _execute_retrieve_context(self, tool_input: Dict[str, Any]) -> Dict:
        """
        Execute context retrieval tool

        Args:
            tool_input: {
                "ranges": [{"start": "msg_042", "end": "msg_050", "reason": "..."}],
                "expand_context": 5
            }

        Returns:
            Retrieved contexts formatted for LLM
        """
        from ..retrieval.retrieval import ContextRetriever

        retriever = ContextRetriever(self.session)
        results = []

        for range_spec in tool_input.get("ranges", []):
            # Build mock summary item with message_range
            summary_item = {
                "message_range": {
                    "start": range_spec["start"],
                    "end": range_spec["end"],
                    "start_index": self._msg_id_to_index(range_spec["start"]),
                    "end_index": self._msg_id_to_index(range_spec["end"])
                }
            }

            expand = tool_input.get("expand_context", 5)
            context = retriever.retrieve_full_context(summary_item, expand_context=expand)

            # Format for LLM
            formatted = self._format_retrieved_context(context, range_spec.get("reason"))
            results.append(formatted)

        return {
            "retrieved_ranges": len(results),
            "total_messages": sum(r.get("message_count", 0) for r in results),
            "contexts": results
        }

    def _execute_search_history(self, tool_input: Dict[str, Any]) -> Dict:
        """
        Execute history search tool

        Args:
            tool_input: {
                "query": "authentication bug",
                "max_results": 5,
                "category": "problems_solved"
            }

        Returns:
            Search results with message_range links
        """
        from ..retrieval.search import search

        # Get sessions_dir from session path
        sessions_dir = self.session_path.parent if hasattr(self, 'session_path') else Path.cwd()

        try:
            results = search(
                sessions_dir=sessions_dir,
                query=tool_input["query"],
                top_k=tool_input.get("max_results", 5),
                scope={'session': self.session.session_id}
            )

            # Format for LLM
            formatted_results = []
            for result in results[:tool_input.get("max_results", 5)]:
                formatted_results.append({
                    "category": result.get("category", "unknown"),
                    "summary": result.get("description", result.get("decision", result.get("problem", ""))),
                    "message_range": result.get("message_range"),
                    "relevance_score": result.get("score", 0.0),
                    "preview": str(result.get("content", ""))[:200]
                })

            return {
                "results_found": len(formatted_results),
                "results": formatted_results
            }
        except Exception as e:
            return {
                "error": f"Search failed: {str(e)}",
                "results_found": 0,
                "results": []
            }

    def _format_retrieved_context(self, context: Dict, reason: Optional[str] = None) -> Dict:
        """Format retrieved context for LLM readability"""
        messages = context.get("messages", [])

        formatted_lines = []
        if reason:
            formatted_lines.append(f"## Retrieved Context: {reason}\n")

        core_range = context.get("core_range", {})
        formatted_lines.append(f"Messages {core_range.get('start', '?')}-{core_range.get('end', '?')}:\n")

        for msg in messages:
            role = msg.get("role", "unknown")
            content = msg.get("content", "")[:500]  # Limit to 500 chars per message
            msg_id = msg.get("_message_id", "")
            is_core = msg.get("_in_core_range", False)

            marker = ">>> " if is_core else "    "
            formatted_lines.append(f"{marker}{msg_id} ({role}): {content}\n")

        return {
            "text": "".join(formatted_lines),
            "message_count": len(messages)
        }

    def _build_minimal_initial_context(self, user_message: str) -> Dict:
        """Build minimal initial context (summary + recent only, ~8K tokens)"""
        context = {
            'summary': self.session.summary if self.session.summary else {},
            'recent': self.session.conversation[-20:] if len(self.session.conversation) > 20 else self.session.conversation,
            'user_query': user_message
        }
        return context

    def _merge_contexts(self, base: Dict, retrieved: List[Dict]) -> Dict:
        """Merge retrieved contexts into base context"""
        merged = base.copy()

        if retrieved:
            merged['retrieved'] = {
                'count': len(retrieved),
                'contexts': retrieved
            }

        return merged

    def chat_with_retrieval(self, user_message: str, max_rounds: int = 3) -> str:
        """
        Chat loop with multi-round retrieval support

        Args:
            user_message: User query
            max_rounds: Max retrieval rounds (default: 3)

        Returns:
            Final response after all retrievals
        """
        # Build minimal initial context
        context = self._build_minimal_initial_context(user_message)
        retrieved_contexts = []

        for round_num in range(max_rounds):
            # Send message (this will handle tool calls)
            response_text = self.send_message(user_message)

            # Check if we're done (no pending tool calls)
            # In the current implementation, send_message handles tools automatically
            # So if we get here, all tool calls have been processed

            # For now, return after first round since send_message handles the loop
            return response_text

        return response_text

    def _process_tool_response(self, response):
        """Process API response that may contain tool use"""
        # Check if response contains tool use
        if response.stop_reason == "tool_use":
            # Find all tool use blocks
            tool_results = []
            for block in response.content:
                if block.type == "tool_use":
                    # Execute the tool
                    result = self._execute_tool(block.name, block.input)
                    tool_results.append({
                        "type": "tool_result",
                        "tool_use_id": block.id,
                        "content": result
                    })

            # Add assistant's tool use to messages
            self.messages.append({
                "role": "assistant",
                "content": response.content
            })

            # Add tool results to messages
            self.messages.append({
                "role": "user",
                "content": tool_results
            })

            # Continue the conversation to get the final response
            model_map = {
                'claude': 'claude-sonnet-4-5-20250929',
                'claude-sonnet': 'claude-sonnet-4-5-20250929',
                'claude-haiku': 'claude-haiku-4-5-20251001',
                'claude-opus': 'claude-opus-4-1-20250805',
            }
            model_id = model_map.get(self.model, self.model)

            # Redefine tools for continuation (same as above)
            tools = [
                {
                    "name": "read_file",
                    "description": "Read the contents of a file from the filesystem. Returns the file content as a string.",
                    "input_schema": {
                        "type": "object",
                        "properties": {
                            "path": {"type": "string", "description": "The absolute or relative path to the file to read"}
                        },
                        "required": ["path"]
                    }
                },
                {
                    "name": "write_file",
                    "description": "Write content to a file. Creates the file if it doesn't exist, overwrites if it does.",
                    "input_schema": {
                        "type": "object",
                        "properties": {
                            "path": {"type": "string", "description": "The absolute or relative path to the file to write"},
                            "content": {"type": "string", "description": "The content to write to the file"}
                        },
                        "required": ["path", "content"]
                    }
                },
                {
                    "name": "list_directory",
                    "description": "List all files and directories in the specified path.",
                    "input_schema": {
                        "type": "object",
                        "properties": {
                            "path": {"type": "string", "description": "The directory path to list. Defaults to current directory if not specified."}
                        },
                        "required": []
                    }
                },
                {
                    "name": "glob_files",
                    "description": "Find files matching a glob pattern (e.g., '**/*.py' for all Python files).",
                    "input_schema": {
                        "type": "object",
                        "properties": {
                            "pattern": {"type": "string", "description": "The glob pattern to match files against"},
                            "path": {"type": "string", "description": "The base directory to search from. Defaults to current directory."}
                        },
                        "required": ["pattern"]
                    }
                },
                {
                    "name": "retrieve_context",
                    "description": "Retrieve specific message ranges from conversation history.",
                    "input_schema": {
                        "type": "object",
                        "properties": {
                            "ranges": {
                                "type": "array",
                                "items": {
                                    "type": "object",
                                    "properties": {
                                        "start": {"type": "string"},
                                        "end": {"type": "string"},
                                        "reason": {"type": "string"}
                                    },
                                    "required": ["start", "end"]
                                }
                            },
                            "expand_context": {"type": "integer", "default": 5}
                        },
                        "required": ["ranges"]
                    }
                },
                {
                    "name": "search_history",
                    "description": "Search conversation history semantically.",
                    "input_schema": {
                        "type": "object",
                        "properties": {
                            "query": {"type": "string"},
                            "max_results": {"type": "integer", "default": 5},
                            "category": {"type": "string", "enum": ["decisions", "code_changes", "problems_solved", "open_issues", "all"], "default": "all"}
                        },
                        "required": ["query"]
                    }
                }
            ]

            # Get final response after tools
            final_response = self.client.messages.create(
                model=model_id,
                max_tokens=4096,
                messages=self.messages,
                tools=tools
            )

            # Recursively process in case there are more tool uses
            return self._process_tool_response(final_response)

        # No tool use, return text response
        for block in response.content:
            if hasattr(block, 'text'):
                return block.text
        return str(response.content)

    def _call_anthropic(self) -> str:
        """Call Anthropic API with tool support"""
        # Map model names to current Claude models
        model_map = {
            # Primary models (4.5 and 4.1)
            'claude': 'claude-sonnet-4-5-20250929',           # Default: Sonnet 4.5
            'claude-sonnet': 'claude-sonnet-4-5-20250929',   # Sonnet 4.5 (fast, capable)
            'claude-haiku': 'claude-haiku-4-5-20251001',     # Haiku 4.5 (fastest, cheapest)
            'claude-opus': 'claude-opus-4-1-20250805',       # Opus 4.1 (most capable, rare use)
        }
        model_id = model_map.get(self.model, self.model)

        # Define available tools
        tools = [
            {
                "name": "read_file",
                "description": "Read the contents of a file from the filesystem. Returns the file content as a string.",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "path": {
                            "type": "string",
                            "description": "The absolute or relative path to the file to read"
                        }
                    },
                    "required": ["path"]
                }
            },
            {
                "name": "write_file",
                "description": "Write content to a file. Creates the file if it doesn't exist, overwrites if it does.",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "path": {
                            "type": "string",
                            "description": "The absolute or relative path to the file to write"
                        },
                        "content": {
                            "type": "string",
                            "description": "The content to write to the file"
                        }
                    },
                    "required": ["path", "content"]
                }
            },
            {
                "name": "list_directory",
                "description": "List all files and directories in the specified path.",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "path": {
                            "type": "string",
                            "description": "The directory path to list. Defaults to current directory if not specified."
                        }
                    },
                    "required": []
                }
            },
            {
                "name": "glob_files",
                "description": "Find files matching a glob pattern (e.g., '**/*.py' for all Python files).",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "pattern": {
                            "type": "string",
                            "description": "The glob pattern to match files against"
                        },
                        "path": {
                            "type": "string",
                            "description": "The base directory to search from. Defaults to current directory."
                        }
                    },
                    "required": ["pattern"]
                }
            },
            {
                "name": "retrieve_context",
                "description": "Retrieve specific message ranges from conversation history. Use when summary mentions something and you need full details. The summary has message_range fields showing where discussions occurred.",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "ranges": {
                            "type": "array",
                            "description": "Message ranges to retrieve",
                            "items": {
                                "type": "object",
                                "properties": {
                                    "start": {"type": "string", "description": "Start message ID (e.g., 'msg_042')"},
                                    "end": {"type": "string", "description": "End message ID (e.g., 'msg_050')"},
                                    "reason": {"type": "string", "description": "Why retrieving this"}
                                },
                                "required": ["start", "end"]
                            }
                        },
                        "expand_context": {"type": "integer", "description": "Messages before/after for context (default: 5)", "default": 5}
                    },
                    "required": ["ranges"]
                }
            },
            {
                "name": "search_history",
                "description": "Search conversation history semantically. Use to find all mentions of a topic across the session.",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "query": {"type": "string", "description": "Search query"},
                        "max_results": {"type": "integer", "description": "Max results (default: 5)", "default": 5},
                        "category": {"type": "string", "description": "Filter by: decisions, code_changes, problems_solved, open_issues, or all", "enum": ["decisions", "code_changes", "problems_solved", "open_issues", "all"], "default": "all"}
                    },
                    "required": ["query"]
                }
            }
        ]

        system_message = self._build_system_message()

        try:
            response = self.client.messages.create(
                model=model_id,
                max_tokens=4096,
                system=system_message,
                messages=self.messages,
                tools=tools
            )

            # Handle tool use in response
            return self._process_tool_response(response)
        except Exception as e:
            return f"❌ Error calling Claude: {e}"

    def _call_anthropic_streaming(self, on_event) -> str:
        """Call Anthropic API with streaming events for tool use"""
        # Map model names to current Claude models
        model_map = {
            'claude': 'claude-sonnet-4-5-20250929',
            'claude-sonnet': 'claude-sonnet-4-5-20250929',
            'claude-haiku': 'claude-haiku-4-5-20251001',
            'claude-opus': 'claude-opus-4-1-20250805',
        }
        model_id = model_map.get(self.model, self.model)

        # Define available tools (same as non-streaming)
        tools = [
            {
                "name": "read_file",
                "description": "Read the contents of a file from the filesystem. Returns the file content as a string.",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "path": {"type": "string", "description": "The absolute or relative path to the file to read"}
                    },
                    "required": ["path"]
                }
            },
            {
                "name": "write_file",
                "description": "Write content to a file. Creates the file if it doesn't exist, overwrites if it does.",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "path": {"type": "string", "description": "The absolute or relative path to the file to write"},
                        "content": {"type": "string", "description": "The content to write to the file"}
                    },
                    "required": ["path", "content"]
                }
            },
            {
                "name": "list_directory",
                "description": "List all files and directories in the specified path.",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "path": {"type": "string", "description": "The directory path to list. Defaults to current directory if not specified."}
                    },
                    "required": []
                }
            },
            {
                "name": "glob_files",
                "description": "Find files matching a glob pattern (e.g., '**/*.py' for all Python files).",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "pattern": {"type": "string", "description": "The glob pattern to match files against"},
                        "path": {"type": "string", "description": "The base directory to search from. Defaults to current directory."}
                    },
                    "required": ["pattern"]
                }
            },
            {
                "name": "retrieve_context",
                "description": "Retrieve specific message ranges from conversation history.",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "ranges": {
                            "type": "array",
                            "items": {
                                "type": "object",
                                "properties": {
                                    "start": {"type": "string"},
                                    "end": {"type": "string"},
                                    "reason": {"type": "string"}
                                },
                                "required": ["start", "end"]
                            }
                        },
                        "expand_context": {"type": "integer", "default": 5}
                    },
                    "required": ["ranges"]
                }
            },
            {
                "name": "search_history",
                "description": "Search conversation history semantically.",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "query": {"type": "string"},
                        "max_results": {"type": "integer", "default": 5},
                        "category": {"type": "string", "enum": ["decisions", "code_changes", "problems_solved", "open_issues", "all"], "default": "all"}
                    },
                    "required": ["query"]
                }
            }
        ]

        system_message = self._build_system_message()

        try:
            response = self.client.messages.create(
                model=model_id,
                max_tokens=4096,
                system=system_message,
                messages=self.messages,
                tools=tools
            )

            # Process response and emit streaming events
            collected_text = []

            # Check if response contains tool use
            if response.stop_reason == "tool_use":
                # Emit text chunks first
                for block in response.content:
                    if block.type == "text":
                        on_event("text_chunk", {"content": block.text})
                        collected_text.append(block.text)
                    elif block.type == "tool_use":
                        # Emit tool call start
                        on_event("tool_call_start", {
                            "tool_name": block.name,
                            "tool_input": block.input
                        })

                        # Execute tool
                        result = self._execute_tool(block.name, block.input)

                        # Emit tool result
                        on_event("tool_call_result", {
                            "tool_name": block.name,
                            "result": result
                        })

                # Now continue conversation with tool results
                tool_results = []
                for block in response.content:
                    if block.type == "tool_use":
                        result = self._execute_tool(block.name, block.input)
                        tool_results.append({
                            "type": "tool_result",
                            "tool_use_id": block.id,
                            "content": result
                        })

                # Add assistant's tool use to messages
                self.messages.append({
                    "role": "assistant",
                    "content": response.content
                })

                # Add tool results to messages
                self.messages.append({
                    "role": "user",
                    "content": tool_results
                })

                # Continue the conversation to get final text response
                continue_response = self.client.messages.create(
                    model=model_id,
                    max_tokens=4096,
                    messages=self.messages,
                    tools=tools
                )

                # Emit final text chunks
                for block in continue_response.content:
                    if block.type == "text":
                        on_event("text_chunk", {"content": block.text})
                        collected_text.append(block.text)

                return "\n".join(collected_text)

            else:
                # No tool use, just text
                for block in response.content:
                    if block.type == "text":
                        on_event("text_chunk", {"content": block.text})
                        collected_text.append(block.text)

                return "\n".join(collected_text)

        except Exception as e:
            error_msg = f"❌ Error calling Claude: {e}"
            on_event("text_chunk", {"content": error_msg})
            return error_msg

    def _call_openai(self) -> str:
        """Call OpenAI API with tool support"""
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

        # Define tools in OpenAI format
        tools = [
            {
                "type": "function",
                "function": {
                    "name": "read_file",
                    "description": "Read the contents of a file from the filesystem. Returns the file content as a string.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "path": {
                                "type": "string",
                                "description": "The absolute or relative path to the file to read"
                            }
                        },
                        "required": ["path"]
                    }
                }
            },
            {
                "type": "function",
                "function": {
                    "name": "write_file",
                    "description": "Write content to a file. Creates the file if it doesn't exist, overwrites if it does.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "path": {
                                "type": "string",
                                "description": "The absolute or relative path to the file to write"
                            },
                            "content": {
                                "type": "string",
                                "description": "The content to write to the file"
                            }
                        },
                        "required": ["path", "content"]
                    }
                }
            },
            {
                "type": "function",
                "function": {
                    "name": "list_directory",
                    "description": "List all files and directories in the specified path.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "path": {
                                "type": "string",
                                "description": "The directory path to list. Defaults to current directory if not specified."
                            }
                        },
                        "required": []
                    }
                }
            },
            {
                "type": "function",
                "function": {
                    "name": "glob_files",
                    "description": "Find files matching a glob pattern (e.g., '**/*.py' for all Python files).",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "pattern": {
                                "type": "string",
                                "description": "The glob pattern to match files against"
                            },
                            "path": {
                                "type": "string",
                                "description": "The base directory to search from. Defaults to current directory."
                            }
                        },
                        "required": ["pattern"]
                    }
                }
            },
            {
                "type": "function",
                "function": {
                    "name": "retrieve_context",
                    "description": "Retrieve specific message ranges from conversation history.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "ranges": {
                                "type": "array",
                                "description": "Message ranges to retrieve",
                                "items": {
                                    "type": "object",
                                    "properties": {
                                        "start": {"type": "string"},
                                        "end": {"type": "string"},
                                        "reason": {"type": "string"}
                                    },
                                    "required": ["start", "end"]
                                }
                            },
                            "expand_context": {"type": "integer", "default": 5}
                        },
                        "required": ["ranges"]
                    }
                }
            },
            {
                "type": "function",
                "function": {
                    "name": "search_history",
                    "description": "Search conversation history semantically.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "query": {"type": "string"},
                            "max_results": {"type": "integer", "default": 5},
                            "category": {"type": "string", "enum": ["decisions", "code_changes", "problems_solved", "open_issues", "all"], "default": "all"}
                        },
                        "required": ["query"]
                    }
                }
            }
        ]

        system_message = self._build_system_message()

        # OpenAI uses system message as first message in array
        messages_with_system = [{"role": "system", "content": system_message}] + self.messages

        try:
            response = self.client.chat.completions.create(
                model=model_id,
                messages=messages_with_system,
                tools=tools,
                tool_choice="auto"
            )

            # Handle tool calls in response
            return self._process_openai_tool_response(response)
        except Exception as e:
            return f"❌ Error calling OpenAI: {e}"

    def _call_openai_streaming(self, on_event) -> str:
        """Call OpenAI API with streaming events for tool use (stub for now)"""
        # For now, just call the non-streaming version and emit as chunks
        result = self._call_openai()
        on_event("text_chunk", {"content": result})
        return result

    def _process_openai_tool_response(self, response):
        """Process OpenAI response that may contain tool calls"""
        message = response.choices[0].message

        # Check if there are tool calls
        if message.tool_calls:
            # Add assistant's message with tool calls
            self.messages.append({
                "role": "assistant",
                "content": message.content,
                "tool_calls": [
                    {
                        "id": tc.id,
                        "type": "function",
                        "function": {
                            "name": tc.function.name,
                            "arguments": tc.function.arguments
                        }
                    } for tc in message.tool_calls
                ]
            })

            # Execute each tool call
            import json
            for tool_call in message.tool_calls:
                function_name = tool_call.function.name
                function_args = json.loads(tool_call.function.arguments)

                # Execute the tool
                result = self._execute_tool(function_name, function_args)

                # Add tool result to messages
                self.messages.append({
                    "role": "tool",
                    "tool_call_id": tool_call.id,
                    "name": function_name,
                    "content": result
                })

            # Redefine tools for continuation
            tools = [
                {
                    "type": "function",
                    "function": {
                        "name": "read_file",
                        "description": "Read the contents of a file from the filesystem.",
                        "parameters": {
                            "type": "object",
                            "properties": {
                                "path": {"type": "string", "description": "The file path to read"}
                            },
                            "required": ["path"]
                        }
                    }
                },
                {
                    "type": "function",
                    "function": {
                        "name": "write_file",
                        "description": "Write content to a file.",
                        "parameters": {
                            "type": "object",
                            "properties": {
                                "path": {"type": "string", "description": "The file path to write"},
                                "content": {"type": "string", "description": "The content to write"}
                            },
                            "required": ["path", "content"]
                        }
                    }
                },
                {
                    "type": "function",
                    "function": {
                        "name": "list_directory",
                        "description": "List directory contents.",
                        "parameters": {
                            "type": "object",
                            "properties": {
                                "path": {"type": "string", "description": "The directory path"}
                            }
                        }
                    }
                },
                {
                    "type": "function",
                    "function": {
                        "name": "glob_files",
                        "description": "Find files by glob pattern.",
                        "parameters": {
                            "type": "object",
                            "properties": {
                                "pattern": {"type": "string", "description": "The glob pattern"},
                                "path": {"type": "string", "description": "Base directory"}
                            },
                            "required": ["pattern"]
                        }
                    }
                },
                {
                    "type": "function",
                    "function": {
                        "name": "retrieve_context",
                        "description": "Retrieve specific message ranges from conversation history.",
                        "parameters": {
                            "type": "object",
                            "properties": {
                                "ranges": {
                                    "type": "array",
                                    "items": {
                                        "type": "object",
                                        "properties": {
                                            "start": {"type": "string"},
                                            "end": {"type": "string"},
                                            "reason": {"type": "string"}
                                        },
                                        "required": ["start", "end"]
                                    }
                                },
                                "expand_context": {"type": "integer", "default": 5}
                            },
                            "required": ["ranges"]
                        }
                    }
                },
                {
                    "type": "function",
                    "function": {
                        "name": "search_history",
                        "description": "Search conversation history semantically.",
                        "parameters": {
                            "type": "object",
                            "properties": {
                                "query": {"type": "string"},
                                "max_results": {"type": "integer", "default": 5},
                                "category": {"type": "string", "enum": ["decisions", "code_changes", "problems_solved", "open_issues", "all"], "default": "all"}
                            },
                            "required": ["query"]
                        }
                    }
                }
            ]

            # Get final response after tools
            model_map = {
                'gpt5': 'gpt-5',
                'gpt5-chat': 'gpt-5-chat-latest',
                'gpt5-mini': 'gpt-5-mini',
                'gpt5-nano': 'gpt-5-nano',
                'gpt5-codex': 'gpt-5-codex',
                'gpt4': 'gpt-4-turbo',
                'gpt4o': 'gpt-4o',
                'gpt4-turbo': 'gpt-4-turbo'
            }
            model_id = model_map.get(self.model, self.model)

            final_response = self.client.chat.completions.create(
                model=model_id,
                messages=self.messages,
                tools=tools,
                tool_choice="auto"
            )

            # Recursively process in case there are more tool calls
            return self._process_openai_tool_response(final_response)

        # No tool calls, return text response
        return message.content if message.content else ""

    def chat_loop(self, enable_compaction: bool = True, enable_retrieval: bool = True):
        """
        Interactive chat loop with optional preemptive compaction and retrieval

        Args:
            enable_compaction: Enable automatic compaction at 190K tokens
            enable_retrieval: Enable LLM retrieval tools (Phase 8)
        """
        # Welcome header with spacing
        print("\n" + "═" * 60)
        print(f"  \033[91m●\033[0m RecCli Chat - {self.model}")
        print(f"  Session: {self.session_path.name}")

        # Initialize compactor if enabled
        compactor = None
        if enable_compaction:
            try:
                from ..summarization.preemptive_compaction import PreemptiveCompactor
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
                            # Refresh project context after compaction so the
                            # LLM retains spatial awareness and feature navigation
                            self.refresh_project_context()

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
