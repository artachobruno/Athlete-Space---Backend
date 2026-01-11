"""Embedding model lock for reproducibility.

This module locks the embedding model and dimensions to ensure
deterministic and reproducible embeddings.
"""

# Locked embedding model configuration
EMBEDDING_MODEL = "text-embedding-3-large"
EMBEDDING_DIM = 3072
EMBEDDING_VERSION = "v1.0"
EMBEDDING_PROVIDER = "openai"

# Batch size for embedding API calls
EMBEDDING_BATCH_SIZE = 100


def assert_embedding_lock(model: str, version: str) -> None:
    """Assert that embedding model and version match locked configuration.

    This prevents silent upgrades and ensures version compatibility.

    Args:
        model: Embedding model name to check
        version: Embedding version to check

    Raises:
        RuntimeError: If model or version doesn't match locked configuration
    """
    if model != EMBEDDING_MODEL or version != EMBEDDING_VERSION:
        raise RuntimeError(
            f"Embedding model/version mismatch — re-ingest required. "
            f"Expected model={EMBEDDING_MODEL}, version={EMBEDDING_VERSION}, "
            f"got model={model}, version={version}"
        )
