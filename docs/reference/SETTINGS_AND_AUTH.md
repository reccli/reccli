# Settings and Authentication

**Status:** Current operational reference with limited future notes.

This document describes the current configuration surface in the live CLI. Where future auth or settings flows are mentioned, they are explicitly labeled as planned rather than shipped.

## Current Authentication Model

RecCli currently uses provider API keys stored in local config.

Supported providers:

- Anthropic
- OpenAI

Current setup commands:

```bash
PYTHONPATH=packages/reccli-core python3 -m reccli.cli config --anthropic-key sk-ant-...
PYTHONPATH=packages/reccli-core python3 -m reccli.cli config --openai-key sk-...
```

There is no shipped OAuth flow, setup wizard, keychain integration, or `config` subcommand tree in the current CLI.

## Current Config Surface

The live `config` command supports:

- `--anthropic-key`
- `--openai-key`
- `--default-model`

Examples:

```bash
PYTHONPATH=packages/reccli-core python3 -m reccli.cli config --default-model claude
PYTHONPATH=packages/reccli-core python3 -m reccli.cli config
```

Running `config` with no flags prints:

- sessions directory
- default model
- whether Anthropic key is set
- whether OpenAI key is set

## Current Storage

Config directory:

```text
~/reccli/
```

Config file:

```text
~/reccli/config.json
```

Default sessions directory:

```text
~/reccli/sessions/
```

## Current Config Shape

The current [Config](/Users/will/coding-projects/RecCli/packages/reccli-core/reccli/config.py) implementation stores a simple JSON object:

```json
{
  "api_keys": {
    "anthropic": null,
    "openai": null
  },
  "default_model": "claude",
  "sessions_dir": "/Users/you/reccli/sessions"
}
```

Important accuracy note:

- keys are stored as plaintext JSON today
- the current code does not use system keychain integration
- the current code does not store project cache or OAuth session state in this file

## Current Behavior Limits

The following are not implemented in the live CLI:

- `reccli config list`
- `reccli config test`
- `reccli config unset`
- `reccli config setup`
- `reccli auth login`
- project-root storage configuration via `config set`

Those ideas may still be useful future design directions, but they are not the current operational contract.

## Practical Guidance

For today, treat settings as intentionally minimal:

- save API keys
- set a default model
- inspect current config output

If you need richer settings, they should be added by extending [config.py](/Users/will/coding-projects/RecCli/packages/reccli-core/reccli/config.py) and the argparse surface in [cli.py](/Users/will/coding-projects/RecCli/packages/reccli-core/reccli/cli.py).

## Future Notes

Possible future directions that are not yet implemented:

- keychain-backed secret storage
- OAuth or hosted auth flows
- setup wizard
- project-level storage selection
- validation and testing helpers for provider credentials
