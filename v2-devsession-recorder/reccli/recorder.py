"""
DevsessionRecorder - Pure Python terminal recorder
Captures terminal I/O using PTY and outputs .devsession format
Includes floating button GUI for easy recording
"""

import os
import sys
import pty
import time
import signal
import select
import termios
import struct
import fcntl
import subprocess
import threading
from pathlib import Path
from typing import Optional
from datetime import datetime

from .devsession import DevSession

# Tkinter imports (optional for GUI mode)
try:
    import tkinter as tk
    from tkinter import messagebox, ttk
    HAS_TKINTER = True
except ImportError:
    HAS_TKINTER = False


class DevsessionRecorder:
    """Record terminal sessions to .devsession format"""

    def __init__(self, output_path: Path, shell: Optional[str] = None):
        """
        Initialize recorder

        Args:
            output_path: Path to output .devsession file
            shell: Shell to spawn (default: $SHELL or /bin/bash)
        """
        self.output_path = Path(output_path)
        self.shell = shell or os.environ.get('SHELL', '/bin/bash')

        # Session management
        self.session = DevSession()
        self.start_time = None
        self.running = False

        # Terminal state
        self.old_tty_attrs = None

    def get_terminal_size(self):
        """Get current terminal size"""
        try:
            # Get window size using ioctl
            winsize = fcntl.ioctl(sys.stdout.fileno(), termios.TIOCGWINSZ, b'\x00' * 8)
            rows, cols = struct.unpack('HHHH', winsize)[:2]
            return cols, rows
        except:
            return 80, 24  # Default fallback

    def record(self) -> int:
        """
        Start recording terminal session

        Returns:
            Exit code of spawned shell
        """
        self.running = True
        self.start_time = time.time()

        # Get terminal info
        cols, rows = self.get_terminal_size()
        self.session.set_terminal_info(cols, rows, self.shell)

        # Save raw terminal attributes
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
        try:
            exit_code = pty.spawn(
                [self.shell],
                master_read=self._handle_output,
                stdin_read=self._handle_input
            )
        except OSError as e:
            print(f"Error spawning shell: {e}", file=sys.stderr)
            exit_code = 1
        finally:
            self._cleanup()

        return exit_code

    def _handle_output(self, fd: int) -> bytes:
        """
        Handle output from PTY (shell output)

        Args:
            fd: File descriptor to read from

        Returns:
            Data read from PTY
        """
        try:
            data = os.read(fd, 1024)
        except OSError:
            return b''

        if not data:
            return b''

        # Record event
        timestamp = time.time() - self.start_time
        text = data.decode('utf-8', errors='replace')
        self.session.append_event(timestamp, 'o', text)

        # Auto-save every 50 events (crash protection)
        if self.session.get_event_count() % 50 == 0:
            self._incremental_save()

        return data

    def _handle_input(self, fd: int) -> bytes:
        """
        Handle input from user (stdin)

        Args:
            fd: File descriptor to read from

        Returns:
            Data read from stdin
        """
        try:
            data = os.read(fd, 1024)
        except OSError:
            return b''

        if not data:
            return b''

        # Record event
        timestamp = time.time() - self.start_time
        text = data.decode('utf-8', errors='replace')
        self.session.append_event(timestamp, 'i', text)

        return data

    def _handle_resize(self, signum, frame):
        """Handle terminal window resize"""
        cols, rows = self.get_terminal_size()
        timestamp = time.time() - self.start_time

        # Record resize event
        self.session.append_event(timestamp, 'r', f"{cols}x{rows}")

        # Update session terminal info
        self.session.terminal_recording["width"] = cols
        self.session.terminal_recording["height"] = rows

    def _incremental_save(self):
        """Save current state (for crash protection)"""
        try:
            # Save with .tmp extension first
            tmp_path = self.output_path.with_suffix('.devsession.tmp')
            self.session.save(tmp_path)

            # Rename to final path (atomic on Unix)
            tmp_path.replace(self.output_path)
        except Exception as e:
            # Don't crash recording if save fails
            print(f"\n⚠️  Auto-save failed: {e}", file=sys.stderr)

    def _cleanup(self):
        """Cleanup and save final .devsession file"""
        self.running = False

        # Restore terminal attributes
        if self.old_tty_attrs:
            try:
                termios.tcsetattr(sys.stdin.fileno(), termios.TCSADRAIN, self.old_tty_attrs)
            except:
                pass

        # Final save
        print(f"\n\n✅ Recording stopped")
        print(f"Saving to {self.output_path}...")

        try:
            self.session.save(self.output_path)
            duration = self.session.get_duration()
            event_count = self.session.get_event_count()
            print(f"✓ Saved {event_count} events ({duration:.1f} seconds)")
            print(f"✓ File: {self.output_path}")
        except Exception as e:
            print(f"❌ Error saving .devsession: {e}", file=sys.stderr)


def record_session(output_path: Path, shell: Optional[str] = None) -> int:
    """
    Convenience function to record a terminal session

    Args:
        output_path: Path to output .devsession file
        shell: Shell to spawn (default: $SHELL or /bin/bash)

    Returns:
        Exit code of spawned shell
    """
    recorder = DevsessionRecorder(output_path, shell)
    return recorder.record()


# ============================================================================
# GUI - Floating Button Interface
# ============================================================================

class BackgroundRecorder:
    """Manages background recording process using subprocess"""

    def __init__(self):
        self.recording = False
        self.output_path = None
        self.start_time = None
        self.terminal_id = None

    def start(self, terminal_id: Optional[str] = None) -> tuple[bool, str]:
        """
        Start background recording by launching CLI recorder in terminal

        Args:
            terminal_id: Terminal window ID to record (macOS specific)

        Returns:
            (success: bool, message: str)
        """
        if self.recording:
            return False, "Already recording"

        # Store terminal_id for targeting during stop
        self.terminal_id = terminal_id

        # Generate output path
        sessions_dir = Path.home() / '.reccli' / 'sessions'
        sessions_dir.mkdir(parents=True, exist_ok=True)
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        filename = f"session_{timestamp}"
        self.output_path = sessions_dir / f"{filename}.devsession"

        self.start_time = time.time()

        if sys.platform == 'darwin':  # macOS
            # Use AppleScript to type the reccli command into the terminal
            # This creates a nested recording session (like asciinema rec)
            # Find the reccli-v2.py entry point
            reccli_script = Path(__file__).parent.parent / 'reccli-v2.py'

            # Command to start CLI recording
            cmd = f"python3 {reccli_script} record -o {self.output_path}"

            # Build AppleScript to activate terminal and send keystrokes
            script_text = f'''
            tell application "Terminal"
                activate
                delay 0.2
            end tell
            tell application "System Events"
                tell process "Terminal"
                    keystroke "{cmd}"
                    keystroke return
                end tell
            end tell
            '''

            try:
                subprocess.run(['osascript', '-e', script_text],
                             check=True, capture_output=True, text=True)
                self.recording = True
                return True, str(self.output_path)
            except Exception as e:
                return False, f"Failed to start recording: {str(e)}"
        else:
            return False, "Currently only macOS is supported"

    def stop(self) -> tuple[bool, str, float]:
        """
        Stop recording by sending Ctrl+D to terminal

        Returns:
            (success: bool, output_path: str, duration: float)
        """
        if not self.recording:
            return False, "Not recording", 0

        duration = time.time() - self.start_time if self.start_time else 0

        if sys.platform == 'darwin':  # macOS
            # Send Ctrl+D to the specific terminal window being recorded
            if self.terminal_id:
                # Target specific window by ID
                script_text = f'''
                tell application "Terminal"
                    set targetWindow to first window whose id is {self.terminal_id}
                    set index of targetWindow to 1
                    activate
                    delay 0.2
                end tell
                tell application "System Events"
                    tell process "Terminal"
                        keystroke "d" using control down
                    end tell
                end tell
                '''
            else:
                # Fallback to frontmost window
                script_text = '''
                tell application "Terminal"
                    activate
                    delay 0.2
                end tell
                tell application "System Events"
                    tell process "Terminal"
                        keystroke "d" using control down
                    end tell
                end tell
                '''

            try:
                subprocess.run(['osascript', '-e', script_text],
                             check=True, capture_output=True)
                # Give recorder time to finish writing
                time.sleep(1.0)
            except Exception as e:
                print(f"Warning: Failed to stop recording: {e}")

        self.recording = False
        self.terminal_id = None
        return True, str(self.output_path), duration


class DevsessionGUI:
    """Floating button GUI for .devsession recorder"""

    def __init__(self, terminal_id=None):
        if not HAS_TKINTER:
            raise ImportError("Tkinter not available. Install tkinter for GUI mode.")

        self.recorder = BackgroundRecorder()
        self.terminal_window = None
        self.last_terminal_position = None
        self.current_terminal_id = None
        self.my_terminal_id = terminal_id
        self.last_terminal_id = None
        self.terminal_is_frontmost = False
        self.is_dark_mode = self._detect_dark_mode()
        self.popup_hidden = False

        # Create GUI
        self.root = tk.Tk()
        self.root.title("RecCli v2")

        # macOS rounded corners
        try:
            self.root.tk.call('::tk::unsupported::MacWindowStyle', 'style',
                            self.root._w, 'floating', 'closeBox')
        except:
            self.root.overrideredirect(True)

        self.root.attributes('-topmost', True)
        self.root.geometry("80x30")

        # Set background color
        bg_color = '#2c2c2c' if self.is_dark_mode else '#e5e5e5'
        self.root.configure(bg=bg_color)

        # Get terminal window position
        if self.my_terminal_id:
            self.find_terminal_by_id(self.my_terminal_id)
        else:
            self.find_terminal_window()
            self.my_terminal_id = self.current_terminal_id

        self.position_window()

        # Create canvas
        self.canvas = tk.Canvas(
            self.root,
            width=80,
            height=30,
            highlightthickness=0,
            bg=bg_color
        )
        self.canvas.pack()

        # Draw initial button
        self.draw_button(recording=False)

        # Bind events
        self.canvas.bind("<ButtonPress-1>", self.on_press)
        self.canvas.bind("<B1-Motion>", self.on_drag)
        self.canvas.bind("<ButtonRelease-1>", self.on_release)
        self.canvas.bind("<Button-3>", self.show_menu)

        # Create right-click menu
        self.menu = tk.Menu(self.root, tearoff=0)
        self.menu.add_command(label="📊 Stats", command=self.show_stats)
        self.menu.add_command(label="📁 Sessions", command=self.open_sessions_folder)
        self.menu.add_separator()
        self.menu.add_command(label="❌ Quit", command=self.quit)

        # Track state
        self.recording = False
        self.start_pos = None
        self.is_dragging = False
        self.recording_start_time = None

        # Start position tracking
        self.track_terminal_position()

    def _detect_dark_mode(self):
        """Detect if macOS is in dark mode"""
        try:
            result = subprocess.run(
                ['defaults', 'read', '-g', 'AppleInterfaceStyle'],
                capture_output=True,
                text=True,
                timeout=1
            )
            return result.returncode == 0 and 'Dark' in result.stdout
        except:
            return False

    def find_terminal_by_id(self, target_id):
        """Find specific terminal window by ID"""
        try:
            # Only check if minimized (not 'visible' since that's false on different Space)
            result = subprocess.run([
                'osascript',
                '-e', 'tell application "Terminal"',
                '-e', 'repeat with w in windows',
                '-e', f'if id of w is {target_id} then',
                '-e', 'set isMini to miniaturized of w',
                '-e', 'if isMini is true then',
                '-e', 'return "MINIMIZED"',
                '-e', 'end if',
                '-e', 'set windowPosition to position of w',
                '-e', 'set windowSize to size of w',
                '-e', 'set windowID to id of w',
                '-e', 'return (item 1 of windowPosition) & "," & (item 2 of windowPosition) & "," & (item 1 of windowSize) & "," & (item 2 of windowSize) & "," & windowID',
                '-e', 'end if',
                '-e', 'end repeat',
                '-e', 'return "NOT_FOUND"',
                '-e', 'end tell'
            ], capture_output=True, text=True, timeout=2)

            if result.returncode == 0 and result.stdout.strip():
                output = result.stdout.strip()
                if output == "MINIMIZED":
                    if self.recorder.recording:
                        if not self.popup_hidden:
                            self.root.withdraw()
                            self.popup_hidden = True
                        return
                    else:
                        self.quit()
                        return
                elif output != "NOT_FOUND":
                    if self.popup_hidden:
                        self.root.deiconify()
                        self.popup_hidden = False
                        self.draw_button(recording=self.recorder.recording)
                        self.recording = self.recorder.recording

                    parts = [p.strip() for p in output.replace(',,', ',').split(',') if p.strip()]
                    if len(parts) >= 5:
                        window_id = parts[4]
                        self.current_terminal_id = window_id
                        self.terminal_window = {
                            'x': int(parts[0]),
                            'y': int(parts[1]),
                            'width': int(parts[2]),
                            'height': int(parts[3]),
                            'id': window_id
                        }
                        return
                else:
                    self.quit()
        except Exception:
            pass

    def find_terminal_window(self):
        """Find active terminal window"""
        try:
            result = subprocess.run([
                'osascript',
                '-e', 'tell application "System Events"',
                '-e', 'set frontApp to name of first application process whose frontmost is true',
                '-e', 'if frontApp is "Terminal" then',
                '-e', 'tell application "Terminal"',
                '-e', 'set frontWindow to front window',
                '-e', 'set windowPosition to position of frontWindow',
                '-e', 'set windowSize to size of frontWindow',
                '-e', 'set windowID to id of frontWindow',
                '-e', 'return (item 1 of windowPosition) & "," & (item 2 of windowPosition) & "," & (item 1 of windowSize) & "," & (item 2 of windowSize) & "," & windowID',
                '-e', 'end tell',
                '-e', 'else',
                '-e', 'return "NOT_TERMINAL"',
                '-e', 'end if',
                '-e', 'end tell'
            ], capture_output=True, text=True, timeout=2)

            if result.returncode == 0 and result.stdout.strip():
                output = result.stdout.strip()
                if output == "NOT_TERMINAL":
                    self.terminal_window = None
                    self.current_terminal_id = None
                    self.terminal_is_frontmost = False
                    return
                else:
                    self.terminal_is_frontmost = True

                parts = [p.strip() for p in output.replace(',,', ',').split(',') if p.strip()]
                if len(parts) >= 5:
                    window_id = parts[4]
                    self.current_terminal_id = window_id
                    self.last_terminal_id = window_id
                    self.terminal_window = {
                        'x': int(parts[0]),
                        'y': int(parts[1]),
                        'width': int(parts[2]),
                        'height': int(parts[3]),
                        'id': window_id
                    }
        except Exception:
            self.terminal_window = None
            self.current_terminal_id = None

    def track_terminal_position(self):
        """Continuously track terminal position"""
        if not self.is_dragging:
            if self.my_terminal_id:
                self.find_terminal_by_id(self.my_terminal_id)

                try:
                    result = subprocess.run([
                        'osascript',
                        '-e', 'tell application "System Events"',
                        '-e', 'set frontApp to name of first application process whose frontmost is true',
                        '-e', 'return frontApp',
                        '-e', 'end tell'
                    ], capture_output=True, text=True, timeout=1)
                    is_terminal_frontmost = result.returncode == 0 and result.stdout.strip() == "Terminal"
                    self.terminal_is_frontmost = is_terminal_frontmost and self.current_terminal_id == self.my_terminal_id
                except:
                    self.terminal_is_frontmost = False
            else:
                self.find_terminal_window()

            try:
                if self.terminal_is_frontmost:
                    self.root.attributes('-topmost', True)
                else:
                    self.root.attributes('-topmost', False)
            except Exception:
                pass

            if self.current_terminal_id != self.last_terminal_id:
                actual_recording = self.recorder.recording
                self.draw_button(recording=actual_recording)
                self.recording = actual_recording
                if self.current_terminal_id is not None:
                    self.last_terminal_id = self.current_terminal_id

            if self.terminal_window != self.last_terminal_position:
                self.position_window()
                self.last_terminal_position = self.terminal_window.copy() if self.terminal_window else None

        self.root.after(50, self.track_terminal_position)

    def position_window(self):
        """Position window at top-right of terminal"""
        self.root.update_idletasks()
        if self.terminal_window:
            x = self.terminal_window['x'] + self.terminal_window['width'] - 132 + 52
            y = self.terminal_window['y'] - 29
            self.root.geometry(f"+{x}+{y}")

    def draw_button(self, recording=False):
        """Draw the recording button"""
        self.canvas.delete("all")

        bg_color = '#2c2c2c' if self.is_dark_mode else '#e5e5e5'
        text_color = 'white' if self.is_dark_mode else '#333333'
        button_fill = 'white' if self.is_dark_mode else 'black'
        button_outline = 'black' if self.is_dark_mode else 'white'

        self.canvas.configure(bg=bg_color)

        if recording:
            label_text = "Rec"
            text_x = 20
        else:
            label_text = "RecCli"
            text_x = 25

        self.canvas.create_text(
            text_x, 15,
            text=label_text,
            fill=text_color,
            font=('Arial', 10, 'bold'),
            anchor='center'
        )

        if recording:
            # Square with stop icon
            self.button = self.canvas.create_rectangle(
                40, 5, 61, 26,
                fill=button_fill,
                outline=button_outline,
                width=1
            )
            self.canvas.create_rectangle(
                47, 12, 54, 19,
                fill='#ff4757',
                outline=''
            )
        else:
            # Circle with record icon
            self.button = self.canvas.create_oval(
                50, 3, 75, 28,
                fill=button_fill,
                outline=button_outline,
                width=1
            )
            self.canvas.create_oval(
                60, 13, 66, 19,
                fill='#e74c3c',
                outline=''
            )

    def start_recording(self):
        """Start recording"""
        success, result = self.recorder.start(terminal_id=self.my_terminal_id)
        if success:
            self.recording = True
            self.recording_start_time = datetime.now()
            self.draw_button(recording=True)
            self.show_notification("Recording started", "#27ae60")
        else:
            messagebox.showerror("Error", f"Failed to start: {result}")

    def stop_recording(self):
        """Stop recording"""
        success, result, duration = self.recorder.stop()
        if success:
            self.recording = False
            self.show_stopped_state()

            filename = Path(result).name
            self.show_notification(f"Saved: {filename}", "#27ae60")
        else:
            messagebox.showerror("Error", f"Failed to stop: {result}")

    def show_stopped_state(self):
        """Show 'Stopped' text briefly"""
        self.canvas.delete("all")
        bg_color = '#2c2c2c' if self.is_dark_mode else '#e5e5e5'
        text_color = 'white' if self.is_dark_mode else '#333333'

        self.canvas.configure(bg=bg_color)
        self.canvas.create_text(
            40, 15,
            text="Stopped",
            fill=text_color,
            font=('Arial', 10, 'bold'),
            anchor='center'
        )

        self.root.after(1500, lambda: self.draw_button(recording=False))

    def show_notification(self, message, color="#2c2c2c"):
        """Show temporary notification"""
        notif = tk.Toplevel(self.root)
        notif.overrideredirect(True)
        notif.attributes('-topmost', True)
        notif.geometry("250x40")

        x = self.root.winfo_x() - 90
        y = self.root.winfo_y() + 80
        notif.geometry(f"+{x}+{y}")

        label = tk.Label(
            notif,
            text=message,
            bg=color,
            fg='white',
            font=('Arial', 11),
            padx=15,
            pady=10
        )
        label.pack(fill=tk.BOTH, expand=True)

        notif.after(3000, notif.destroy)

    def show_stats(self):
        """Show recording statistics"""
        sessions_dir = Path.home() / '.reccli' / 'sessions'
        if sessions_dir.exists():
            sessions = list(sessions_dir.glob('*.devsession'))
            count = len(sessions)
            message = f"📊 RecCli v2 Stats\n\nTotal Sessions: {count}"
        else:
            message = "📊 RecCli v2 Stats\n\nNo sessions recorded yet"

        messagebox.showinfo("RecCli Stats", message)

    def open_sessions_folder(self):
        """Open sessions folder"""
        folder = Path.home() / '.reccli' / 'sessions'
        folder.mkdir(parents=True, exist_ok=True)
        if sys.platform == 'darwin':
            subprocess.run(['open', folder])
        elif sys.platform == 'linux':
            subprocess.run(['xdg-open', folder])
        elif sys.platform == 'win32':
            subprocess.run(['explorer', folder])

    def show_menu(self, event):
        """Show right-click menu"""
        self.menu.post(event.x_root, event.y_root)

    def on_press(self, event):
        """Start drag tracking"""
        self.start_pos = (event.x, event.y)
        self.is_dragging = False

    def on_drag(self, event):
        """Handle window dragging"""
        if self.start_pos:
            self.is_dragging = True
            x = self.root.winfo_pointerx() - self.start_pos[0]
            y = self.root.winfo_pointery() - self.start_pos[1]
            self.root.geometry(f"+{x}+{y}")

    def on_release(self, event):
        """Handle mouse button release"""
        if not self.is_dragging:
            actual_recording = self.recorder.recording
            if not actual_recording:
                self.start_recording()
            else:
                self.stop_recording()
        self.is_dragging = False
        self.start_pos = None

    def quit(self):
        """Quit application"""
        if self.recording:
            if messagebox.askyesno("Recording in progress", "Stop recording and quit?"):
                self.stop_recording()
            else:
                return
        self.root.quit()

    def run(self):
        """Run the GUI"""
        self.root.mainloop()


# Watcher functions for auto-launching GUI per terminal

def get_all_terminal_ids_including_minimized():
    """Get IDs of ALL Terminal windows, including minimized and across Spaces"""
    try:
        result = subprocess.run([
            'osascript',
            '-e', 'tell application "Terminal"',
            '-e', 'set windowIDs to {}',
            '-e', 'repeat with w in windows',
            '-e', 'set end of windowIDs to id of w',
            '-e', 'end repeat',
            '-e', 'return windowIDs',
            '-e', 'end tell'
        ], capture_output=True, text=True, timeout=5)

        if result.returncode == 0 and result.stdout.strip():
            ids = [id.strip() for id in result.stdout.strip().split(',') if id.strip()]
            return ids
        return []
    except Exception:
        return []


def watch_terminals():
    """Watch for new Terminal windows and auto-launch reccli instances"""
    import json
    import time
    from pathlib import Path

    print("👀 RecCli v2 watcher started")
    print("   Monitoring for new Terminal windows...")
    print("   Press Ctrl+C to stop")

    processes_file = Path("/tmp/reccli_v2_processes.json")
    gui_script = Path(__file__).parent.parent / "reccli-gui.py"

    if not gui_script.exists():
        print(f"❌ Error: GUI script not found at {gui_script}")
        return 1

    # Track which terminals we've already launched for
    tracked_terminals = set()

    # Load existing processes if any
    if processes_file.exists():
        try:
            with open(processes_file, 'r') as f:
                existing = json.load(f)
                tracked_terminals = set(existing.keys())
        except:
            pass

    try:
        while True:
            # Get ALL terminals (including minimized and across Spaces)
            all_terminals = set(get_all_terminal_ids_including_minimized())

            # Find new terminals (in all_terminals but not tracked)
            new_terminals = all_terminals - tracked_terminals

            # Find closed terminals (tracked but not in all_terminals)
            closed_terminals = tracked_terminals - all_terminals

            # Launch popups for new terminals
            for term_id in new_terminals:
                # Double-check: is there already a running process for this terminal?
                # This prevents race conditions during Space changes
                existing_pid = None
                if processes_file.exists():
                    try:
                        with open(processes_file, 'r') as f:
                            processes = json.load(f)
                            existing_pid = processes.get(term_id)
                    except:
                        pass

                # Check if existing process is still alive
                if existing_pid:
                    try:
                        os.kill(int(existing_pid), 0)  # Signal 0 = check if process exists
                        tracked_terminals.add(term_id)  # Add to tracking to prevent future attempts
                        continue  # Skip launching duplicate
                    except (OSError, ValueError):
                        # Process is dead, safe to launch new one
                        pass

                try:
                    proc = subprocess.Popen(
                        [sys.executable, str(gui_script), term_id],
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                        start_new_session=True
                    )
                    print(f"  ✓ Launched reccli for new terminal {term_id} (PID: {proc.pid})")
                    tracked_terminals.add(term_id)

                    # Update processes file
                    if processes_file.exists():
                        with open(processes_file, 'r') as f:
                            processes = json.load(f)
                    else:
                        processes = {}
                    processes[term_id] = proc.pid
                    with open(processes_file, 'w') as f:
                        json.dump(processes, f, indent=2)
                except Exception as e:
                    print(f"  ✗ Failed to launch for terminal {term_id}: {e}")

            # Clean up closed terminals from tracking
            if closed_terminals:
                tracked_terminals -= closed_terminals

                # Update processes file
                if processes_file.exists():
                    try:
                        with open(processes_file, 'r') as f:
                            processes = json.load(f)
                        for term_id in closed_terminals:
                            processes.pop(term_id, None)
                        with open(processes_file, 'w') as f:
                            json.dump(processes, f, indent=2)
                    except:
                        pass

            # Check every 2 seconds
            time.sleep(2)

    except KeyboardInterrupt:
        print("\n👋 Watcher stopped")
        return 0
