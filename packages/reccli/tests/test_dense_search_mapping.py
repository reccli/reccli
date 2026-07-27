"""Dense search must return the document the matched embedding came from.

The embedding matrix holds only vectors carrying an inline embedding, so matrix
row i is the i-th *such* vector, not unified_vectors[i]. Search assumed they were
the same. The result was not a subtle ranking issue: a query whose nearest
neighbour was a message in one session returned a message from a different
session, and only the first N vectors were reachable at all (2.4% of one real
store).

Self-retrieval is the sharpest test available: feed back an embedding that is
already in the index, and the vector it came from must rank first. It fails
loudly under the old mapping and cannot be satisfied by accident.
"""

import unittest

import numpy as np

from reccli.retrieval.search import dense_search


def _unit(seed, dim=8):
    rng = np.random.default_rng(seed)
    v = rng.normal(size=dim).astype(np.float32)
    return (v / np.linalg.norm(v)).tolist()


def _index(n_vectors, embedded_positions, dim=8, with_row_map=True):
    """Build an index where only `embedded_positions` carry inline embeddings."""
    vectors = []
    for i in range(n_vectors):
        vec = {"id": f"vec_{i:03d}", "session": f"sess_{i:03d}", "content_preview": f"doc {i}"}
        if i in embedded_positions:
            vec["embedding"] = _unit(i, dim)
        vectors.append(vec)
    rows = [i for i in range(n_vectors) if i in embedded_positions]
    index = {
        "unified_vectors": vectors,
        "embeddings_matrix": np.array([vectors[i]["embedding"] for i in rows], dtype=np.float32),
    }
    if with_row_map:
        index["embedding_row_map"] = rows
    return index, rows


class DenseSearchMappingTests(unittest.TestCase):

    def test_self_retrieval_returns_the_source_vector(self):
        """The property that was violated: a stored embedding must find itself."""
        index, rows = _index(50, embedded_positions={7, 19, 33, 41})
        for position in rows:
            with self.subTest(position=position):
                hits = dense_search(index, index["unified_vectors"][position]["embedding"], k=1)
                self.assertTrue(hits, "expected a hit")
                self.assertEqual(hits[0]["id"], f"vec_{position:03d}")

    def test_sparse_embeddings_do_not_shift_results(self):
        """Embeddings clustered late in the list is the shape that broke.

        With rows at 40+ and arange mapping, every hit resolved to vectors[0:4].
        """
        index, rows = _index(60, embedded_positions={40, 47, 53, 59})
        for position in rows:
            hits = dense_search(index, index["unified_vectors"][position]["embedding"], k=1)
            self.assertEqual(hits[0]["id"], f"vec_{position:03d}")

    def test_works_without_a_row_map(self):
        """Indexes built before the map exists must still resolve correctly."""
        index, rows = _index(30, embedded_positions={3, 11, 26}, with_row_map=False)
        for position in rows:
            hits = dense_search(index, index["unified_vectors"][position]["embedding"], k=1)
            self.assertEqual(hits[0]["id"], f"vec_{position:03d}")

    def test_every_embedded_vector_is_reachable(self):
        """Nothing carrying an embedding may be excluded from results."""
        embedded = {5, 12, 21, 28, 37}
        index, rows = _index(40, embedded_positions=embedded)
        returned = {h["id"] for h in dense_search(index, _unit(999), k=40)}
        self.assertEqual(returned, {f"vec_{i:03d}" for i in sorted(embedded)})

    def test_mismatched_matrix_returns_nothing_rather_than_wrong_documents(self):
        """A matrix that does not describe these vectors must not be guessed at."""
        index, _ = _index(20, embedded_positions={2, 9}, with_row_map=False)
        index["embeddings_matrix"] = np.array([_unit(1), _unit(2), _unit(3)], dtype=np.float32)
        self.assertEqual(dense_search(index, _unit(1), k=5), [])

    def test_row_map_of_wrong_length_is_not_trusted(self):
        index, rows = _index(20, embedded_positions={2, 9})
        index["embedding_row_map"] = [0]          # stale/truncated
        hits = dense_search(index, index["unified_vectors"][9]["embedding"], k=1)
        self.assertTrue(hits)
        self.assertEqual(hits[0]["id"], "vec_009", "should fall back to recomputing, not trust a bad map")


if __name__ == "__main__":
    unittest.main()
