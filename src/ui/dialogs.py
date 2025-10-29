"""
UI Dialogs for RecCli
Export dialog and settings dialog
"""

import tkinter as tk
from tkinter import ttk, filedialog, messagebox
from pathlib import Path
from typing import Optional, Dict, Callable
import sys

# Add parent to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from src.export import SessionExporter, format_duration


class ExportDialog:
    """Export session dialog with format selection"""

    def __init__(self, parent, session_file: Path, metadata: Dict, config: Dict):
        """
        Initialize export dialog

        Args:
            parent: Parent tkinter window
            session_file: Path to recorded .cast file
            metadata: Session metadata (session_id, duration, etc.)
            config: User config (default_format, default_location, etc.)
        """
        self.parent = parent
        self.session_file = session_file
        self.metadata = metadata
        self.config = config
        self.result = None

        # Create dialog window
        self.dialog = tk.Toplevel(parent)
        self.dialog.title("Export Session")
        self.dialog.geometry("500x600")
        self.dialog.resizable(False, False)

        # Center on screen
        self.dialog.update_idletasks()
        x = (self.dialog.winfo_screenwidth() // 2) - (500 // 2)
        y = (self.dialog.winfo_screenheight() // 2) - (600 // 2)
        self.dialog.geometry(f"+{x}+{y}")

        # Make modal
        self.dialog.transient(parent)
        self.dialog.grab_set()

        # Build UI
        self._build_ui()

    def _build_ui(self):
        """Build the dialog UI"""
        # Buttons FIRST - pack at bottom
        button_frame = ttk.Frame(self.dialog)
        button_frame.pack(side=tk.BOTTOM, fill=tk.X, padx=20, pady=20)

        ttk.Button(
            button_frame,
            text="Cancel",
            command=self._cancel,
            width=15
        ).pack(side=tk.LEFT)

        ttk.Button(
            button_frame,
            text="Export",
            command=self._export,
            width=15
        ).pack(side=tk.RIGHT)

        # Content container - scrollable
        canvas = tk.Canvas(self.dialog, highlightthickness=0)
        scrollbar = ttk.Scrollbar(self.dialog, orient="vertical", command=canvas.yview)
        scrollable_frame = ttk.Frame(canvas)

        scrollable_frame.bind(
            "<Configure>",
            lambda e: canvas.configure(scrollregion=canvas.bbox("all"))
        )

        canvas.create_window((0, 0), window=scrollable_frame, anchor="nw")
        canvas.configure(yscrollcommand=scrollbar.set)

        canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=(20, 0), pady=(20, 0))
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y, pady=(20, 0))

        # Session Info Frame
        info_frame = ttk.LabelFrame(scrollable_frame, text="Session Information", padding=15)
        info_frame.pack(fill=tk.X, padx=(0, 20), pady=(0, 10))

        session_id = self.metadata.get('session_id', 'Unknown')
        duration = format_duration(self.metadata.get('duration_seconds', 0))

        ttk.Label(info_frame, text=f"Session: {session_id}", font=('Arial', 11)).pack(anchor=tk.W)
        ttk.Label(info_frame, text=f"Duration: {duration}", font=('Arial', 11)).pack(anchor=tk.W, pady=(5, 0))

        # Format Selection Frame
        format_frame = ttk.LabelFrame(scrollable_frame, text="Export Format", padding=15)
        format_frame.pack(fill=tk.X, padx=(0, 20), pady=10)

        self.format_var = tk.StringVar(value=self.config.get('default_export_format', 'md'))

        formats = [
            ('txt', 'Plain Text (.txt)', 'Simple text file with terminal output'),
            ('md', 'Markdown (.md)', 'Markdown format with code blocks'),
            ('json', 'JSON (.json)', 'Structured JSON with metadata'),
            ('html', 'HTML (.html)', 'Styled HTML page'),
            ('cast', 'Asciinema Cast (.cast)', 'Native asciinema format (replayable)')
        ]

        for value, label, description in formats:
            frame = ttk.Frame(format_frame)
            frame.pack(fill=tk.X, pady=2)

            rb = ttk.Radiobutton(
                frame,
                text=label,
                variable=self.format_var,
                value=value
            )
            rb.pack(anchor=tk.W)

            desc_label = ttk.Label(
                frame,
                text=f"  {description}",
                font=('Arial', 9),
                foreground='gray'
            )
            desc_label.pack(anchor=tk.W, padx=(20, 0))

        # Save Location Frame
        location_frame = ttk.LabelFrame(scrollable_frame, text="Save Location", padding=15)
        location_frame.pack(fill=tk.X, padx=(0, 20), pady=10)

        default_location = self.config.get('default_save_location', str(Path.home() / 'Documents'))
        self.location_var = tk.StringVar(value=default_location)

        location_entry_frame = ttk.Frame(location_frame)
        location_entry_frame.pack(fill=tk.X)

        ttk.Entry(
            location_entry_frame,
            textvariable=self.location_var,
            width=40
        ).pack(side=tk.LEFT, fill=tk.X, expand=True)

        ttk.Button(
            location_entry_frame,
            text="Browse...",
            command=self._browse_location,
            width=10
        ).pack(side=tk.RIGHT, padx=(5, 0))

        # Filename Frame
        filename_frame = ttk.LabelFrame(scrollable_frame, text="Filename", padding=15)
        filename_frame.pack(fill=tk.X, padx=(0, 20), pady=10)

        default_filename = session_id.replace('session-', 'session_')
        self.filename_var = tk.StringVar(value=default_filename)

        filename_entry_frame = ttk.Frame(filename_frame)
        filename_entry_frame.pack(fill=tk.X)

        ttk.Entry(
            filename_entry_frame,
            textvariable=self.filename_var,
            width=30
        ).pack(side=tk.LEFT, fill=tk.X, expand=True)

        self.extension_label = ttk.Label(filename_entry_frame, text=".md")
        self.extension_label.pack(side=tk.RIGHT, padx=(5, 0))

        # Update extension when format changes
        def update_extension(*args):
            self.extension_label.config(text=f".{self.format_var.get()}")
        self.format_var.trace_add('write', update_extension)

    def _browse_location(self):
        """Browse for save location"""
        directory = filedialog.askdirectory(
            initialdir=self.location_var.get(),
            title="Select Save Location"
        )
        if directory:
            self.location_var.set(directory)

    def _cancel(self):
        """Cancel export"""
        self.result = None
        self.dialog.destroy()

    def _export(self):
        """Perform export"""
        # Get values
        format_type = self.format_var.get()
        location = Path(self.location_var.get())
        filename = self.filename_var.get()

        # Validate
        if not location.exists():
            messagebox.showerror(
                "Invalid Location",
                f"Directory does not exist: {location}"
            )
            return

        if not filename:
            messagebox.showerror(
                "Invalid Filename",
                "Please enter a filename"
            )
            return

        # Build output path
        output_file = location / f"{filename}.{format_type}"

        # Check if file exists
        if output_file.exists():
            if not messagebox.askyesno(
                "File Exists",
                f"File already exists:\n{output_file.name}\n\nOverwrite?"
            ):
                return

        # Export
        try:
            exporter = SessionExporter(self.session_file, self.metadata)
            success = exporter.export(output_file, format_type)

            if success:
                self.result = {
                    'format': format_type,
                    'output_file': output_file
                }
                messagebox.showinfo(
                    "Export Successful",
                    f"Session exported to:\n{output_file.name}"
                )
                self.dialog.destroy()
            else:
                messagebox.showerror(
                    "Export Failed",
                    "Failed to export session. Check console for errors."
                )
        except Exception as e:
            messagebox.showerror(
                "Export Error",
                f"Error during export:\n{str(e)}"
            )

    def show(self) -> Optional[Dict]:
        """
        Show dialog and wait for result

        Returns:
            Export result dictionary or None if cancelled
        """
        self.dialog.wait_window()
        return self.result


class SettingsDialog:
    """Settings dialog for user preferences"""

    def __init__(self, parent, config: Dict, save_callback: Callable):
        """
        Initialize settings dialog

        Args:
            parent: Parent tkinter window
            config: Current configuration
            save_callback: Function to call when saving settings
        """
        self.parent = parent
        self.config = config.copy()
        self.save_callback = save_callback

        # Create dialog window
        self.dialog = tk.Toplevel(parent)
        self.dialog.title("Settings")
        self.dialog.geometry("500x400")
        self.dialog.resizable(False, False)

        # Center on screen
        self.dialog.update_idletasks()
        x = (self.dialog.winfo_screenwidth() // 2) - (500 // 2)
        y = (self.dialog.winfo_screenheight() // 2) - (400 // 2)
        self.dialog.geometry(f"+{x}+{y}")

        # Make modal
        self.dialog.transient(parent)
        self.dialog.grab_set()

        # Build UI
        self._build_ui()

    def _build_ui(self):
        """Build the settings UI"""
        # Export Settings Frame
        export_frame = ttk.LabelFrame(self.dialog, text="Export Settings", padding=15)
        export_frame.pack(fill=tk.X, padx=20, pady=(20, 10))

        # Default Export Format
        ttk.Label(export_frame, text="Default Export Format:").pack(anchor=tk.W)
        self.format_var = tk.StringVar(value=self.config.get('default_export_format', 'md'))

        format_combo = ttk.Combobox(
            export_frame,
            textvariable=self.format_var,
            values=['txt', 'md', 'json', 'html', 'cast'],
            state='readonly',
            width=20
        )
        format_combo.pack(anchor=tk.W, pady=(5, 15))

        # Default Save Location
        ttk.Label(export_frame, text="Default Save Location:").pack(anchor=tk.W)

        location_frame = ttk.Frame(export_frame)
        location_frame.pack(fill=tk.X, pady=(5, 0))

        self.location_var = tk.StringVar(value=self.config.get('default_save_location', str(Path.home() / 'Documents')))

        ttk.Entry(
            location_frame,
            textvariable=self.location_var,
            width=35
        ).pack(side=tk.LEFT, fill=tk.X, expand=True)

        ttk.Button(
            location_frame,
            text="Browse...",
            command=self._browse_location,
            width=10
        ).pack(side=tk.RIGHT, padx=(5, 0))

        # Recording Settings Frame
        recording_frame = ttk.LabelFrame(self.dialog, text="Recording Settings", padding=15)
        recording_frame.pack(fill=tk.X, padx=20, pady=10)

        self.show_indicator_var = tk.BooleanVar(value=self.config.get('show_recording_indicator', True))
        ttk.Checkbutton(
            recording_frame,
            text="Show recording indicator",
            variable=self.show_indicator_var
        ).pack(anchor=tk.W, pady=2)

        self.show_duration_var = tk.BooleanVar(value=self.config.get('show_duration_timer', True))
        ttk.Checkbutton(
            recording_frame,
            text="Show duration timer",
            variable=self.show_duration_var
        ).pack(anchor=tk.W, pady=2)

        self.auto_pause_var = tk.BooleanVar(value=self.config.get('auto_pause_on_idle', False))
        ttk.Checkbutton(
            recording_frame,
            text="Auto-pause on idle (5 min)",
            variable=self.auto_pause_var
        ).pack(anchor=tk.W, pady=2)

        # Buttons
        button_frame = ttk.Frame(self.dialog)
        button_frame.pack(fill=tk.X, padx=20, pady=(20, 20))

        ttk.Button(
            button_frame,
            text="Cancel",
            command=self._cancel,
            width=15
        ).pack(side=tk.LEFT)

        ttk.Button(
            button_frame,
            text="Save",
            command=self._save,
            width=15
        ).pack(side=tk.RIGHT)

    def _browse_location(self):
        """Browse for save location"""
        directory = filedialog.askdirectory(
            initialdir=self.location_var.get(),
            title="Select Default Save Location"
        )
        if directory:
            self.location_var.set(directory)

    def _cancel(self):
        """Cancel settings"""
        self.dialog.destroy()

    def _save(self):
        """Save settings"""
        # Update config
        self.config['default_export_format'] = self.format_var.get()
        self.config['default_save_location'] = self.location_var.get()
        self.config['show_recording_indicator'] = self.show_indicator_var.get()
        self.config['show_duration_timer'] = self.show_duration_var.get()
        self.config['auto_pause_on_idle'] = self.auto_pause_var.get()

        # Call save callback
        self.save_callback(self.config)

        messagebox.showinfo("Settings Saved", "Your settings have been saved.")
        self.dialog.destroy()

    def show(self):
        """Show dialog and wait"""
        self.dialog.wait_window()
