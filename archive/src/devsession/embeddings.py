"""
Embedding utilities for .devsession format
Supports sentence-transformers or mock embeddings for testing
"""

import hashlib
from typing import List, Callable
import numpy as np


# Try to import sentence-transformers
try:
    from sentence_transformers import SentenceTransformer
    HAS_SENTENCE_TRANSFORMERS = True
except ImportError:
    HAS_SENTENCE_TRANSFORMERS = False


class EmbeddingGenerator:
    """Generate embeddings for text using sentence-transformers or mock"""

    def __init__(self, model_name: str = 'all-MiniLM-L6-v2', use_mock: bool = False):
        """
        Initialize embedding generator

        Args:
            model_name: Name of sentence-transformers model
            use_mock: Force use of mock embeddings (for testing)
        """
        self.model_name = model_name
        self.dimensions = 384  # all-MiniLM-L6-v2 dimensions
        self.use_mock = use_mock

        if not use_mock and HAS_SENTENCE_TRANSFORMERS:
            try:
                print(f"Loading embedding model: {model_name}...")
                self.model = SentenceTransformer(model_name)
                self.dimensions = self.model.get_sentence_embedding_dimension()
                self._generate = self._generate_real
                print(f"✓ Model loaded ({self.dimensions} dimensions)")
            except Exception as e:
                print(f"⚠️  Failed to load model: {e}")
                print("   Using mock embeddings instead")
                self._generate = self._generate_mock
                self.use_mock = True
        else:
            if not HAS_SENTENCE_TRANSFORMERS:
                print("⚠️  sentence-transformers not installed")
                print("   Install with: pip install sentence-transformers")
                print("   Using mock embeddings for now")
            self._generate = self._generate_mock
            self.use_mock = True

    def embed(self, text: str) -> List[float]:
        """
        Generate embedding for text

        Args:
            text: Input text

        Returns:
            Embedding vector as list of floats
        """
        return self._generate(text)

    def embed_batch(self, texts: List[str]) -> List[List[float]]:
        """
        Generate embeddings for multiple texts

        Args:
            texts: List of input texts

        Returns:
            List of embedding vectors
        """
        if self.use_mock:
            return [self._generate_mock(text) for text in texts]
        else:
            embeddings = self.model.encode(texts, show_progress_bar=True)
            return [emb.tolist() for emb in embeddings]

    def _generate_real(self, text: str) -> List[float]:
        """Generate real embedding using sentence-transformers"""
        embedding = self.model.encode(text)
        return embedding.tolist()

    def _generate_mock(self, text: str) -> List[float]:
        """
        Generate mock embedding for testing
        Uses deterministic hash-based vector generation
        """
        # Use MD5 hash of text to generate deterministic vector
        hash_bytes = hashlib.md5(text.encode()).digest()

        # Convert to array of floats
        values = []
        for i in range(0, len(hash_bytes), 4):
            # Take 4 bytes at a time, interpret as float
            chunk = hash_bytes[i:i+4]
            if len(chunk) == 4:
                # Convert bytes to int, then normalize to [-1, 1]
                int_val = int.from_bytes(chunk, 'little')
                float_val = (int_val / (2**31)) - 1.0
                values.append(float_val)

        # Repeat to get desired dimensions
        while len(values) < self.dimensions:
            values.extend(values[:self.dimensions - len(values)])

        vector = values[:self.dimensions]

        # Normalize to unit length (like real embeddings)
        norm = np.linalg.norm(vector)
        if norm > 0:
            vector = [v / norm for v in vector]

        return vector


# Global embedding generator instance
_embedding_generator = None


def get_embedding_generator(
    model_name: str = 'all-MiniLM-L6-v2',
    use_mock: bool = False
) -> EmbeddingGenerator:
    """
    Get or create global embedding generator instance

    Args:
        model_name: Name of sentence-transformers model
        use_mock: Force use of mock embeddings

    Returns:
        EmbeddingGenerator instance
    """
    global _embedding_generator

    if _embedding_generator is None:
        _embedding_generator = EmbeddingGenerator(model_name, use_mock)

    return _embedding_generator


def embed_text(text: str, use_mock: bool = False) -> List[float]:
    """
    Convenience function to embed text

    Args:
        text: Input text
        use_mock: Use mock embeddings

    Returns:
        Embedding vector
    """
    generator = get_embedding_generator(use_mock=use_mock)
    return generator.embed(text)


def embed_texts(texts: List[str], use_mock: bool = False) -> List[List[float]]:
    """
    Convenience function to embed multiple texts

    Args:
        texts: List of input texts
        use_mock: Use mock embeddings

    Returns:
        List of embedding vectors
    """
    generator = get_embedding_generator(use_mock=use_mock)
    return generator.embed_batch(texts)
