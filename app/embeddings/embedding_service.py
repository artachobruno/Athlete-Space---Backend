"""Embedding service for semantic retrieval.

This module provides a unified interface for computing embeddings using
OpenAI's text-embedding-3-small model. All embeddings go through this service
to ensure consistency and enable easy model switching.
"""

import hashlib
from typing import Literal

from loguru import logger
from openai import OpenAI

from app.config.settings import settings

# Use text-embedding-3-small as recommended (cheaper, still high quality)
EMBEDDING_MODEL: Literal["text-embedding-3-small"] = "text-embedding-3-small"

# Embedding dimension for text-embedding-3-small
EMBEDDING_DIMENSION = 1536


class EmbeddingService:
    """Service for computing text embeddings.

    Thread-safe singleton pattern. Initializes OpenAI client on first use.
    """

    _instance: "EmbeddingService | None" = None
    _client: OpenAI | None = None

    def __new__(cls) -> "EmbeddingService":
        """Singleton pattern."""
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def __init__(self) -> None:
        """Initialize OpenAI client if not already done."""
        if EmbeddingService._client is None:
            api_key = settings.openai_api_key
            if not api_key:
                raise RuntimeError(
                    "OPENAI_API_KEY not set. Embeddings require OpenAI API key. "
                    "Set OPENAI_API_KEY environment variable."
                )
            EmbeddingService._client = OpenAI(api_key=api_key)
            logger.info(f"Initialized EmbeddingService with model={EMBEDDING_MODEL}")

    @staticmethod
    def embed_text(text: str) -> list[float]:
        """Compute embedding for a single text string.

        Args:
            text: Text to embed

        Returns:
            List of float values representing the embedding vector

        Raises:
            RuntimeError: If OpenAI API call fails
        """
        if not text.strip():
            raise ValueError("Cannot embed empty text")

        try:
            response = EmbeddingService._client.embeddings.create(
                model=EMBEDDING_MODEL,
                input=text,
            )
        except Exception as e:
            logger.error(f"Failed to compute embedding: {e}")
            raise RuntimeError(f"Embedding computation failed: {e}") from e
        else:
            embedding = response.data[0].embedding
            logger.debug(f"Computed embedding for text (length={len(text)}, dim={len(embedding)})")
            return embedding

    @staticmethod
    def embed_batch(texts: list[str]) -> list[list[float]]:
        """Compute embeddings for multiple texts in a single API call.

        Args:
            texts: List of texts to embed

        Returns:
            List of embedding vectors (one per input text)

        Raises:
            RuntimeError: If OpenAI API call fails
        """
        if not texts:
            return []

        # Filter empty texts
        non_empty_texts = [t for t in texts if t.strip()]
        if not non_empty_texts:
            raise ValueError("Cannot embed empty text list")

        try:
            response = EmbeddingService._client.embeddings.create(
                model=EMBEDDING_MODEL,
                input=non_empty_texts,
            )
        except Exception as e:
            logger.error(f"Failed to compute batch embeddings: {e}")
            raise RuntimeError(f"Batch embedding computation failed: {e}") from e
        else:
            embeddings = [item.embedding for item in response.data]
            logger.debug(f"Computed {len(embeddings)} embeddings in batch")
            return embeddings


def compute_text_hash(text: str) -> str:
    """Compute deterministic hash of text for cache invalidation.

    Args:
        text: Text to hash

    Returns:
        SHA256 hash hex string
    """
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def get_embedding_service() -> EmbeddingService:
    """Get singleton EmbeddingService instance.

    Returns:
        EmbeddingService instance
    """
    return EmbeddingService()
