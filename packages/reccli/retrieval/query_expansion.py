"""
Query expansion for MMC (Multiple Model Comparison) search.

Generates synonym-expanded query variations from a hardcoded
software engineering vocabulary. Used by search_mmc() to run
multiple BM25 passes and fuse results for broader recall.
"""

from typing import List

# ---------------------------------------------------------------------------
# Synonym clusters — bidirectional (each term maps to its cluster peers)
# ---------------------------------------------------------------------------

_SYNONYM_CLUSTERS = [
    # Auth & identity
    {"auth", "authentication", "authorization", "login", "credentials", "sign-in"},
    # Middleware & layers
    {"middleware", "layer", "interceptor", "handler", "filter"},
    # API & endpoints
    {"api", "endpoint", "route", "handler", "REST"},
    # Data & storage
    {"database", "db", "datastore", "persistence", "storage"},
    {"schema", "model", "structure", "table", "entity"},
    {"query", "search", "lookup", "retrieval", "fetch"},
    # Errors & bugs
    {"error", "exception", "failure", "fault", "bug", "issue"},
    {"crash", "panic", "segfault", "abort"},
    # Code quality
    {"refactor", "restructure", "reorganize", "cleanup", "rewrite"},
    {"test", "testing", "spec", "assertion", "validation"},
    # Infra & ops
    {"config", "configuration", "settings", "options", "preferences"},
    {"cache", "caching", "memoization", "buffer"},
    {"deploy", "deployment", "release", "ship"},
    # Architecture
    {"component", "module", "widget", "element"},
    {"state", "store", "context", "state management"},
    {"hook", "callback", "event handler", "listener", "trigger"},
    # Session & memory (RecCli-specific)
    {"session", "conversation", "devsession", "recording"},
    {"summary", "summarization", "compaction", "overview"},
    {"embedding", "vector", "dense", "semantic"},
    {"BM25", "keyword", "sparse", "term frequency"},
]

# Build lookup: term -> set of synonyms (excluding itself)
_SYNONYM_MAP = {}
for cluster in _SYNONYM_CLUSTERS:
    for term in cluster:
        key = term.lower()
        peers = {t.lower() for t in cluster if t.lower() != key}
        if key in _SYNONYM_MAP:
            _SYNONYM_MAP[key] |= peers
        else:
            _SYNONYM_MAP[key] = peers


def expand_query(query: str, max_variations: int = 3) -> List[str]:
    """Generate synonym-expanded query variations.

    Returns the original query plus up to max_variations alternatives
    created by substituting one term at a time with a synonym.

    Args:
        query: Original search query.
        max_variations: Maximum number of additional variations (default 3).

    Returns:
        List starting with the original query, followed by variations.
    """
    terms = query.lower().split()
    variations = [query]

    # Find terms that have synonyms
    expandable = [(i, term) for i, term in enumerate(terms) if term in _SYNONYM_MAP]

    if not expandable:
        return variations

    # Generate variations by substituting one term at a time
    for idx, term in expandable:
        for synonym in sorted(_SYNONYM_MAP[term])[:2]:  # max 2 synonyms per term
            new_terms = terms.copy()
            new_terms[idx] = synonym
            variation = " ".join(new_terms)
            if variation not in variations:
                variations.append(variation)
            if len(variations) > max_variations:
                return variations

    return variations
