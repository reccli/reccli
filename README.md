# 🎬 RecCli

**The dead-simple CLI recorder with a floating record button**

Finally, a record button for your terminal. One click to start, one click to stop. That's it.

![RecCli Demo](https://reccli.com/demo.gif)

## Why RecCli?

Every developer has lost that perfect debugging session. The one where everything magically worked. The command you can't remember. The output that vanished.

**RecCli fixes that.**

- 🔴 **One-click recording** - Floating button stays on top while you work
- ⏱️ **Live duration tracker** - See exactly how long you've been recording
- 💾 **Auto-save with timestamps** - Never lose a session
- 🎥 **Multiple formats** - asciinema or script
- 🚀 **Zero config** - Works immediately after install
- 🆓 **100% Free & Open Source** - MIT Licensed

## Quick Start

```bash
# Install
git clone https://github.com/willluecke/RecCli.git
cd RecCli
./install.sh

# Run
reccli gui

# Start recording with one click!
```

Or install directly:
```bash
curl -sSL https://raw.githubusercontent.com/willluecke/RecCli/main/install.sh | bash
```

## Features

### The Floating Button
- **Circle (green)** = Ready to record
- **Square (red)** = Recording in progress
- Drag anywhere on your screen
- Stays on top of all windows
- Right-click for quick access to recordings

### Smart Recording
- Captures everything: input, output, colors, timing
- Supports both asciinema (.cast) and script (.log) formats
- Organized by timestamp: `session_20251027_143045.cast`
- All recordings stored locally in `~/.reccli/recordings`

### CLI Mode
```bash
# GUI mode (default)
reccli gui

# View stats
reccli status

# Check version
reccli --version
```

## Installation

### Automatic (Recommended)
```bash
git clone https://github.com/willluecke/RecCli.git
cd RecCli
./install.sh
```

### Manual
```bash
# Install dependencies
pip3 install asciinema  # Optional but recommended

# Ubuntu/Debian
sudo apt-get install python3 python3-tk

# macOS
brew install python-tk

# Make executable
chmod +x reccli.py

# Run
python3 reccli.py gui
```

## Requirements

- Python 3.6+
- tkinter (for GUI)
- asciinema or script command (for recording)

**Recommended:** Install asciinema for best recording quality
```bash
pip install asciinema
```

## Use Cases

### Debugging
Record your entire debugging session. When you finally fix that bug, you'll have the exact steps captured.

### Teaching
Share terminal sessions with juniors. They can replay exactly what you did, at their own pace.

### Documentation
Better than screenshots. Better than screen recordings. Just the terminal, perfectly captured.

### AI Coding Sessions
Recording Claude Code, Cursor, or Copilot sessions? RecCli captures everything without losing context.

### Pair Programming
Share your screen recording with remote teammates. Perfect for async code reviews.

## FAQ

**Q: Why not just use the script command?**
A: Same reason you don't use Print Screen for screenshots. Sure, it works, but reducing friction changes behavior. You'll never remember to type 'script' before that debugging session. You will click a red button that's always visible.

**Q: What about asciinema?**
A: asciinema is great! We actually use it under the hood. But it's still command-line based. RecCli is about the UI/UX layer - the floating button that makes you actually USE recording instead of forgetting about it.

**Q: Where are recordings stored?**
A: Locally on your machine in `~/.reccli/recordings/`. Your data, your control.

**Q: Can I use this with tmux/screen?**
A: Yes! RecCli works with any terminal setup.

**Q: Is this really free?**
A: Yes! MIT licensed. Use it, fork it, modify it. We believe great dev tools should be free.

## Contributing

Contributions are welcome! Feel free to:
- Report bugs
- Suggest features
- Submit pull requests
- Improve documentation

## Roadmap

- [ ] Cloud sync for recordings (optional)
- [ ] Team sharing features
- [ ] AI-powered session analysis
- [ ] VS Code extension
- [ ] Terminal session search
- [ ] Playback in browser
- [ ] Windows support

## Tech Stack

- **Frontend**: Python + Tkinter (cross-platform GUI)
- **Recording**: asciinema / script command
- **Storage**: Local filesystem

## License

MIT License - see [LICENSE](LICENSE) file for details.

---

**Built with love by developers, for developers.**

[GitHub](https://github.com/willluecke/RecCli) | [Issues](https://github.com/willluecke/RecCli/issues)

**Like this project? Give it a ⭐ on GitHub!**
