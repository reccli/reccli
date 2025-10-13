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

## Quick Start

```bash
# Install
./install.sh

# Run
reccli gui

# Start recording with one click!
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
- Supports both asciinema (.cast) and script (.script) formats
- Organized by timestamp: `recording_2025-10-13_14-30-45.cast`
- Playback with `reccli play <filename>`

### CLI Mode
```bash
# GUI mode (default)
reccli gui

# Start/stop from terminal
reccli start
reccli stop

# List recordings
reccli list

# Play a recording
reccli play recording_2025-10-13_14-30-45.cast

# Share stats
reccli stats
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
pip3 install tkinter asciinema

# Make executable
chmod +x reccli.py

# Run
python3 reccli.py gui
```

## Pricing

**$5/month** - Simple, honest pricing for a tool that just works.

- ✅ 7-day free trial (card required)
- ✅ Unlimited recordings
- ✅ All features unlocked
- ✅ Cancel anytime
- ✅ No free tier (because good tools cost money)

[Start your free trial →](https://reccli.com)

## Use Cases

### Debugging
Record your entire debugging session. When you finally fix that bug, you'll have the exact steps captured.

### Teaching
Share terminal sessions with juniors. They can replay exactly what you did, at their own pace.

### Documentation
Better than screenshots. Better than screen recordings. Just the terminal, perfectly captured.

### AI Coding Sessions
Recording Claude Code, Cursor, or Copilot sessions? RecCli captures everything without losing context.

## FAQ

**Q: Why no free tier?**
A: We learned from real founder experience - free tiers kill early conversions and create support burden. $5/mo is fair for a tool that saves you hours.

**Q: What happens after the trial?**
A: You'll be charged $5/mo automatically. Cancel anytime with one click. We'll remind you on Day 6.

**Q: Is my data private?**
A: Yes. Recordings are stored locally on your machine. We never see your terminal output.

**Q: What formats are supported?**
A: asciinema (.cast) and script (.script). Both are standard, open formats.

**Q: Can I use this with tmux/screen?**
A: Yes! RecCli works with any terminal setup.

## Tech Stack

- **Frontend**: Python + Tkinter (cross-platform GUI)
- **Recording**: asciinema / script command
- **Backend**: Stripe for payments, simple license validation
- **Hosting**: Vercel (landing page), Supabase (auth)

## Roadmap

- [ ] Cloud sync for recordings
- [ ] Team sharing features
- [ ] AI-powered session analysis
- [ ] VS Code extension
- [ ] Terminal session search

## Contributing

This is a commercial product, but we welcome bug reports and feature suggestions!

Open an issue on GitHub or email: hello@reccli.com

## License

Proprietary - See LICENSE file for details.

---

**Built with love by solo developers, for developers.**

[reccli.com](https://reccli.com) | [@reccli](https://x.com/reccli) | [hello@reccli.com](mailto:hello@reccli.com)
