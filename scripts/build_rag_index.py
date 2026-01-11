"""Build RAG index artifacts (offline embedding generation).

This script is run ONCE during build/CI, never at runtime.
It generates embeddings, builds indexes, and saves all artifacts to disk.

Usage:
    python scripts/build_rag_index.py
"""

import sys
from pathlib import Path

# Add project root to path
_project_root = Path(__file__).parent.parent
if str(_project_root) not in sys.path:
    sys.path.insert(0, str(_project_root))

from loguru import logger

from app.rag.embed.model_lock import EMBEDDING_VERSION
from app.rag.pipeline import RagPipeline

# Output directory for artifacts
OUTPUT_DIR = Path("data/rag_artifacts")


def build_rag_index() -> None:
    """Build RAG index and persist artifacts.

    This function:
    1. Loads corpus from data/rag
    2. Normalizes + chunks documents
    3. Generates embeddings
    4. Builds vector and metadata indexes
    5. Writes artifacts to data/rag_artifacts

    Raises:
        RuntimeError: If corpus not found or build fails
    """
    # Determine corpus paths
    project_root = Path(__file__).parent.parent
    corpus_dir = project_root / "data" / "rag"
    metadata_path = corpus_dir / "metadata.yaml"

    if not corpus_dir.exists() or not metadata_path.exists():
        raise RuntimeError(
            f"RAG corpus not found: corpus_dir={corpus_dir}, metadata_path={metadata_path}"
        )

    logger.info("Starting RAG index build", corpus_dir=str(corpus_dir))

    # Initialize pipeline
    pipeline = RagPipeline(corpus_dir=corpus_dir, metadata_path=metadata_path)

    # Build and persist artifacts
    output_dir = project_root / OUTPUT_DIR
    pipeline.build_and_persist(output_dir=output_dir, embedding_version=EMBEDDING_VERSION)

    logger.info(
        "RAG index build complete",
        output_dir=str(output_dir),
        embedding_version=EMBEDDING_VERSION,
    )


if __name__ == "__main__":
    try:
        build_rag_index()
        logger.info("Build script completed successfully")
        sys.exit(0)
    except Exception as e:
        logger.error("Build script failed", error=str(e), exc_info=True)
        sys.exit(1)
