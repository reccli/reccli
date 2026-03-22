"""
Write-Ahead Log (WAL) based terminal recorder
Crash-safe append-only recording with background parsing
"""

import os
import sys
import json
import time
import signal
import struct
import fcntl
import termios
import pty
from pathlib import Path
from typing import Optional
from datetime import datetime

from ..session.devsession import DevSession
from ..project.devproject import discover_project_root


class WALRecorder:
    """
    Crash-safe terminal recorder using write-ahead log pattern

    Architecture:
    1. Raw events go to .wal file (append-only, fsync)
    2. Background parser creates .conv.json sidecar
    3. On stop: finalize to .devsession atomically
    """

    def __init__(self, output_path: Path, shell: Optional[str] = None):
        self.output_path = Path(output_path)
        self.shell = shell or os.environ.get('SHELL', '/bin/bash')

        # WAL file (append-only raw events)
        self.wal_path = self.output_path.with_suffix('.wal')
        self.wal_file = None

        # Session metadata
        self.session_id = self.output_path.stem
        self.start_time = None
        self.running = False
        self.event_count = 0
        self.fsync_counter = 0
        self.fsync_interval = 64  # fsync every N appends

        # Terminal state
        self.old_tty_attrs = None

    def get_terminal_size(self):
        """Get current terminal size"""
        try:
            winsize = fcntl.ioctl(sys.stdout.fileno(), termios.TIOCGWINSZ, b'\x00' * 8)
            rows, cols = struct.unpack('HHHH', winsize)[:2]
            return cols, rows
        except:
            return 80, 24

    def _append_event(self, timestamp: float, event_type: str, data: str):
        """Append event to WAL (crash-safe)"""
        event = [timestamp, event_type, data]
        event_json = json.dumps(event, ensure_ascii=False)

        # Write to WAL
        self.wal_file.write(event_json + '\n')
        self.event_count += 1
        self.fsync_counter += 1

        # Periodic fsync for crash safety
        if self.fsync_counter >= self.fsync_interval:
            self.wal_file.flush()
            os.fsync(self.wal_file.fileno())
            self.fsync_counter = 0

    def record(self) -> int:
        """Start recording session"""
        self.running = True
        self.start_time = time.time()

        # Open WAL for append
        self.wal_file = open(self.wal_path, 'w', encoding='utf-8')

        # Write header with metadata
        cols, rows = self.get_terminal_size()
        header = {
            "format": "reccli-wal",
            "version": 2,
            "session_id": self.session_id,
            "started": datetime.now().isoformat(),
            "width": cols,
            "height": rows,
            "shell": self.shell,
            "working_directory": str(Path.cwd()),
            "project_root": str(discover_project_root(Path.cwd()) or Path.cwd()),
        }
        self.wal_file.write(json.dumps(header) + '\n')
        self.wal_file.flush()

        # Save terminal attributes
        try:
            self.old_tty_attrs = termios.tcgetattr(sys.stdin.fileno())
        except:
            self.old_tty_attrs = None

        # Setup signal handlers
        signal.signal(signal.SIGWINCH, self._handle_resize)

        print(f"🔴 Recording to {self.output_path.name}")
        print(f"Shell: {self.shell}")
        print("Press Ctrl+D or type 'exit' to stop recording\n")

        # Spawn PTY
        exit_code = 0
        try:
            exit_code = pty.spawn(
                [self.shell],
                master_read=self._handle_output,
                stdin_read=self._handle_input
            )
        except KeyboardInterrupt:
            exit_code = 130
        except OSError as e:
            print(f"Error spawning shell: {e}", file=sys.stderr)
            exit_code = 1
        finally:
            try:
                self._finalize()
            except Exception as e:
                print(f"Error during finalization: {e}", file=sys.stderr)

        return exit_code

    def _handle_output(self, fd: int) -> bytes:
        """Handle PTY output"""
        try:
            data = os.read(fd, 1024)
        except OSError:
            return b''

        if not data:
            return b''

        timestamp = time.time() - self.start_time
        text = data.decode('utf-8', errors='replace')
        self._append_event(timestamp, 'o', text)

        return data

    def _handle_input(self, fd: int) -> bytes:
        """Handle user input"""
        try:
            data = os.read(fd, 1024)
        except OSError:
            return b''

        if not data:
            return b''

        timestamp = time.time() - self.start_time
        text = data.decode('utf-8', errors='replace')
        self._append_event(timestamp, 'i', text)

        return data

    def _handle_resize(self, signum, frame):
        """Handle terminal resize"""
        cols, rows = self.get_terminal_size()
        timestamp = time.time() - self.start_time
        self._append_event(timestamp, 'r', f"{cols}x{rows}")

    def _finalize(self):
        """Finalize recording: WAL → .devsession"""
        self.running = False

        # Restore terminal
        if self.old_tty_attrs:
            try:
                termios.tcsetattr(sys.stdin.fileno(), termios.TCSADRAIN, self.old_tty_attrs)
            except:
                pass

        # Final fsync and close WAL
        if self.wal_file:
            self.wal_file.flush()
            os.fsync(self.wal_file.fileno())
            self.wal_file.close()

        print(f"\n\n✅ Recording stopped")
        print(f"Finalizing {self.event_count} events...")

        try:
            # Load events from WAL
            events = []
            header = None

            with open(self.wal_path, 'r', encoding='utf-8') as f:
                for i, line in enumerate(f):
                    if i == 0:
                        header = json.loads(line)
                        continue
                    events.append(json.loads(line))

            # Parse conversation
            print("Parsing conversation...")
            from .parser import parse_conversation
            conversation = parse_conversation(events)
            print(f"✓ Parsed {len(conversation)} messages")

            # Build final .devsession
            duration = events[-1][0] if events else 0

            session_data = {
                "format": "devsession",
                "version": "2.0",
                "session_id": self.session_id,
                "created": header["started"],
                "updated": datetime.now().isoformat(),
                "metadata": {
                    "session_id": self.session_id,
                    "created_at": header["started"],
                    "updated_at": datetime.now().isoformat(),
                    "working_directory": header.get("working_directory"),
                    "project_root": header.get("project_root"),
                },
                "terminal_recording": {
                    "version": 2,
                    "width": header["width"],
                    "height": header["height"],
                    "shell": header["shell"],
                    "events": events
                },
                "conversation": conversation,
                "summary": None,
                "spans": [],
                "vector_index": None,
                "summary_sync": {
                    "status": "not_started",
                    "last_synced_msg_id": None,
                    "last_synced_msg_index": None,
                    "updated_at": None,
                    "pending_messages": 0,
                    "pending_tokens": 0,
                },
                "embedding_storage": {
                    "mode": "inline",
                    "messages_file": None,
                    "format": None,
                    "external_message_count": 0,
                    "loaded": True,
                },
                "token_counts": {
                    "conversation": 0,
                    "terminal_output": 0,
                    "summary": 0,
                    "total": 0,
                    "last_updated": None
                },
                "checksums": {},
                "compaction_history": [],
                "checkpoints": [],
                "episodes": [],
                "current_episode_id": None,
            }

            # Atomic write: .tmp → .devsession
            tmp_path = self.output_path.with_suffix('.devsession.tmp')
            with open(tmp_path, 'w', encoding='utf-8') as f:
                json.dump(session_data, f, indent=2, ensure_ascii=False)
                f.flush()
                os.fsync(f.fileno())

            # Atomic rename
            os.rename(tmp_path, self.output_path)

            print(f"✓ Finalized to {self.output_path}")
            print(f"✓ Duration: {duration:.1f}s")
            print(f"✓ Events: {len(events)}")

            # Auto-compact (remove redundant events)
            original_size = self.output_path.stat().st_size
            if len(conversation) > 0:
                print("Compacting session...")
                from .compactor import auto_compact
                stats = auto_compact(self.output_path, mode='conversation')
                saved_kb = stats['saved_bytes'] / 1024
                ratio = stats['compression_ratio']
                print(f"✓ Compacted: {saved_kb:.1f}KB saved ({ratio:.1f}x smaller)")

            # Remove WAL (successful finalization)
            self.wal_path.unlink()

        except Exception as e:
            print(f"❌ Error finalizing: {e}", file=sys.stderr)
            import traceback
            traceback.print_exc()
            raise


def record_session_wal(output_path: Path, shell: Optional[str] = None) -> int:
    """Record session using WAL pattern"""
    recorder = WALRecorder(output_path, shell)
    return recorder.record()
