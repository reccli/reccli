#!/usr/bin/env python3
"""
reccli - One-click CLI recorder with floating button
Dead simple terminal recording. Just click and go.
"""

import os
import sys
import time
import json
import subprocess
import datetime
import shutil
from pathlib import Path
from typing import Dict, Tuple, Optional

# Debug logging to file
DEBUG_LOG = Path("/tmp/reccli_debug.log")
def debug_log(msg):
    with open(DEBUG_LOG, "a") as f:
        f.write(f"{datetime.datetime.now().strftime('%H:%M:%S.%f')[:-3]} - {msg}\n")
        f.flush()

try:
    import tkinter as tk
    from tkinter import ttk, messagebox
    HAS_GUI = True
except ImportError:
    HAS_GUI = False

# Import RecCli modules
sys.path.insert(0, str(Path(__file__).parent))
try:
    from src.ui import ExportDialog, SettingsDialog
    from src.export import format_duration
    HAS_EXPORT = True
except ImportError:
    HAS_EXPORT = False
    print("⚠️  Export modules not found. Basic recording only.")

# Configuration
VERSION = "1.0.0"

class ReccliConfig:
    """Manage configuration and stats"""

    def __init__(self):
        self.config_dir = Path.home() / '.reccli'
        self.config_file = self.config_dir / 'config.json'
        self.config_dir.mkdir(parents=True, exist_ok=True)
        self.config = self.load_config()

    def load_config(self) -> Dict:
        """Load or create configuration"""
        if self.config_file.exists():
            with open(self.config_file, 'r') as f:
                return json.load(f)

        # Create new config
        config = {
            'recordings_count': 0,
            'total_time_recorded': 0,
            'first_recording': None,
            'last_recording': None,
            'install_date': datetime.datetime.now().isoformat(),
            # Export settings
            'default_export_format': 'md',
            'default_save_location': str(Path.home() / 'Documents' / 'reccli_sessions'),
            # Recording settings
            'show_recording_indicator': True,
            'show_duration_timer': True,
            'auto_pause_on_idle': False
        }
        # Create default save location
        Path(config['default_save_location']).mkdir(parents=True, exist_ok=True)
        self.save_config(config)
        return config

    def save_config(self, config=None):
        """Save configuration"""
        if config:
            self.config = config
        with open(self.config_file, 'w') as f:
            json.dump(self.config, f, indent=2)

    def increment_stats(self, duration: float = 0):
        """Update usage statistics"""
        self.config['recordings_count'] += 1
        self.config['total_time_recorded'] += duration
        self.config['last_recording'] = datetime.datetime.now().isoformat()
        if not self.config['first_recording']:
            self.config['first_recording'] = self.config['last_recording']
        self.save_config()

class CLIRecorder:
    """Core recording functionality"""

    def __init__(self, output_dir=None):
        self.output_dir = Path(output_dir) if output_dir else Path.home() / '.reccli' / 'recordings'
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.recording = False
        self.process = None
        self.output_file = None
        self.start_time = None
        self.terminal_id = None  # Track which terminal window is being recorded

        # Check for recording tools
        self.has_asciinema = shutil.which('asciinema') is not None
        self.has_script = shutil.which('script') is not None

        if not self.has_asciinema and not self.has_script:
            print("⚠️  Warning: Install 'asciinema' for best recording quality")
            print("   Run: pip install asciinema")

    def start(self, filename=None, auto_launch_claude=False, tool_name="claude", terminal_id=None) -> Tuple[bool, str]:
        """Start recording session using asciinema (nested shell approach)"""
        if self.recording:
            return False, "Already recording"

        # Store terminal_id for targeting during stop
        self.terminal_id = terminal_id
        debug_log(f"CLIRecorder.start() - Stored terminal_id: {self.terminal_id}")

        # Generate filename
        if not filename:
            timestamp = datetime.datetime.now().strftime('%Y%m%d_%H%M%S')
            filename = f"session_{timestamp}"

        self.start_time = time.time()
        self.output_file = self.output_dir / f"{filename}.cast"

        if sys.platform == 'darwin':  # macOS
            # Use asciinema rec - creates a nested shell
            # Optionally auto-launch a tool inside the recording
            # Use just the filename, not full path - asciinema will create it in terminal's pwd
            simple_filename = f"{filename}.cast"
            cmd = f"asciinema rec {simple_filename}"

            # Store the simple filename so we can find it later
            self.temp_filename = simple_filename

            # Build AppleScript to activate terminal and send keystrokes
            if auto_launch_claude:
                # Start script, then immediately launch the selected tool
                script_text = f'''
                tell application "Terminal"
                    activate
                    delay 0.2
                end tell
                tell application "System Events"
                    tell process "Terminal"
                        keystroke "{cmd}"
                        keystroke return
                        delay 1.0
                        keystroke "{tool_name}"
                        keystroke return
                    end tell
                end tell
                '''
            else:
                # Just start script without launching anything
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
                result = subprocess.run(['osascript', '-e', script_text], check=True, capture_output=True, text=True)
                print(f"AppleScript result: stdout={result.stdout}, stderr={result.stderr}")
                self.recording = True
                return True, str(self.output_file)
            except Exception as e:
                print(f"AppleScript error: {e}")
                return False, f"Failed to start recording: {str(e)}"
        else:
            return False, "Currently only macOS is supported"

    def stop(self) -> Tuple[bool, str, float]:
        """Stop recording session by typing exit to exit nested shells"""
        if not self.recording:
            return False, "Not recording", 0

        duration = time.time() - self.start_time if self.start_time else 0

        if sys.platform == 'darwin':  # macOS
            # Send Ctrl+D to the SPECIFIC terminal window that's being recorded
            # This prevents Ctrl+D from being sent to other terminal windows
            debug_log(f"CLIRecorder.stop() - Using terminal_id: {self.terminal_id}")

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
                # Fallback to old behavior if terminal_id not available
                debug_log("WARNING: No terminal_id available, using frontmost window")
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
                subprocess.run(['osascript', '-e', script_text], check=True, capture_output=True)
                # Give asciinema time to finish writing and close
                time.sleep(1.0)

                # Update output_file to point to where the file actually is (home directory)
                # The export dialog will handle moving it to the user's chosen location
                self.output_file = Path.home() / self.temp_filename
                print(f"Recording saved to: {self.output_file}")
            except Exception as e:
                print(f"Warning: Failed to stop recording: {e}")

        self.recording = False
        self.terminal_id = None  # Clear terminal_id after stopping
        return True, str(self.output_file), duration

    def _get_linux_terminal_cmd(self, cmd):
        """Get appropriate terminal command for Linux"""
        terminals = [
            ['gnome-terminal', '--', 'bash', '-c', ' '.join(cmd)],
            ['konsole', '-e', 'bash', '-c', ' '.join(cmd)],
            ['xterm', '-e', 'bash', '-c', ' '.join(cmd)],
            ['terminator', '-x', 'bash', '-c', ' '.join(cmd)],
        ]

        for term_cmd in terminals:
            if shutil.which(term_cmd[0]):
                return term_cmd

        # Fallback
        return ['xterm', '-e', 'bash', '-c', ' '.join(cmd)]

class ReccliGUI:
    """Floating button GUI attached to terminal window"""

    def __init__(self, terminal_id=None):
        self.config = ReccliConfig()
        self.recorder = CLIRecorder()
        self.terminal_window = None
        self.last_terminal_position = None
        self.current_terminal_id = None  # Current active terminal window ID
        self.my_terminal_id = terminal_id  # The specific terminal this instance is attached to (never changes)
        self.last_terminal_id = None  # Track last terminal ID to detect changes
        self.terminal_is_frontmost = False  # Track if terminal is the frontmost app
        self.is_dark_mode = self._detect_dark_mode()  # Detect system appearance
        self.last_appearance_check = None  # Track last appearance check
        self.popup_hidden = False  # Track if popup is hidden due to terminal minimize
        print(f"DEBUG: Initialized with dark mode = {self.is_dark_mode}")

        # Create GUI
        self.root = tk.Tk()
        self.root.title("RecCli")  # Window title shows full brand name

        # Try different approach for macOS rounded corners
        try:
            # On macOS, this gives us rounded corners with close button
            # Style options: closeBox (close button), collapseBox (minimize), resizable
            self.root.tk.call('::tk::unsupported::MacWindowStyle', 'style', self.root._w, 'floating', 'closeBox')
        except:
            # Fallback to overrideredirect if not on macOS
            self.root.overrideredirect(True)

        self.root.attributes('-topmost', True)

        # Size: 80x30 for "RecCli", shrinks to 65x30 when recording shows "Rec"
        self.root.geometry("80x30")

        # Set root window background color
        # Dark mode: #2c2c2c (very dark), Light mode: #e5e5e5 (light gray)
        bg_color = '#2c2c2c' if self.is_dark_mode else '#e5e5e5'
        self.root.configure(bg=bg_color)
        print(f"DEBUG: Root bg_color = {bg_color} (is_dark_mode={self.is_dark_mode})")

        # Get terminal window position and attach to it
        if self.my_terminal_id:
            # Terminal ID specified - lock onto it immediately
            debug_log(f"ReccliGUI initialized with specified terminal ID: {self.my_terminal_id}")
            self.find_terminal_by_id(self.my_terminal_id)
        else:
            # No terminal ID specified - use frontmost terminal at launch
            self.find_terminal_window()
            self.my_terminal_id = self.current_terminal_id  # Lock onto this terminal forever
            debug_log(f"ReccliGUI initialized - locked to frontmost terminal ID: {self.my_terminal_id}")
        self.position_window()

        # Create canvas for text + button
        print(f"DEBUG: Canvas bg_color = {bg_color} (is_dark_mode={self.is_dark_mode})")
        self.canvas = tk.Canvas(
            self.root,
            width=80,
            height=30,
            highlightthickness=0,
            bg=bg_color
        )
        self.canvas.pack()

        # Draw initial button (circle when not recording)
        self.draw_button(recording=False)

        # Bind events
        self.canvas.bind("<ButtonPress-1>", self.on_press)
        self.canvas.bind("<B1-Motion>", self.on_drag)
        self.canvas.bind("<ButtonRelease-1>", self.on_release)
        self.canvas.bind("<Button-3>", self.show_menu)  # Right-click

        # Create right-click menu
        self.menu = tk.Menu(self.root, tearoff=0)
        self.menu.add_command(label="⚙️ Settings", command=self.show_settings)
        self.menu.add_command(label="📊 Stats", command=self.show_stats)
        self.menu.add_command(label="📁 Recordings", command=self.open_recordings_folder)
        self.menu.add_separator()
        self.menu.add_command(label="❌ Quit", command=self.quit)

        # Track state
        self.recording = False
        self.start_pos = None
        self.is_dragging = False
        self.target_terminal_id = None  # Track which terminal window to record
        self.recording_start_time = None  # Track when recording started
        self.tooltip = None  # Tooltip window

        # Start position tracking loop
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
            # Dark mode: command succeeds and contains "Dark"
            # Light mode: command fails (returncode != 0)
            return result.returncode == 0 and 'Dark' in result.stdout
        except:
            return False  # Default to light mode if detection fails

    def _get_terminal_window_index(self, target_id):
        """Get the z-order index (window stack position) of a terminal window"""
        try:
            result = subprocess.run([
                'osascript',
                '-e', 'tell application "Terminal"',
                '-e', f'set targetWindow to first window whose id is {target_id}',
                '-e', 'return index of targetWindow',
                '-e', 'end tell'
            ], capture_output=True, text=True, timeout=1)

            if result.returncode == 0 and result.stdout.strip():
                index = int(result.stdout.strip())
                debug_log(f"Terminal {target_id} has z-index: {index}")
                return index
            return None
        except Exception as e:
            debug_log(f"Error getting terminal z-index: {e}")
            return None

    def find_terminal_by_id(self, target_id):
        """Find a specific terminal window by its numeric ID"""
        try:
            # Query all Terminal windows and find the one matching target_id
            # Also check if it's visible and not minimized
            result = subprocess.run([
                'osascript',
                '-e', 'tell application "Terminal"',
                '-e', 'repeat with w in windows',
                '-e', f'if id of w is {target_id} then',
                '-e', 'set isVisible to visible of w',
                '-e', 'set isMini to miniaturized of w',
                '-e', 'if isVisible is false or isMini is true then',
                '-e', 'return "CLOSED_OR_MINIMIZED"',
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
                if output == "CLOSED_OR_MINIMIZED":
                    debug_log(f"Instance {self.my_terminal_id}: Terminal closed or minimized")
                    # If recording, just hide the popup, don't quit
                    if self.recorder.recording:
                        # Only hide if not already hidden
                        if not self.popup_hidden:
                            debug_log(f"Instance {self.my_terminal_id}: Recording active, hiding popup")
                            self.root.withdraw()  # Hide the popup window completely
                            self.popup_hidden = True
                        return
                    else:
                        # Not recording, safe to quit
                        debug_log(f"Instance {self.my_terminal_id}: Not recording, quitting")
                        self.quit()
                        return
                elif output != "NOT_FOUND":
                    # Terminal is visible and not minimized
                    # If popup was hidden, restore it
                    if self.popup_hidden:
                        debug_log(f"Instance {self.my_terminal_id}: Terminal restored, showing popup (recording={self.recorder.recording})")
                        self.root.deiconify()
                        self.popup_hidden = False
                        # Redraw button with correct recording state from recorder
                        self.draw_button(recording=self.recorder.recording)
                        # Sync GUI state with recorder
                        self.recording = self.recorder.recording
                        # Update last_terminal_id to prevent double-redraw in track_terminal_position
                        self.last_terminal_id = self.my_terminal_id

                    # Parse the window info - format is "x, ,, y, ,, width, ,, height, ,, id"
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
                        debug_log(f"Instance {self.my_terminal_id}: Found terminal at ({parts[0]}, {parts[1]})")
                        return
                else:
                    debug_log(f"Instance {self.my_terminal_id}: Terminal NOT_FOUND - quitting")
                    self.quit()
            # If we didn't find it, quit
        except Exception as e:
            debug_log(f"Instance {self.my_terminal_id}: Error finding terminal by ID: {e}")
            pass

    def find_terminal_window(self):
        """Find the active terminal window position using AppleScript"""
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
            debug_log(f"AppleScript result: returncode={result.returncode}, stdout='{result.stdout.strip()}', stderr='{result.stderr.strip()}'")
            if result.returncode == 0 and result.stdout.strip():
                output = result.stdout.strip()
                if output == "NOT_TERMINAL":
                    # Not focused on terminal - keep last_terminal_id!
                    self.terminal_window = None
                    self.current_terminal_id = None
                    self.terminal_is_frontmost = False
                    # Don't clear last_terminal_id - we need it when button is clicked
                    return
                else:
                    # Terminal is frontmost
                    self.terminal_is_frontmost = True

                # Parse output - format is "x, ,, y, ,, width, ,, height, ,, id"
                # Remove empty strings and extra commas
                parts = [p.strip() for p in output.replace(',,', ',').split(',') if p.strip()]
                debug_log(f"Parsed terminal window parts: {parts}")
                if len(parts) >= 5:
                    window_id = parts[4]  # Numeric terminal window ID
                    self.current_terminal_id = window_id
                    self.last_terminal_id = window_id  # Update last known ID
                    self.terminal_window = {
                        'x': int(parts[0]),
                        'y': int(parts[1]),
                        'width': int(parts[2]),
                        'height': int(parts[3]),
                        'id': window_id
                    }
                    debug_log(f"Terminal window found: {self.terminal_window}")
                else:
                    debug_log(f"Not enough parts in output: {len(parts)}")
                    self.terminal_window = None
                    self.current_terminal_id = None
            else:
                debug_log(f"AppleScript failed or no output")
                self.terminal_window = None
                self.current_terminal_id = None
        except Exception as e:
            debug_log(f"Exception in find_terminal_window: {e}")
            self.terminal_window = None
            self.current_terminal_id = None

    def track_terminal_position(self):
        """Continuously track terminal position and update button position"""
        # Don't update position if user is dragging
        if not self.is_dragging:
            # ALWAYS track only our specific terminal (the one we're attached to)
            if self.my_terminal_id:
                # Query all terminal windows and find the one matching my_terminal_id
                self.find_terminal_by_id(self.my_terminal_id)

                # Check if our specific terminal is frontmost
                try:
                    result = subprocess.run([
                        'osascript',
                        '-e', 'tell application "System Events"',
                        '-e', 'set frontApp to name of first application process whose frontmost is true',
                        '-e', 'return frontApp',
                        '-e', 'end tell'
                    ], capture_output=True, text=True, timeout=1)
                    is_terminal_frontmost = result.returncode == 0 and result.stdout.strip() == "Terminal"
                    # Check if OUR specific terminal is the frontmost one
                    self.terminal_is_frontmost = is_terminal_frontmost and self.current_terminal_id == self.my_terminal_id
                except:
                    self.terminal_is_frontmost = False
            else:
                # Fallback - no terminal ID set yet
                self.find_terminal_window()

            # Simple window layering - just topmost when our terminal is focused
            try:
                if self.terminal_is_frontmost:
                    # Our terminal is frontmost
                    self.root.attributes('-topmost', True)
                else:
                    # Our terminal is NOT frontmost
                    self.root.attributes('-topmost', False)
            except Exception as e:
                debug_log(f"Error managing window layering: {e}")

            # Only update button if terminal changed
            if self.current_terminal_id != self.last_terminal_id:
                # Redraw button with actual recorder state (source of truth)
                actual_recording = self.recorder.recording
                debug_log(f"Terminal changed to {self.current_terminal_id}, recording={actual_recording}")
                self.draw_button(recording=actual_recording)
                # Sync GUI state
                self.recording = actual_recording
                # Only update last_terminal_id if current is not None (preserve last known terminal)
                if self.current_terminal_id is not None:
                    self.last_terminal_id = self.current_terminal_id

            # Only update position if it changed
            if self.terminal_window != self.last_terminal_position:
                debug_log(f"Position changed, updating window position")
                self.position_window()
                self.last_terminal_position = self.terminal_window.copy() if self.terminal_window else None
        # Check position every 50ms for smooth tracking
        self.root.after(50, self.track_terminal_position)

    def position_window(self):
        """Position window in top-right corner of terminal or screen"""
        self.root.update_idletasks()

        if self.terminal_window:
            # Position at top-right of terminal window (moved 52px right, 29px up from top)
            x = self.terminal_window['x'] + self.terminal_window['width'] - 132 + 52  # 82 + 50px left
            y = self.terminal_window['y'] - 29  # 81px up from previous position (52 - (-29) = 81)
            self.root.geometry(f"+{x}+{y}")
        # If no terminal window, don't move - stay at last position

    def draw_button(self, recording=False):
        """Draw the recording button"""
        print(f"DEBUG: draw_button called with recording={recording}")
        self.canvas.delete("all")

        # Set colors based on appearance mode
        bg_color = '#2c2c2c' if self.is_dark_mode else '#e5e5e5'  # Dark gray or light gray
        text_color = 'white' if self.is_dark_mode else '#333333'
        button_fill = 'white' if self.is_dark_mode else 'black'  # White fill for dark mode, black for light
        button_outline = 'black' if self.is_dark_mode else 'white'  # Black outline for dark, white for light
        border_color = '#333333'

        # Update canvas background
        self.canvas.configure(bg=bg_color)

        # State-dependent text: "RecCli" when idle, "Rec" when recording
        if recording:
            # Minimal "Rec" during recording - cinematic and focused
            label_text = "Rec"
            text_x = 20
        else:
            # Full "RecCli" when idle - brand visibility
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
            # Square with border when recording (positioned for "Rec" text - shorter)
            self.button = self.canvas.create_rectangle(
                40, 5, 61, 26,
                fill=button_fill,
                outline=button_outline,
                width=1
            )
            # Red square in center (stop icon)
            self.canvas.create_rectangle(
                47, 12, 54, 19,
                fill='#ff4757',
                outline=''
            )
        else:
            # Circle with border when ready (positioned for "RecCli" text)
            self.button = self.canvas.create_oval(
                50, 3, 75, 28,
                fill=button_fill,
                outline=button_outline,
                width=1
            )
            # Red dot in center (record icon) - 6x6 circle
            self.canvas.create_oval(
                60, 13, 66, 19,
                fill='#e74c3c',
                outline=''
            )

    def _check_for_active_tools(self):
        """Check if Claude or Codex is already running in this specific terminal"""
        if not self.my_terminal_id:
            return False

        try:
            # Get the TTY/shell info for this terminal window
            # This is a simplification - ideally we'd check the process tree of the specific terminal
            # For now, we'll just check if there are ANY claude/codex processes
            # This needs AppleScript to get the tab info for the specific terminal window

            script = f'''
            tell application "Terminal"
                set targetWindow to first window whose id is {self.my_terminal_id}
                set tabList to every tab of targetWindow
                repeat with t in tabList
                    set processes to processes of t
                    repeat with p in processes
                        if p contains "claude" or p contains "codex" then
                            return "FOUND"
                        end if
                    end repeat
                end repeat
                return "NOT_FOUND"
            end tell
            '''

            result = subprocess.run(
                ['osascript', '-e', script],
                capture_output=True,
                text=True,
                timeout=2
            )

            return result.returncode == 0 and "FOUND" in result.stdout
        except Exception as e:
            debug_log(f"Error checking for active tools: {e}")
            return False

    def show_tool_warning(self):
        """Show warning tooltip when trying to record with tools already running"""
        # Create tooltip window
        tooltip = tk.Toplevel(self.root)
        tooltip.overrideredirect(True)
        tooltip.attributes('-topmost', True)

        # Position near the button
        x = self.root.winfo_x() - 50
        y = self.root.winfo_y() + 35
        tooltip.geometry(f"+{x}+{y}")

        # Style based on dark mode
        bg_color = '#2c2c2c' if self.is_dark_mode else '#f0f0f0'
        fg_color = 'white' if self.is_dark_mode else '#333333'

        label = tk.Label(
            tooltip,
            text="⚠️ Start RecCli before launching tools",
            bg=bg_color,
            fg=fg_color,
            font=('Arial', 10),
            padx=15,
            pady=10,
            justify='center'
        )
        label.pack()

        # Auto-close after 3 seconds
        tooltip.after(3000, tooltip.destroy)

    def start_recording(self):
        """Start recording"""
        # Check if tools are already running
        if self._check_for_active_tools():
            self.show_tool_warning()
            return

        # Always use our specific terminal ID (the one this instance is attached to)
        self.target_terminal_id = self.my_terminal_id
        debug_log(f"Start recording - target_terminal_id set to: {self.target_terminal_id} (my_terminal_id={self.my_terminal_id})")

        try:
            # Ask user which tool to launch
            dialog = tk.Toplevel(self.root)
            dialog.title("Launch Tool")
            dialog.geometry("300x250")
            dialog.resizable(False, False)

            # Center on screen
            dialog.update_idletasks()
            x = (dialog.winfo_screenwidth() // 2) - (150)
            y = (dialog.winfo_screenheight() // 2) - (125)
            dialog.geometry(f"+{x}+{y}")

            # Make modal
            dialog.transient(self.root)
            dialog.grab_set()
            dialog.focus_force()
        except Exception as e:
            print(f"Error creating dialog: {e}")
            messagebox.showerror("Error", f"Failed to create dialog: {e}")
            return

        selected_tool = tk.StringVar(value="claude")

        ttk.Label(
            dialog,
            text="Which tool would you like to launch?",
            font=('Arial', 11),
            padding=20
        ).pack()

        ttk.Radiobutton(
            dialog,
            text="Claude Code",
            variable=selected_tool,
            value="claude"
        ).pack(anchor=tk.W, padx=40, pady=5)

        ttk.Radiobutton(
            dialog,
            text="Codex CLI",
            variable=selected_tool,
            value="codex"
        ).pack(anchor=tk.W, padx=40, pady=5)

        ttk.Radiobutton(
            dialog,
            text="Just record (no tool)",
            variable=selected_tool,
            value="none"
        ).pack(anchor=tk.W, padx=40, pady=5)

        def start_with_tool():
            tool = selected_tool.get()
            dialog.destroy()

            if tool == "none":
                success, result = self.recorder.start(auto_launch_claude=False, terminal_id=self.target_terminal_id)
                notification = "Recording started"
            else:
                success, result = self.recorder.start(auto_launch_claude=True, tool_name=tool, terminal_id=self.target_terminal_id)
                notification = f"Recording started - {tool} launching"

            if success:
                self.recording = True
                self.recording_start_time = datetime.datetime.now()
                debug_log(f"!!! RECORDING STARTED: terminal_id={self.target_terminal_id}, recording={self.recording}")
                self.draw_button(recording=True)
                self.show_notification(notification, "#27ae60")
            else:
                messagebox.showerror("Error", f"Failed to start: {result}")

        def cancel():
            dialog.destroy()

        button_frame = ttk.Frame(dialog)
        button_frame.pack(side=tk.BOTTOM, pady=20)

        ttk.Button(button_frame, text="Cancel", command=cancel, width=10).pack(side=tk.LEFT, padx=5)
        ttk.Button(button_frame, text="Start", command=start_with_tool, width=10).pack(side=tk.RIGHT, padx=5)

        dialog.wait_window()

    def stop_recording(self):
        """Stop recording"""
        success, result, duration = self.recorder.stop()
        if success:
            self.recording = False
            print(f"DEBUG: Recording stopped, showing 'Stopped' state")

            # Show "Stopped" temporarily
            self.show_stopped_state()

            # Calculate duration from timestamps
            recorded_duration = duration  # Use duration from recorder

            # Update stats
            self.config.increment_stats(duration)

            # Show export dialog if available
            if HAS_EXPORT:
                self.show_export_dialog(Path(result), recorded_duration)
            else:
                # Just show notification
                filename = Path(result).name
                self.show_notification(f"Saved: {filename}", "#27ae60")
        else:
            messagebox.showerror("Error", f"Failed to stop: {result}")

    def show_stopped_state(self):
        """Show 'Stopped' text briefly, then revert to 'RecCli'"""
        # Draw stopped state
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

        # Revert to normal after 1.5 seconds
        self.root.after(1500, lambda: self.draw_button(recording=False))

    def show_export_dialog(self, session_file: Path, duration_seconds: float):
        """
        Show export dialog after recording stops

        Args:
            session_file: Path to recorded .cast file
            duration_seconds: Duration of recording in seconds
        """
        # Prepare metadata
        metadata = {
            'session_id': session_file.stem,
            'duration': format_duration(duration_seconds),
            'duration_seconds': duration_seconds,
            'timestamp': datetime.datetime.now().isoformat()
        }

        # Show dialog
        dialog = ExportDialog(self.root, session_file, metadata, self.config.config)
        result = dialog.show()

        if result:
            # Successfully exported
            filename = result['output_file'].name
            self.show_notification(f"Exported: {filename}", "#27ae60")
        else:
            # Cancelled - session still saved as .cast
            self.show_notification(f"Recording saved (not exported)", "#f39c12")

    def show_notification(self, message, color="#2c2c2c"):
        """Show a temporary notification"""
        notif = tk.Toplevel(self.root)
        notif.overrideredirect(True)
        notif.attributes('-topmost', True)
        notif.geometry("250x40")

        # Position below button
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

        # Auto-close after 3 seconds
        notif.after(3000, notif.destroy)

    def show_settings(self):
        """Show settings dialog"""
        if HAS_EXPORT:
            from src.ui import SettingsDialog
            dialog = SettingsDialog(self.root, self.config.config, self.config.save_config)
            dialog.show()
        else:
            messagebox.showinfo("Settings", "Settings module not available")

    def show_stats(self):
        """Show recording statistics"""
        stats = self.config.config
        recordings = stats.get('recordings_count', 0)
        time_recorded = stats.get('total_time_recorded', 0)
        hours = time_recorded / 3600

        first = stats.get('first_recording', 'Never')
        if first != 'Never':
            first = first[:10]

        last = stats.get('last_recording', 'Never')
        if last != 'Never':
            last = last[:10]

        message = f"""📊 Your reccli Stats

Recordings: {recordings}
Time Saved: {hours:.1f} hours
First Recording: {first}
Last Recording: {last}

Share your stats on X!"""

        messagebox.showinfo("reccli Stats", message)

    def open_recordings_folder(self):
        """Open the recordings folder"""
        folder = Path.home() / '.reccli' / 'recordings'
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
        # Only trigger click if we weren't dragging
        if not self.is_dragging:
            # Check actual recorder state (source of truth)
            actual_recording = self.recorder.recording
            print(f"Button clicked! GUI recording={self.recording}, Recorder recording={actual_recording}")
            if not actual_recording:
                self.start_recording()
            else:
                self.stop_recording()
        # Reset dragging state
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

def get_all_terminal_ids():
    """Get IDs of all visible, non-minimized Terminal windows"""
    try:
        result = subprocess.run([
            'osascript',
            '-e', 'tell application "Terminal"',
            '-e', 'set windowIDs to {}',
            '-e', 'repeat with w in windows',
            '-e', 'if visible of w is true and miniaturized of w is false then',
            '-e', 'set end of windowIDs to id of w',
            '-e', 'end if',
            '-e', 'end repeat',
            '-e', 'return windowIDs',
            '-e', 'end tell'
        ], capture_output=True, text=True, timeout=5)

        if result.returncode == 0 and result.stdout.strip():
            # Parse comma-separated IDs
            ids = [id.strip() for id in result.stdout.strip().split(',') if id.strip()]
            debug_log(f"Found visible terminal IDs: {ids}")
            return ids
        return []
    except Exception as e:
        debug_log(f"Error getting terminal IDs: {e}")
        return []

def get_all_terminal_ids_including_minimized():
    """Get IDs of ALL Terminal windows, including minimized ones"""
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
            # Parse comma-separated IDs
            ids = [id.strip() for id in result.stdout.strip().split(',') if id.strip()]
            return ids
        return []
    except Exception as e:
        debug_log(f"Error getting all terminal IDs: {e}")
        return []

def launch_all_terminals():
    """Launch one reccli instance for each open terminal window"""
    terminal_ids = get_all_terminal_ids()

    if not terminal_ids:
        print("❌ No Terminal windows found")
        return

    print(f"📺 Found {len(terminal_ids)} Terminal windows")
    print(f"🚀 Launching reccli for each terminal...")

    # Store process info in a file for tracking
    processes_file = Path("/tmp/reccli_processes.json")
    processes = {}

    script_path = Path(__file__).resolve()

    for term_id in terminal_ids:
        try:
            # Launch reccli in background for this specific terminal
            # Use --terminal-id to specify which terminal to attach to
            proc = subprocess.Popen(
                [sys.executable, str(script_path), 'gui', '--terminal-id', term_id],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True
            )
            processes[term_id] = proc.pid
            print(f"  ✓ Launched for terminal {term_id} (PID: {proc.pid})")
        except Exception as e:
            print(f"  ✗ Failed to launch for terminal {term_id}: {e}")

    # Save process info
    with open(processes_file, 'w') as f:
        json.dump(processes, f, indent=2)

    print(f"\n✅ Launched {len(processes)} reccli instances")
    print(f"📝 Process info saved to {processes_file}")

def killall_reccli():
    """Kill all running reccli instances"""
    try:
        result = subprocess.run(['pkill', '-f', 'reccli.py gui'], capture_output=True, text=True)
        print("✅ All reccli instances stopped")

        # Clean up tracking file
        processes_file = Path("/tmp/reccli_processes.json")
        if processes_file.exists():
            processes_file.unlink()
            print("📝 Cleaned up process tracking file")
    except Exception as e:
        print(f"❌ Error stopping instances: {e}")

def watch_terminals():
    """Watch for new Terminal windows and auto-launch reccli instances"""
    print("👀 RecCli watcher started")
    print("   Monitoring for new Terminal windows...")
    print("   Press Ctrl+C to stop")

    processes_file = Path("/tmp/reccli_processes.json")
    script_path = Path(__file__).resolve()

    # Track which terminals we've already launched for
    tracked_terminals = set()

    # Load existing processes if any
    if processes_file.exists():
        try:
            with open(processes_file, 'r') as f:
                existing = json.load(f)
                tracked_terminals = set(existing.keys())
                debug_log(f"Watcher: Loaded {len(tracked_terminals)} existing terminals: {tracked_terminals}")
        except:
            pass

    try:
        while True:
            # Get current visible terminals (for launching new popups)
            visible_terminals = set(get_all_terminal_ids())

            # Get ALL terminals including minimized (to detect actual closes)
            all_terminals = set(get_all_terminal_ids_including_minimized())

            # Find new terminals (visible but not tracked)
            new_terminals = visible_terminals - tracked_terminals

            # Find closed terminals (tracked but not in ALL terminals, including minimized)
            # This way we only remove from tracking when terminal is actually closed, not just minimized
            closed_terminals = tracked_terminals - all_terminals

            # Launch popups for new terminals
            for term_id in new_terminals:
                try:
                    proc = subprocess.Popen(
                        [sys.executable, str(script_path), 'gui', '--terminal-id', term_id],
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                        start_new_session=True
                    )
                    print(f"  ✓ Launched reccli for new terminal {term_id} (PID: {proc.pid})")
                    debug_log(f"Watcher: Launched for new terminal {term_id}, PID: {proc.pid}")
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
                    debug_log(f"Watcher: Error launching for {term_id}: {e}")

            # Clean up closed terminals from tracking
            if closed_terminals:
                debug_log(f"Watcher: Terminals closed: {closed_terminals}")
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
        debug_log("Watcher: Stopped by user")

def main():
    """Main entry point"""
    import argparse

    parser = argparse.ArgumentParser(description='reccli - One-click CLI recorder')
    parser.add_argument('command', nargs='?', default='watch',
                       choices=['gui', 'launch', 'watch', 'killall', 'start', 'stop', 'status'],
                       help='Command to execute (default: watch)')
    parser.add_argument('--terminal-id', type=str, help='Specific terminal ID to attach to (internal use)')
    parser.add_argument('--version', action='version', version=f'reccli {VERSION}')

    args = parser.parse_args()

    if args.command == 'watch':
        # Default command: watch for new terminals and auto-launch
        watch_terminals()

    elif args.command == 'launch':
        # Launch one instance per current terminal
        launch_all_terminals()

    elif args.command == 'killall':
        # Kill all reccli instances
        killall_reccli()

    elif args.command == 'gui':
        if not HAS_GUI:
            print("❌ GUI not available. Install tkinter:")
            print("   Ubuntu/Debian: sudo apt-get install python3-tk")
            print("   macOS: brew install python-tk")
            sys.exit(1)

        # Pass terminal_id if specified (from launcher)
        app = ReccliGUI(terminal_id=args.terminal_id)
        app.run()

    elif args.command == 'status':
        config = ReccliConfig()
        stats = config.config
        print("📊 reccli Stats")
        print(f"   Recordings: {stats.get('recordings_count', 0)}")
        print(f"   Time saved: {stats.get('total_time_recorded', 0)/3600:.1f} hours")
        print(f"   Recordings folder: ~/.reccli/recordings")

    else:
        print(f"Command '{args.command}' not implemented yet")
        print("Use 'reccli gui' to start the floating button")

if __name__ == '__main__':
    main()
