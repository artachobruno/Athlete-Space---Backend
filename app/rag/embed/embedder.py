"""Deterministic embedding pipeline.

This module generates embeddings for RAG chunks using a locked model,
ensuring reproducibility through deterministic ordering and batch processing.
"""

import hashlib
from dataclasses import dataclass

import numpy as np
from openai import OpenAI

from app.config.settings import settings
from app.rag.embed.model_lock import (
    EMBEDDING_BATCH_SIZE,
    EMBEDDING_DIM,
    EMBEDDING_MODEL,
)
from app.rag.types import RagChunk


@dataclass
class EmbeddedChunk:
    """A chunk with its embedding vector."""

    chunk: RagChunk
    vector: list[float]


def hash_embedding(vector: list[float]) -> str:
    """Generate a hash of an embedding vector for reproducibility checks.

    Args:
        vector: Embedding vector

    Returns:
        SHA256 hash of the vector
    """
    vector_bytes = np.array(vector, dtype=np.float32).tobytes()
    return hashlib.sha256(vector_bytes).hexdigest()


def embed_chunks(chunks: list[RagChunk]) -> list[EmbeddedChunk]:
    """Embed a list of chunks deterministically.

    Args:
        chunks: List of chunks to embed

    Returns:
        List of EmbeddedChunk instances

    Raises:
        ValueError: If embedding API call fails
    """
    if not chunks:
        return []

    # Initialize OpenAI client
    client = OpenAI(api_key=settings.openai_api_key)

    # Sort chunks by chunk_id for deterministic ordering
    sorted_chunks = sorted(chunks, key=lambda c: c.chunk_id)

    # Extract texts in deterministic order
    texts = [chunk.text for chunk in sorted_chunks]

    # Batch embeddings
    all_embeddings: list[list[float]] = []

    for i in range(0, len(texts), EMBEDDING_BATCH_SIZE):
        batch_texts = texts[i : i + EMBEDDING_BATCH_SIZE]

        try:
            response = client.embeddings.create(
                model=EMBEDDING_MODEL,
                input=batch_texts,
            )

            batch_embeddings = [item.embedding for item in response.data]
            all_embeddings.extend(batch_embeddings)

        except Exception as e:
            raise ValueError(f"Failed to generate embeddings for batch {i}: {e}") from e

    # Verify embedding dimensions
    for idx, embedding in enumerate(all_embeddings):
        if len(embedding) != EMBEDDING_DIM:
            raise ValueError(
                f"Embedding dimension mismatch: expected {EMBEDDING_DIM}, "
                f"got {len(embedding)} for chunk {sorted_chunks[idx].chunk_id}"
            )

    # Create EmbeddedChunk instances in same order
    embedded_chunks: list[EmbeddedChunk] = []

    for chunk, vector in zip(sorted_chunks, all_embeddings, strict=True):
        embedded_chunks.append(EmbeddedChunk(chunk=chunk, vector=vector))

    return embedded_chunks


def embed_query(query: str) -> list[float]:
    """Embed a single query string.

    Args:
        query: Query text to embed

    Returns:
        Embedding vector

    Raises:
        ValueError: If embedding API call fails
    """
    client = OpenAI(api_key=settings.openai_api_key)

    def _validate_embedding(emb: list[float]) -> list[float]:
        if len(emb) != EMBEDDING_DIM:
            raise ValueError(
                f"Embedding dimension mismatch: expected {EMBEDDING_DIM}, "
                f"got {len(emb)}"
            )
        return emb

    try:
        response = client.embeddings.create(
            model=EMBEDDING_MODEL,
            input=[query],
        )

        embedding = response.data[0].embedding
        return _validate_embedding(embedding)

    except Exception as e:
        raise ValueError(f"Failed to generate query embedding: {e}") from e
