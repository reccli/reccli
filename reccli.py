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
            # Send Ctrl+D twice: once to exit whatever is running (e.g., claude)
            # and once to exit the asciinema session
            # Activate Terminal first to ensure we're targeting the right window
            script_text = '''
            tell application "Terminal"
                activate
                delay 0.2
            end tell
            tell application "System Events"
                tell process "Terminal"
                    keystroke "d" using control down
                    delay 0.3
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

    def __init__(self):
        self.config = ReccliConfig()
        self.recorder = CLIRecorder()
        self.update_timer = None
        self.terminal_window = None
        self.last_terminal_position = None
        self.terminal_recording_states = {}  # Track recording state per terminal window ID
        self.current_terminal_id = None  # Current active terminal window ID
        self.last_terminal_id = None  # Track last terminal ID to detect changes
        self.terminal_is_frontmost = False  # Track if terminal is the frontmost app

        # Create GUI
        self.root = tk.Tk()
        self.root.title("reccli")
        self.root.overrideredirect(True)  # Remove window decorations
        self.root.attributes('-topmost', True)

        # Make window wider to fit "RecCli" text + button (80x35)
        self.root.geometry("80x35")

        # Try to make window transparent
        try:
            self.root.attributes('-alpha', 0.95)
        except:
            pass

        # Get terminal window position and attach to it
        self.find_terminal_window()
        self.position_window()

        # Create canvas for "RecCli" text + button
        self.canvas = tk.Canvas(
            self.root,
            width=80,
            height=35,
            highlightthickness=0,
            bg='#2c2c2c'
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
        self.duration = 0
        self.is_dragging = False
        self.target_terminal_id = None  # Track which terminal window to record

        # Start position tracking loop
        self.track_terminal_position()

    def find_terminal_window(self):
        """Find the active terminal window position using AppleScript"""
        try:
            script = '''
            tell application "System Events"
                set frontApp to name of first application process whose frontmost is true
                if frontApp is "Terminal" or frontApp is "iTerm2" then
                    tell process frontApp
                        set frontWindow to window 1
                        set windowPosition to position of frontWindow
                        set windowSize to size of frontWindow
                        set windowName to name of frontWindow
                        return (item 1 of windowPosition) & "," & (item 2 of windowPosition) & "," & (item 1 of windowSize) & "," & (item 2 of windowSize) & "," & windowName
                    end tell
                else
                    return "NOT_TERMINAL"
                end if
            end tell
            '''
            result = subprocess.run(['osascript', '-e', script], capture_output=True, text=True, timeout=2)
            if result.returncode == 0 and result.stdout.strip():
                output = result.stdout.strip()
                if output == "NOT_TERMINAL":
                    # Not focused on terminal
                    self.terminal_window = None
                    self.current_terminal_id = None
                    self.terminal_is_frontmost = False
                    return
                else:
                    # Terminal is frontmost
                    self.terminal_is_frontmost = True

                # Clean up output - remove extra spaces and split by comma
                cleaned = output.replace(' ', '')
                parts = [p.strip() for p in cleaned.split(',') if p.strip()]
                if len(parts) >= 5:
                    window_id = parts[4]  # Use window name as unique ID
                    self.current_terminal_id = window_id
                    self.terminal_window = {
                        'x': int(parts[0]),
                        'y': int(parts[1]),
                        'width': int(parts[2]),
                        'height': int(parts[3]),
                        'id': window_id
                    }
                    # Initialize recording state for this terminal if not exists
                    if window_id not in self.terminal_recording_states:
                        self.terminal_recording_states[window_id] = False
            else:
                self.terminal_window = None
                self.current_terminal_id = None
        except Exception as e:
            # Silently fail
            self.terminal_window = None
            self.current_terminal_id = None

    def track_terminal_position(self):
        """Continuously track terminal position and update button position"""
        # Don't update position if user is dragging
        if not self.is_dragging:
            self.find_terminal_window()

            # Toggle topmost based on terminal focus
            try:
                self.root.attributes('-topmost', self.terminal_is_frontmost)
            except:
                pass

            # Only update button if terminal changed
            if self.current_terminal_id != self.last_terminal_id:
                is_recording = self.terminal_recording_states.get(self.current_terminal_id, False)
                self.draw_button(recording=is_recording)
                self.last_terminal_id = self.current_terminal_id

            # Only update position if it changed
            if self.terminal_window != self.last_terminal_position:
                self.position_window()
                self.last_terminal_position = self.terminal_window.copy() if self.terminal_window else None
        # Check position every 50ms for smooth tracking
        self.root.after(50, self.track_terminal_position)

    def position_window(self):
        """Position window in top-right corner of terminal or screen"""
        self.root.update_idletasks()

        if self.terminal_window:
            # Position at top-right of terminal window (moved 38px right, 52px down)
            x = self.terminal_window['x'] + self.terminal_window['width'] - 132 + 38  # 82 + 50px left - 14px right
            y = self.terminal_window['y'] + 28 + 24  # Moved down 52px total
        else:
            # Fallback to screen top-right
            screen_width = self.root.winfo_screenwidth()
            x = screen_width - 132 + 38  # 82 + 50px left - 14px right
            y = 28 + 24  # Moved down 52px total

        self.root.geometry(f"+{x}+{y}")

    def draw_button(self, recording=False):
        """Draw the recording button"""
        self.canvas.delete("all")

        # Draw "RecCli" text on the left, top-aligned
        self.canvas.create_text(
            25, 10,
            text="RecCli",
            fill='white',
            font=('Arial', 10, 'bold'),
            anchor='n'
        )

        if recording:
            # Black square with white border when recording (shifted right for text)
            self.button = self.canvas.create_rectangle(
                52, 7, 73, 28,
                fill='black',
                outline='white',
                width=1
            )
            # Red square in center (stop icon)
            self.canvas.create_rectangle(
                59, 15, 66, 22,
                fill='#ff4757',
                outline=''
            )
        else:
            # Black circle with white border when ready (shifted right for text)
            self.button = self.canvas.create_oval(
                50, 5, 75, 30,
                fill='black',
                outline='white',
                width=1
            )
            # Red dot in center (record icon)
            self.canvas.create_oval(
                59, 15, 66, 22,
                fill='#e74c3c',
                outline=''
            )

    def start_recording(self):
        """Start recording"""
        # Capture which terminal window is currently active BEFORE showing dialog
        try:
            script = '''
            tell application "System Events"
                set frontApp to name of first application process whose frontmost is true
                if frontApp is "Terminal" then
                    tell application "Terminal"
                        return id of front window
                    end tell
                else if frontApp is "iTerm2" then
                    tell application "iTerm"
                        return id of current window
                    end tell
                end if
            end tell
            '''
            result = subprocess.run(['osascript', '-e', script], capture_output=True, text=True, timeout=2)
            if result.returncode == 0 and result.stdout.strip():
                self.target_terminal_id = result.stdout.strip()
                print(f"Captured terminal ID: {self.target_terminal_id}")
        except Exception as e:
            print(f"Failed to capture terminal ID: {e}")
            self.target_terminal_id = None

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
                # Set recording state for current terminal
                if self.current_terminal_id:
                    self.terminal_recording_states[self.current_terminal_id] = True
                self.draw_button(recording=True)
                self.update_duration()
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
            # Clear recording state for current terminal
            if self.current_terminal_id:
                self.terminal_recording_states[self.current_terminal_id] = False
            self.draw_button(recording=False)

            # Stop duration timer
            if self.update_timer:
                self.root.after_cancel(self.update_timer)
                recorded_duration = self.duration
                self.duration = 0

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

    def update_duration(self):
        """Update recording duration"""
        if self.recording:
            self.duration += 1
            self.draw_button(recording=True)
            self.update_timer = self.root.after(1000, self.update_duration)

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
            # Check recording state for current terminal
            is_recording = self.terminal_recording_states.get(self.current_terminal_id, False)
            print(f"Button clicked! Recording={is_recording} Terminal={self.current_terminal_id}")
            if not is_recording:
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

def main():
    """Main entry point"""
    import argparse

    parser = argparse.ArgumentParser(description='reccli - One-click CLI recorder')
    parser.add_argument('command', nargs='?', default='gui',
                       choices=['gui', 'start', 'stop', 'status'],
                       help='Command to execute')
    parser.add_argument('--version', action='version', version=f'reccli {VERSION}')

    args = parser.parse_args()

    if args.command == 'gui':
        if not HAS_GUI:
            print("❌ GUI not available. Install tkinter:")
            print("   Ubuntu/Debian: sudo apt-get install python3-tk")
            print("   macOS: brew install python-tk")
            sys.exit(1)

        app = ReccliGUI()
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
