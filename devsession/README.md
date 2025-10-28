# .devsession Format Documentation

This directory contains documentation for the `.devsession` file format - an open standard for storing AI-assisted development sessions with intelligent summarization.

## What's Here

- **[DEVSESSION_FORMAT.md](DEVSESSION_FORMAT.md)** - Complete format specification
- **[examples/](examples/)** - Example `.devsession` files showing real usage
- **[schemas/](schemas/)** - JSON schema for validation

## Quick Overview

The `.devsession` format enables:

1. **Lossless preservation** of AI coding conversations
2. **Intelligent summarization** for efficient context loading
3. **Multi-session synthesis** for compound context
4. **Tool-agnostic design** works with any AI coding assistant

## Format Status

**Current:** Draft v1.0.0 (Specification complete, implementation pending)

**Timeline:**
- ✅ **Now:** Format specification defined
- 🔄 **Q1 2025:** Wait for better AI models (Sonnet 5, Grok 5)
- 🚧 **Q2 2025:** Implement smart summarization in RecCli Pro
- 🎯 **2025-2026:** Push for adoption across AI coding tools

## Why This Matters

Current AI coding tools (Claude Code, Cursor, Copilot) all struggle with context management:
- Conversations get long
- Auto-compaction is lossy
- Context gets lost between sessions

The `.devsession` format solves this with:
- Full conversation preservation
- AI-generated summaries for efficiency
- Section expansion for details
- Multi-session context loading

## Example Use Case

```bash
# Session 1: Build feature (2 hours, 200+ messages)
claude-code build-stripe-integration
# Save with AI summary
reccli save session-001.devsession --summarize

# Session 2: Continue next day
claude-code --load session-001.devsession
# Claude has full context from previous day
# Can expand specific sections if needed
```

**Better than compaction:** Nothing is lost, everything is searchable, context is intelligent.

## For Tool Developers

Want to implement `.devsession` support in your AI coding tool?

1. Read the [format specification](DEVSESSION_FORMAT.md)
2. Check out the [example files](examples/)
3. Use the [JSON schema](schemas/devsession.schema.json) for validation
4. Open an issue to discuss integration

The format is designed to be tool-agnostic. We'd love to see it adopted across the AI coding ecosystem.

## For Users

This is **future functionality**. The format spec is ready, but implementation requires:
1. Better AI models for quality summarization
2. Tool integration/APIs
3. User testing and feedback

Expected in RecCli Pro (Q2 2025).

## Contributing

To propose changes to the format specification:
1. Open an issue: https://github.com/willluecke/RecCli/issues
2. Discuss with community
3. Submit PR with updates
4. Follow semantic versioning

## License

The `.devsession` format specification is released under **CC0 1.0 Universal** (Public Domain).

Tools may implement this format freely without restriction.

---

**Maintained by the RecCli project and community.**
