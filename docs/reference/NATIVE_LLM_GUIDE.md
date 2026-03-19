# RecCli Native LLM Guide

**Status:** Current operational reference.

This document covers the current Python CLI chat surface. It reflects the live command set under [cli.py](/Users/will/coding-projects/RecCli/packages/reccli-core/reccli/cli.py).

## What Exists Today

RecCli supports native LLM chat without wrapping another CLI.

Current entry points:

- `chat`: interactive chat UI
- `ask`: one-shot question
- `config`: API keys and default model
- `list`, `show`, `export`: session management

All chats are persisted as `.devsession` files.

## Quick Start

### 1. Install dependencies

```bash
cd /Users/will/coding-projects/RecCli
pip3 install anthropic openai
```

### 2. Configure a provider key

```bash
PYTHONPATH=packages/reccli-core python3 -m reccli.cli config --anthropic-key sk-ant-YOUR_KEY_HERE
PYTHONPATH=packages/reccli-core python3 -m reccli.cli config --openai-key sk-YOUR_KEY_HERE
```

### 3. Start chat

```bash
PYTHONPATH=packages/reccli-core python3 -m reccli.cli chat
PYTHONPATH=packages/reccli-core python3 -m reccli.cli chat --model claude
PYTHONPATH=packages/reccli-core python3 -m reccli.cli chat --model gpt5
PYTHONPATH=packages/reccli-core python3 -m reccli.cli ask "explain .devsession"
```

## Current Model Surface

The chat command currently accepts:

- `claude`
- `claude-sonnet`
- `claude-opus`
- `claude-haiku`
- `gpt5`
- `gpt5-mini`
- `gpt5-nano`
- `gpt4o`

`claude` is normalized to `claude-sonnet` in the CLI path.

## Configuration

The current config command supports only three operations:

```bash
PYTHONPATH=packages/reccli-core python3 -m reccli.cli config --anthropic-key sk-ant-...
PYTHONPATH=packages/reccli-core python3 -m reccli.cli config --openai-key sk-...
PYTHONPATH=packages/reccli-core python3 -m reccli.cli config --default-model claude
```

Running `config` with no flags prints the current configuration:

```bash
PYTHONPATH=packages/reccli-core python3 -m reccli.cli config
```

Config is stored in:

```text
~/reccli/config.json
```

Sessions default to:

```text
~/reccli/sessions/
```

## Session Management

List sessions:

```bash
PYTHONPATH=packages/reccli-core python3 -m reccli.cli list
```

Show one session:

```bash
PYTHONPATH=packages/reccli-core python3 -m reccli.cli show my-session
```

Export a session:

```bash
PYTHONPATH=packages/reccli-core python3 -m reccli.cli export my-session
PYTHONPATH=packages/reccli-core python3 -m reccli.cli export my-session -f txt
```

## Chat Notes

The current `chat` command launches the TypeScript + Ink UI through [chat_ui.py](/Users/will/coding-projects/RecCli/packages/reccli-core/reccli/chat_ui.py). That means the UX is terminal-native, but the source of truth for session and model logic remains in the Python core.

The UI bridge now targets the packaged backend under `packages/reccli-core/backend/`. See [RECCLI_CLI_UI.md](/Users/will/coding-projects/RecCli/docs/architecture/RECCLI_CLI_UI.md) for the current architecture note.

## `.devsession` Output

The current `DevSession` object includes:

- terminal recording metadata/events
- conversation messages
- summary
- vector index
- token counts
- compaction history
- episodes

The exact schema is documented in [DEVSESSION_FORMAT.md](/Users/will/coding-projects/RecCli/docs/specs/DEVSESSION_FORMAT.md).

## What This Doc Does Not Cover

This guide does not define:

- retrieval architecture
- compaction behavior
- `.devproject` design
- future auth or settings UX

Use these docs for that:

- [CONTEXT_LOADING.md](/Users/will/coding-projects/RecCli/docs/architecture/CONTEXT_LOADING.md)
- [SETTINGS_AND_AUTH.md](/Users/will/coding-projects/RecCli/docs/reference/SETTINGS_AND_AUTH.md)
- [DEVPROJECT_FORMAT.md](/Users/will/coding-projects/RecCli/docs/specs/DEVPROJECT_FORMAT.md)
