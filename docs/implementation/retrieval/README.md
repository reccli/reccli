# Retrieval Implementation

**Status:** Current implementation overview.

This folder documents the live retrieval and prompt-hydration path behind RecCli memory loading.

## Source Of Truth In Code

- `packages/reccli-core/reccli/memory_middleware.py`
- `packages/reccli-core/reccli/streaming_retrieval.py`
- `packages/reccli-core/reccli/search.py`

## What The Current Code Does

- hydrates prompts from session summary, recent messages, and relevant history
- performs progressive retrieval in instant, fast, and smart stages
- uses live search/index infrastructure rather than summary-only placeholder search
- keeps message-range linking and verification as part of the summary safety model

## Important Boundary

`packages/reccli-core/reccli/retrieval.py` still exists, but it is a simpler helper layer. The main implemented retrieval path is the middleware plus streaming retrieval stack above.

## Recommended Reading

- `STREAMING_HYBRID_RETRIEVAL.md` for the progressive retrieval design and flow
- `docs/archive/implementation/retrieval/SUMMARIZER_LINKING_INDEX_SAFEGUARDS.md` for the historical safeguards review
- `docs/specs/MESSAGE_RANGE_SPEC.md` for the canonical range semantics
