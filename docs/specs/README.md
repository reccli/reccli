# RecCli Specs

This folder contains the format and contract documents for RecCli.

If you need to understand how the system stores, links, or retrieves memory, start here.

## Implemented Or Closest To Code

- [DEVSESSION_FORMAT.md](./DEVSESSION_FORMAT.md)
- [MESSAGE_RANGE_SPEC.md](./MESSAGE_RANGE_SPEC.md)
- [UNIFIED_VECTOR_INDEX.md](./UNIFIED_VECTOR_INDEX.md)

## Planned Project-Layer Specs

- [DEVPROJECT_FILE.md](./DEVPROJECT_FILE.md)
- [SESSION_STORAGE.md](./SESSION_STORAGE.md)

## Related Phase Docs

These are implementation-phase documents rather than canonical format specs:

- [PHASE_5_IMPLEMENTATION.md](../archive/progress/phases/PHASE_5_IMPLEMENTATION.md)
- [PHASE_6_IMPLEMENTATION.md](../archive/progress/phases/PHASE_6_IMPLEMENTATION.md)

## Schemas

- [schemas/devsession.schema.json](./schemas/devsession.schema.json) - historical draft schema; not yet aligned with the live Python implementation

## Recommended Reading Order

1. [DEVSESSION_FORMAT.md](./DEVSESSION_FORMAT.md)
2. [MESSAGE_RANGE_SPEC.md](./MESSAGE_RANGE_SPEC.md)
3. [UNIFIED_VECTOR_INDEX.md](./UNIFIED_VECTOR_INDEX.md)
4. [DEVPROJECT_FILE.md](./DEVPROJECT_FILE.md)

## Notes

The implemented `.devsession` and retrieval/index specs are closer to canon today than the project-layer docs. `.devproject` and storage strategy are still design-facing rather than mainline CLI behavior.

For system rationale and evolution, see:

- [Docs Index](../README.md)
- [Architecture](../architecture/ARCHITECTURE.md)
- [Project Plan](../../PROJECT_PLAN.md)
