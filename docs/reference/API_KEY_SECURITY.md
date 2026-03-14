# API Key Security

**Status:** Current storage/security note.

This document describes the current code behavior, not an aspirational security model.

## Where Keys Are Stored Today

The live CLI stores config in:

```text
~/reccli/config.json
```

That path is outside the RecCli git repository.

The current config implementation is in [config.py](/Users/will/coding-projects/RecCli/packages/reccli-core/reccli/config.py).

## What That Means

Today:

- API keys are stored locally on disk
- the storage format is plaintext JSON
- keys are not encrypted by the current code
- keys are not stored in the repository unless a user manually copies the file there

This is acceptable for local development, but it is not the same as keychain-backed secret storage.

## Why This Is Still Relatively Safe

- the file lives in your home directory, outside the repo
- normal local file permissions still apply
- the config path is separate from tracked project files

## What To Be Careful About

- do not copy `~/reccli/config.json` into the repository
- do not paste real keys into docs, tests, examples, or commit messages
- do not assume the current implementation encrypts keys

## Practical Check

To inspect the current config without printing key values directly into a shared shell history snippet, use:

```bash
PYTHONPATH=packages/reccli-core python3 -m reccli.cli config
```

That command reports whether keys are set, not the key contents.

## Better Future Direction

If stronger local secret handling becomes important, the next upgrade path is:

- move provider secrets into system keychain storage
- keep non-secret settings in `~/reccli/config.json`
- leave the CLI surface unchanged where possible

That upgrade is not implemented yet.
