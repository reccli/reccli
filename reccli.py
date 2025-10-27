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
from typing import Dict, Tuple

try:
    import tkinter as tk
    from tkinter import messagebox
    HAS_GUI = True
except ImportError:
    HAS_GUI = False

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
            'install_date': datetime.datetime.now().isoformat()
        }
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

    def start(self, filename=None) -> Tuple[bool, str]:
        """Start recording session"""
        if self.recording:
            return False, "Already recording"

        # Generate filename
        if not filename:
            timestamp = datetime.datetime.now().strftime('%Y%m%d_%H%M%S')
            filename = f"session_{timestamp}"

        self.start_time = time.time()

        if self.has_asciinema:
            # Use asciinema for best compatibility
            self.output_file = self.output_dir / f"{filename}.cast"
            cmd = ['asciinema', 'rec', '--quiet', '--overwrite', str(self.output_file)]

            # Start in new terminal
            if sys.platform == 'darwin':  # macOS
                terminal_cmd = ['osascript', '-e', f'tell app "Terminal" to do script "cd {os.getcwd()} && {" ".join(cmd)}"']
            elif sys.platform == 'linux':
                terminal_cmd = self._get_linux_terminal_cmd(cmd)
            else:
                return False, "Platform not supported"

            try:
                subprocess.Popen(terminal_cmd)
                self.recording = True
                return True, str(self.output_file)
            except Exception as e:
                return False, str(e)

        elif self.has_script:
            # Fallback to script command
            self.output_file = self.output_dir / f"{filename}.log"
            if sys.platform == 'darwin':  # macOS
                cmd = ['script', '-q', str(self.output_file)]
            else:  # Linux
                cmd = ['script', '-q', '-f', str(self.output_file)]

            try:
                self.process = subprocess.Popen(cmd)
                self.recording = True
                return True, str(self.output_file)
            except Exception as e:
                return False, str(e)

        return False, "No recording tool available"

    def stop(self) -> Tuple[bool, str, float]:
        """Stop recording session"""
        if not self.recording:
            return False, "Not recording", 0

        duration = time.time() - self.start_time if self.start_time else 0

        if self.process:
            self.process.terminate()
            try:
                self.process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                self.process.kill()

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
    """Floating button GUI"""

    def __init__(self):
        self.config = ReccliConfig()
        self.recorder = CLIRecorder()
        self.update_timer = None

        # Create GUI
        self.root = tk.Tk()
        self.root.title("reccli")
        self.root.overrideredirect(True)  # Remove window decorations
        self.root.attributes('-topmost', True)

        # Make window small and round
        self.root.geometry("70x70")

        # Try to make window transparent
        try:
            self.root.attributes('-alpha', 0.95)
        except:
            pass

        # Position in top-right corner
        self.position_window()

        # Create canvas for circular button
        self.canvas = tk.Canvas(
            self.root,
            width=70,
            height=70,
            highlightthickness=0,
            bg='#2c2c2c'
        )
        self.canvas.pack()

        # Draw initial button (circle when not recording)
        self.draw_button(recording=False)

        # Bind events
        self.canvas.bind("<Button-1>", self.on_click)
        self.canvas.bind("<B1-Motion>", self.on_drag)
        self.canvas.bind("<ButtonPress-1>", self.on_press)
        self.canvas.bind("<Button-3>", self.show_menu)  # Right-click

        # Create right-click menu
        self.menu = tk.Menu(self.root, tearoff=0)
        self.menu.add_command(label="📊 Stats", command=self.show_stats)
        self.menu.add_command(label="📁 Recordings", command=self.open_recordings_folder)
        self.menu.add_separator()
        self.menu.add_command(label="❌ Quit", command=self.quit)

        # Track state
        self.recording = False
        self.start_pos = None
        self.duration = 0

    def position_window(self):
        """Position window in top-right corner"""
        self.root.update_idletasks()
        screen_width = self.root.winfo_screenwidth()
        x = screen_width - 100
        y = 30
        self.root.geometry(f"+{x}+{y}")

    def draw_button(self, recording=False):
        """Draw the recording button"""
        self.canvas.delete("all")

        if recording:
            # Red square when recording
            self.button = self.canvas.create_rectangle(
                15, 15, 55, 55,
                fill='#ff4757',
                outline='#ff6348',
                width=2
            )
            # White square in center (stop icon)
            self.canvas.create_rectangle(
                28, 28, 42, 42,
                fill='white',
                outline=''
            )
        else:
            # Green circle when ready
            self.button = self.canvas.create_oval(
                10, 10, 60, 60,
                fill='#27ae60',
                outline='#2ecc71',
                width=2
            )
            # Red dot in center (record icon)
            self.canvas.create_oval(
                28, 28, 42, 42,
                fill='#e74c3c',
                outline=''
            )

        # Duration text
        if recording and self.duration > 0:
            mins = int(self.duration // 60)
            secs = int(self.duration % 60)
            self.canvas.create_text(
                35, 65,
                text=f"{mins:02d}:{secs:02d}",
                fill='white',
                font=('Arial', 9, 'bold')
            )

    def on_click(self, event):
        """Handle button click"""
        if not self.recording:
            self.start_recording()
        else:
            self.stop_recording()

    def start_recording(self):
        """Start recording"""
        success, result = self.recorder.start()
        if success:
            self.recording = True
            self.draw_button(recording=True)
            self.update_duration()
            self.show_notification("Recording started", "#27ae60")
        else:
            messagebox.showerror("Error", f"Failed to start: {result}")

    def stop_recording(self):
        """Stop recording"""
        success, result, duration = self.recorder.stop()
        if success:
            self.recording = False
            self.draw_button(recording=False)

            # Stop duration timer
            if self.update_timer:
                self.root.after_cancel(self.update_timer)
                self.duration = 0

            # Update stats
            self.config.increment_stats(duration)

            # Show notification
            filename = Path(result).name
            self.show_notification(f"Saved: {filename}", "#27ae60")
        else:
            messagebox.showerror("Error", f"Failed to stop: {result}")

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

    def on_drag(self, event):
        """Handle window dragging"""
        if self.start_pos:
            x = self.root.winfo_pointerx() - self.start_pos[0]
            y = self.root.winfo_pointery() - self.start_pos[1]
            self.root.geometry(f"+{x}+{y}")

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
