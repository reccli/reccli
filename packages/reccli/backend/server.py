#!/usr/bin/env python3
"""
Python backend server for RecCli TypeScript UI.
Handles LLM communication and .devsession management.
"""

import os
import sys
import json
import asyncio
from pathlib import Path
from typing import Dict, Any, Optional
import threading
import queue

# Add parent directory to path to import reccli modules
sys.path.insert(0, str(Path(__file__).parent.parent))

from reccli.llm import LLMSession
from reccli.devsession import DevSession
from reccli.devproject import default_devsession_path

class RecCliBackend:
    def __init__(self):
        self.session: Optional[LLMSession] = None
        self.devsession: Optional[DevSession] = None
        self.request_queue = queue.Queue()
        self.response_map: Dict[str, queue.Queue] = {}

    def initialize_session(self, model: str = None, session_name: Optional[str] = None):
        """Initialize a new chat session."""
        # Get model from environment or use default
        if not model:
            model = os.environ.get('RECCLI_MODEL', 'claude')

        # Get session name from environment or generate
        if not session_name:
            session_name = os.environ.get('RECCLI_SESSION_NAME')
            if not session_name:
                from datetime import datetime
                session_name = f"session_{datetime.now().strftime('%Y%m%d_%H%M%S')}"

        # Create output path for session
        output_path = default_devsession_path(Path.cwd())

        # Initialize .devsession file
        self.devsession = DevSession(session_name)

        # Initialize LLM session
        self.session = LLMSession(
            model=model,
            session_path=output_path,
            api_key=None  # Will load from config
        )

    def process_message(self, content: str) -> Dict[str, Any]:
        """Process a chat message and return the response."""
        if not self.session:
            self.initialize_session()

        # Send message and get response
        response = self.session.send_message(content)

        # Get approximate token count (4 chars per token, ~250 tokens per message pair)
        token_count = len(self.session.messages) * 250  # Rough estimate

        return {
            "content": response,
            "tokenCount": token_count
        }

    def process_message_streaming(self, content: str, request_id: str):
        """Process message and emit streaming events via stdout."""
        if not self.session:
            self.initialize_session()

        def emit_event(event_type: str, data: Dict[str, Any]):
            """Emit a streaming event to stdout"""
            event = {
                "id": request_id,
                "type": event_type,
                **data
            }
            print(json.dumps(event), flush=True)

        try:
            # Send message with streaming callback
            self.session.send_message_streaming(content, on_event=emit_event)

            # Emit final event
            emit_event("final_response", {"complete": True})

        except Exception as e:
            emit_event("error", {"message": str(e)})

    def get_session_info(self) -> Dict[str, Any]:
        """Get current session information."""
        if not self.session:
            self.initialize_session()

        session_name = os.environ.get('RECCLI_SESSION_NAME', 'session')
        token_count = len(self.session.messages) * 250 if self.session else 0

        return {
            "name": session_name,
            "tokenCount": token_count,
            "maxTokens": 150000  # Max before preemptive compaction
        }

    def handle_request(self, request: Dict[str, Any]) -> Dict[str, Any]:
        """Handle JSON-RPC request and return response."""
        request_id = request.get('id')
        method = request.get('method')
        params = request.get('params', {})

        try:
            if method == 'ping':
                result = {"status": "ready"}
            elif method == 'chat':
                result = self.process_message(params.get('content', ''))
            elif method == 'chat_streaming':
                # Handle streaming - emit events directly, don't return here
                self.process_message_streaming(params.get('content', ''), request_id)
                return None  # Streaming already handled
            elif method == 'getSessionInfo':
                result = self.get_session_info()
            else:
                raise ValueError(f"Unknown method: {method}")

            return {
                "id": request_id,
                "result": result
            }
        except Exception as e:
            return {
                "id": request_id,
                "error": str(e)
            }

    def run(self):
        """Main loop reading JSON-RPC from stdin and writing to stdout."""
        while True:
            try:
                line = sys.stdin.readline()
                if not line:
                    break

                request = json.loads(line.strip())
                response = self.handle_request(request)

                # Write response to stdout (if not None - streaming methods handle their own output)
                if response is not None:
                    print(json.dumps(response))
                    sys.stdout.flush()

            except json.JSONDecodeError as e:
                error_response = {
                    "id": None,
                    "error": f"Invalid JSON: {e}"
                }
                print(json.dumps(error_response))
                sys.stdout.flush()
            except KeyboardInterrupt:
                break
            except Exception as e:
                error_response = {
                    "id": None,
                    "error": f"Server error: {e}"
                }
                print(json.dumps(error_response))
                sys.stdout.flush()

if __name__ == "__main__":
    backend = RecCliBackend()
    backend.run()
