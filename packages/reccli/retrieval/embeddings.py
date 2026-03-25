"""
Embedding providers for RecCli Phase 5

Supports multiple embedding backends:
- OpenAI text-embedding-3-small (default)
- Local sentence-transformers (optional)

All providers return L2-normalized vectors for consistent cosine similarity.
"""

from typing import List, Dict, Optional
from abc import ABC, abstractmethod
import hashlib


class EmbeddingProvider(ABC):
    """Base class for embedding providers"""

    @abstractmethod
    def embed(self, text: str) -> List[float]:
        """Embed a single text"""
        pass

    @abstractmethod
    def embed_batch(self, texts: List[str]) -> List[List[float]]:
        """Embed multiple texts in batch"""
        pass

    @property
    @abstractmethod
    def dimensions(self) -> int:
        """Vector dimensionality"""
        pass

    @property
    @abstractmethod
    def model_name(self) -> str:
        """Model identifier"""
        pass

    @property
    @abstractmethod
    def provider_name(self) -> str:
        """Provider name (openai, local, etc.)"""
        pass

    def compute_text_hash(self, text: str) -> str:
        """
        Compute hash of text for caching

        Uses blake3 if available, otherwise sha256
        """
        try:
            import blake3
            return f"blake3:{blake3.blake3(text.encode()).hexdigest()[:16]}"
        except ImportError:
            return f"sha256:{hashlib.sha256(text.encode()).hexdigest()[:16]}"


class OpenAIEmbeddings(EmbeddingProvider):
    """
    OpenAI text-embedding-3-small (default)

    Cost: $0.00002 per 1K tokens (~$0.02 per 1M tokens)
    Dimensions: 1536 (small) or 3072 (large)
    Quality: Excellent for semantic search
    """

    def __init__(self, api_key: Optional[str] = None, model: str = "text-embedding-3-small"):
        """
        Initialize OpenAI embeddings

        Args:
            api_key: OpenAI API key (or set OPENAI_API_KEY env var)
            model: Model name (text-embedding-3-small or text-embedding-3-large)
        """
        try:
            import openai
        except ImportError:
            raise ImportError(
                "OpenAI package not installed. Install with: pip install openai"
            )

        self.client = openai.OpenAI(api_key=api_key)
        self.model = model
        self._dimensions = 1536 if "small" in model else 3072

    def embed(self, text: str) -> List[float]:
        """Embed a single text"""
        response = self.client.embeddings.create(
            model=self.model,
            input=text
        )
        return response.data[0].embedding

    def embed_batch(self, texts: List[str], batch_size: int = 512) -> List[List[float]]:
        """
        Embed multiple texts in batch

        Args:
            texts: List of texts to embed
            batch_size: Batch size (OpenAI supports up to 2048)

        Returns:
            List of embeddings
        """
        if not texts:
            return []

        all_embeddings = []

        # Process in batches for cost efficiency
        for i in range(0, len(texts), batch_size):
            batch = texts[i:i + batch_size]

            response = self.client.embeddings.create(
                model=self.model,
                input=batch
            )

            embeddings = [item.embedding for item in response.data]
            all_embeddings.extend(embeddings)

        return all_embeddings

    @property
    def dimensions(self) -> int:
        return self._dimensions

    @property
    def model_name(self) -> str:
        return self.model

    @property
    def provider_name(self) -> str:
        return "openai"


class LocalEmbeddings(EmbeddingProvider):
    """
    Local sentence-transformers embeddings (optional)

    Models (recommended order):
    - nomic-ai/nomic-embed-text-v1.5: 768-dim, strong quality, Matryoshka support
    - BAAI/bge-base-en-v1.5: 768-dim, strong quality
    - BAAI/bge-small-en-v1.5: 384-dim, good quality, fast

    Pros: Free, offline, no API keys needed
    Cons: First load downloads model (~500MB), slower than API on large batches
    """

    def __init__(self, model: str = "nomic-ai/nomic-embed-text-v1.5"):
        """
        Initialize local embeddings

        Args:
            model: HuggingFace model name
        """
        try:
            from sentence_transformers import SentenceTransformer
        except ImportError:
            raise ImportError(
                "sentence-transformers not installed. Install with: pip install sentence-transformers"
            )

        print(f"Loading local embedding model: {model}...")
        self.model = SentenceTransformer(model, trust_remote_code=True)
        self._model_name = model
        self._dimensions = self.model.get_sentence_embedding_dimension()
        print(f"✓ Model loaded ({self._dimensions} dimensions)")

    def embed(self, text: str) -> List[float]:
        """Embed a single text"""
        embedding = self.model.encode(text, convert_to_numpy=True)
        return embedding.tolist()

    def embed_batch(self, texts: List[str], batch_size: int = 32) -> List[List[float]]:
        """
        Embed multiple texts in batch

        Args:
            texts: List of texts to embed
            batch_size: Batch size for inference

        Returns:
            List of embeddings
        """
        if not texts:
            return []

        embeddings = self.model.encode(
            texts,
            batch_size=batch_size,
            show_progress_bar=len(texts) > 100,
            convert_to_numpy=True
        )

        return embeddings.tolist()

    @property
    def dimensions(self) -> int:
        return self._dimensions

    @property
    def model_name(self) -> str:
        return self._model_name

    @property
    def provider_name(self) -> str:
        return "local"


def get_embedding_provider(config: Optional[Dict] = None) -> EmbeddingProvider:
    """
    Factory function for embedding providers

    Args:
        config: Configuration dict with keys:
            - provider: 'openai' (default) or 'local'
            - model: Model name
            - api_key: API key (for OpenAI)

    Returns:
        EmbeddingProvider instance

    Examples:
        # Use default (OpenAI text-embedding-3-small)
        provider = get_embedding_provider()

        # Use OpenAI with custom model
        provider = get_embedding_provider({
            'provider': 'openai',
            'model': 'text-embedding-3-large'
        })

        # Use local embeddings
        provider = get_embedding_provider({
            'provider': 'local',
            'model': 'nomic-ai/nomic-embed-text-v1.5'
        })
    """
    if config is None:
        config = {}

    provider = config.get('provider', 'openai')

    if provider == 'openai':
        # Fall back to local embeddings if no API key available
        api_key = config.get('api_key')
        if not api_key:
            try:
                from ..runtime.config import Config
                api_key = Config().get_api_key('openai')
            except Exception:
                pass
        if api_key:
            return OpenAIEmbeddings(
                api_key=api_key,
                model=config.get('model', 'text-embedding-3-small')
            )
        # No API key — fall back to local
        try:
            return LocalEmbeddings(
                model=config.get('model', 'nomic-ai/nomic-embed-text-v1.5')
            )
        except RuntimeError:
            raise RuntimeError(
                "No embedding provider available. Either set an OpenAI API key "
                "or install sentence-transformers: pip install sentence-transformers"
            )
    elif provider == 'local':
        return LocalEmbeddings(
            model=config.get('model', 'nomic-ai/nomic-embed-text-v1.5')
        )
    else:
        raise ValueError(f"Unknown embedding provider: {provider}")


def normalize_vector(vector: List[float]) -> List[float]:
    """
    L2-normalize a vector for cosine similarity

    Args:
        vector: Input vector

    Returns:
        Normalized vector
    """
    import math

    magnitude = math.sqrt(sum(x * x for x in vector))
    if magnitude == 0:
        return vector
    return [x / magnitude for x in vector]


def cosine_similarity(vec1: List[float], vec2: List[float]) -> float:
    """
    Calculate cosine similarity between two vectors

    Assumes vectors are already L2-normalized.

    Args:
        vec1: First vector
        vec2: Second vector

    Returns:
        Similarity score (0 to 1)
    """
    if len(vec1) != len(vec2):
        raise ValueError(f"Vector dimension mismatch: {len(vec1)} != {len(vec2)}")

    dot_product = sum(a * b for a, b in zip(vec1, vec2))
    return dot_product
