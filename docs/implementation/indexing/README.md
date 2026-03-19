# Indexing Implementation

**Status:** Current implementation overview.

This folder documents the live multi-session indexing and search path behind RecCli retrieval.

## Source Of Truth In Code

- `packages/reccli-core/reccli/vector_index.py`
- `packages/reccli-core/reccli/search.py`

## What The Current Code Does

- builds a unified cross-session index from `.devsession` files
- stores vector metadata in `index.json`
- stores the dense embedding matrix in `.index_embeddings.npy`
- loads embeddings through a three-path strategy: binary file, in-memory array, fallback extraction
- performs dense search with vectorized numpy operations
- combines dense and BM25 sparse search with reciprocal rank fusion
- preserves temporal metadata including session, section, and episode filters

## Recommended Reading

- `docs/specs/UNIFIED_VECTOR_INDEX.md` for the format/contract layer
- `docs/archive/implementation/indexing/VECTOR_SEARCH_FINAL.md` for the historical implementation retrospective

## Historical Notes

Earlier optimization notes were iterative snapshots of the same work and have been moved under `docs/archive/implementation/indexing/`.
