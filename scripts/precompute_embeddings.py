"""Precompute embeddings for philosophies and week structures.

This script:
1. Loads raw markdown files
2. Builds canonical text representations
3. Computes embeddings via OpenAI API
4. Saves to JSON cache files
5. Uses content hashing to skip unchanged items

Run during build or on startup (with guard).
"""

import json
import sys
from pathlib import Path

# Add project root to Python path (must be absolute for Render/production)
script_dir = Path(__file__).parent.resolve()
project_root = script_dir.parent.resolve()

# Verify project root contains app directory or pyproject.toml
if not (project_root / "app").exists() and not (project_root / "pyproject.toml").exists():
    # If parent doesn't have app/ or pyproject.toml, try current working directory
    cwd = Path.cwd().resolve()
    if (cwd / "app").exists() or (cwd / "pyproject.toml").exists():
        project_root = cwd
    else:
        # Last resort: try going up one more level (for cases where script is in src/scripts/)
        parent_parent = script_dir.parent.parent.resolve()
        if (parent_parent / "app").exists() or (parent_parent / "pyproject.toml").exists():
            project_root = parent_parent

# Ensure project root is in path
project_root_str = str(project_root)
if project_root_str not in sys.path:
    sys.path.insert(0, project_root_str)

import typer
from loguru import logger

from app.domains.training_plan.philosophy_embedding import (
    build_philosophy_canonical_text,
    load_philosophy_with_body,
)
from app.domains.training_plan.philosophy_loader import load_philosophies
from app.domains.training_plan.week_structure import get_structures_dir, load_structures_from_philosophy
from app.domains.training_plan.week_structure_embedding import build_week_structure_canonical_text
from app.embeddings.embedding_service import compute_text_hash, get_embedding_service
from app.planning.structure.types import StructureSpec

app = typer.Typer()

# Cache directory
CACHE_DIR = Path(__file__).parent.parent / "data" / "embeddings"
PHILOSOPHIES_CACHE = CACHE_DIR / "philosophies.json"
WEEK_STRUCTURES_CACHE = CACHE_DIR / "week_structures.json"


def _ensure_cache_dir() -> None:
    """Ensure cache directory exists."""
    CACHE_DIR.mkdir(parents=True, exist_ok=True)


def _load_cache(cache_file: Path) -> dict[str, dict]:
    """Load existing cache file.

    Args:
        cache_file: Path to cache file

    Returns:
        Dictionary mapping item IDs to cached data
    """
    if not cache_file.exists():
        return {}

    try:
        with Path(cache_file).open("r", encoding="utf-8") as f:
            data = json.load(f)
            return {item["id"]: item for item in data}
    except Exception as e:
        logger.warning(f"Failed to load cache {cache_file}: {e}")
        return {}


def _save_cache(cache_file: Path, items: list[dict]) -> None:
    """Save cache to file.

    Args:
        cache_file: Path to cache file
        items: List of item dictionaries
    """
    with Path(cache_file).open("w", encoding="utf-8") as f:
        json.dump(items, f, indent=2, ensure_ascii=False)
    logger.info(f"Saved {len(items)} items to {cache_file}")


def _compute_philosophy_embeddings(force: bool = False) -> int:
    """Precompute embeddings for all philosophies.

    Args:
        force: If True, recompute all embeddings even if unchanged

    Returns:
        Number of embeddings computed
    """
    logger.info("Starting philosophy embedding precomputation")
    _ensure_cache_dir()

    # Load existing cache
    cache = _load_cache(PHILOSOPHIES_CACHE)

    # Load all philosophies
    philosophies = load_philosophies()
    embedding_service = get_embedding_service()

    items_to_compute: list[tuple[str, str]] = []  # (id, canonical_text)
    items_to_skip: list[dict] = []

    for philosophy in philosophies:
        try:
            # Load body content
            _, body = load_philosophy_with_body(philosophy.id)

            # Build canonical text
            canonical_text = build_philosophy_canonical_text(philosophy, body)
            text_hash = compute_text_hash(canonical_text)

            # Check cache
            cached_item = cache.get(philosophy.id)
            if cached_item and not force:
                cached_hash = cached_item.get("text_hash")
                if cached_hash == text_hash:
                    items_to_skip.append(cached_item)
                    logger.debug(f"Skipping unchanged philosophy: {philosophy.id}")
                    continue

            items_to_compute.append((philosophy.id, canonical_text))

        except Exception as e:
            logger.error(f"Failed to process philosophy {philosophy.id}: {e}")
            continue

    # Compute embeddings in batch
    computed_count = 0
    if items_to_compute:
        texts = [text for _, text in items_to_compute]
        ids = [item_id for item_id, _ in items_to_compute]

        logger.info(f"Computing {len(texts)} philosophy embeddings")
        embeddings = embedding_service.embed_batch(texts)

        # Build cache entries
        for item_id, canonical_text, embedding in zip(ids, texts, embeddings, strict=True):
            text_hash = compute_text_hash(canonical_text)
            cache[item_id] = {
                "id": item_id,
                "embedding": embedding,
                "text_hash": text_hash,
                "metadata": {
                    "domain": next(p.domain for p in philosophies if p.id == item_id),
                    "audience": next(p.audience for p in philosophies if p.id == item_id),
                    "race_types": next(p.race_types for p in philosophies if p.id == item_id),
                },
            }
            computed_count += 1

    # Save cache
    all_items = list(cache.values())
    _save_cache(PHILOSOPHIES_CACHE, all_items)

    logger.info(
        f"Philosophy embeddings: {computed_count} computed, {len(items_to_skip)} skipped, "
        f"{len(all_items)} total"
    )
    return computed_count


def _process_structure_spec(
    spec: StructureSpec,
    cache: dict[str, dict],
    force: bool,
    items_to_compute: list[tuple[str, str, StructureSpec]],
    items_to_skip: list[dict],
) -> None:
    """Process a single structure spec for embedding.

    Args:
        spec: Structure specification
        cache: Existing cache dictionary
        force: If True, recompute even if unchanged
        items_to_compute: List to append items that need embedding
        items_to_skip: List to append items that can be skipped
    """
    try:
        canonical_text = build_week_structure_canonical_text(spec)
        text_hash = compute_text_hash(canonical_text)

        cached_item = cache.get(spec.metadata.id)
        if cached_item and not force:
            cached_hash = cached_item.get("text_hash")
            if cached_hash == text_hash:
                items_to_skip.append(cached_item)
                logger.debug(f"Skipping unchanged structure: {spec.metadata.id}")
                return

        items_to_compute.append((spec.metadata.id, canonical_text, spec))
    except Exception as e:
        logger.error(f"Failed to process structure {spec.metadata.id}: {e}")


def _compute_week_structure_embeddings(force: bool = False) -> int:
    """Precompute embeddings for all week structures.

    Args:
        force: If True, recompute all embeddings even if unchanged

    Returns:
        Number of embeddings computed
    """
    logger.info("Starting week structure embedding precomputation")
    _ensure_cache_dir()

    # Load existing cache
    cache = _load_cache(WEEK_STRUCTURES_CACHE)

    structures_dir = get_structures_dir()
    embedding_service = get_embedding_service()

    items_to_compute: list[tuple[str, str, StructureSpec]] = []  # (id, canonical_text, spec)
    items_to_skip: list[dict] = []

    for domain_dir in structures_dir.iterdir():
        if not domain_dir.is_dir():
            continue

        for philosophy_dir in domain_dir.iterdir():
            if not philosophy_dir.is_dir():
                continue

            philosophy_id = philosophy_dir.name
            try:
                structures = load_structures_from_philosophy(domain_dir.name, philosophy_id)
            except Exception as e:
                logger.warning(f"Failed to load structures for {domain_dir.name}/{philosophy_id}: {e}")
                continue

            for spec in structures:
                _process_structure_spec(spec, cache, force, items_to_compute, items_to_skip)

    # Compute embeddings in batch
    computed_count = 0
    if items_to_compute:
        texts = [text for _, text, _ in items_to_compute]
        ids = [item_id for item_id, _, _ in items_to_compute]
        specs = [spec for _, _, spec in items_to_compute]

        logger.info(f"Computing {len(texts)} week structure embeddings")
        embeddings = embedding_service.embed_batch(texts)

        # Build cache entries
        for item_id, canonical_text, spec, embedding in zip(ids, texts, specs, embeddings, strict=True):
            text_hash = compute_text_hash(canonical_text)
            cache[item_id] = {
                "id": item_id,
                "embedding": embedding,
                "text_hash": text_hash,
                "metadata": {
                    "philosophy_id": spec.metadata.philosophy_id,
                    "phase": spec.metadata.phase,
                    "audience": spec.metadata.audience,
                    "race_types": spec.metadata.race_types,
                    "days_to_race_min": spec.metadata.days_to_race_min,
                    "days_to_race_max": spec.metadata.days_to_race_max,
                },
            }
            computed_count += 1

    # Save cache
    all_items = list(cache.values())
    _save_cache(WEEK_STRUCTURES_CACHE, all_items)

    logger.info(
        f"Week structure embeddings: {computed_count} computed, {len(items_to_skip)} skipped, "
        f"{len(all_items)} total"
    )
    return computed_count


@app.command()
def all(
    force: bool = typer.Option(False, "--force", help="Recompute all embeddings even if unchanged"),
) -> None:
    """Precompute all embeddings (philosophies and week structures)."""
    logger.info("Starting full embedding precomputation")
    philo_count = _compute_philosophy_embeddings(force=force)
    struct_count = _compute_week_structure_embeddings(force=force)
    logger.info(f"Completed: {philo_count} philosophy + {struct_count} structure embeddings")


@app.command()
def philosophies(
    force: bool = typer.Option(False, "--force", help="Recompute all embeddings even if unchanged"),
) -> None:
    """Precompute philosophy embeddings only."""
    _compute_philosophy_embeddings(force=force)


@app.command()
def week_structures(
    force: bool = typer.Option(False, "--force", help="Recompute all embeddings even if unchanged"),
) -> None:
    """Precompute week structure embeddings only."""
    _compute_week_structure_embeddings(force=force)


if __name__ == "__main__":
    app()
