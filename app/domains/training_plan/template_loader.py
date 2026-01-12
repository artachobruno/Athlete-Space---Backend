"""Template loader with embeddings.

This module loads all templates from the embedding cache and initializes
the template library for embedding-only selection.
"""

import json
import re
from pathlib import Path

import yaml
from loguru import logger

from app.domains.training_plan.models import SessionTemplate
from app.domains.training_plan.session_template_selector import (
    get_templates_dir,
    parse_template_file,
)
from app.domains.training_plan.template_selector_embedding import (
    EmbeddedTemplate,
    initialize_template_library,
)

# Cache directory
# Path from app/domains/training_plan/template_loader.py to project root:
# parent = app/domains/training_plan/
# parent.parent = app/domains/
# parent.parent.parent = app/
# parent.parent.parent.parent = project root
CACHE_DIR = Path(__file__).parent.parent.parent.parent / "data" / "embeddings"
TEMPLATES_CACHE = CACHE_DIR / "templates.json"


def _get_doc_type(file_path: Path) -> str | None:
    """Extract doc_type from template file frontmatter.

    Args:
        file_path: Path to template file

    Returns:
        doc_type string or None if not found
    """
    try:
        content = file_path.read_text(encoding="utf-8")
        frontmatter_pattern = r"^---\s*\n(.*?)\n---\s*\n(.*)$"
        match = re.match(frontmatter_pattern, content, re.DOTALL)
        if not match:
            return None

        frontmatter_text = match.group(1)
        frontmatter = yaml.safe_load(frontmatter_text)
        if isinstance(frontmatter, dict):
            return frontmatter.get("doc_type")
    except Exception as e:
        logger.debug(f"Failed to extract doc_type from {file_path}: {e}")
    return None


def load_templates_with_embeddings() -> list[EmbeddedTemplate]:
    """Load all templates with embeddings from cache.

    Returns:
        List of EmbeddedTemplate objects

    Raises:
        RuntimeError: If cache not found or template library is empty
    """
    if not TEMPLATES_CACHE.exists():
        raise RuntimeError(
            f"Template embeddings cache not found: {TEMPLATES_CACHE}\n"
            "Run: python scripts/precompute_embeddings.py templates"
        )

    # Load cache
    with TEMPLATES_CACHE.open("r", encoding="utf-8") as f:
        cache_data = json.load(f)

    if not cache_data:
        raise RuntimeError(
            "Template embeddings cache is empty. "
            "Run: python scripts/precompute_embeddings.py --templates"
        )

    # Load template files to get full template objects
    templates_dir = get_templates_dir()
    template_sets_by_key: dict[str, tuple] = {}  # key -> (template_set, template)

    # Build mapping of template IDs to template objects
    for domain_dir in templates_dir.iterdir():
        if not domain_dir.is_dir():
            continue

        for philosophy_dir in domain_dir.iterdir():
            if not philosophy_dir.is_dir():
                continue

            for template_file in philosophy_dir.glob("*.md"):
                # Skip session_template_pack files (they use template_sets format, not template_spec)
                doc_type = _get_doc_type(template_file)
                if doc_type == "session_template_pack":
                    logger.debug(f"Skipping template pack file {template_file.name} (not supported by parser)")
                    continue

                try:
                    template_set = parse_template_file(template_file)

                    for template in template_set.templates:
                        template_id = (
                            f"{template_set.domain}__{template_set.philosophy_id}__"
                            f"{template_set.phase}__{template_set.session_type}__{template.template_id}"
                        )
                        template_sets_by_key[template_id] = (template_set, template)

                except Exception as e:
                    logger.warning(f"Failed to parse template file {template_file}: {e}")
                    continue

    # Build EmbeddedTemplate objects from cache
    embedded_templates: list[EmbeddedTemplate] = []

    for item_data in cache_data:
        template_id = item_data["id"]
        embedding = item_data["embedding"]
        metadata = item_data.get("metadata", {})

        # Find matching template object
        if template_id not in template_sets_by_key:
            logger.warning(f"Template {template_id} not found in template files, skipping")
            continue

        _template_set, template = template_sets_by_key[template_id]

        # Verify metadata matches
        if metadata.get("template_id") != template.template_id:
            logger.warning(
                f"Template ID mismatch for {template_id}: "
                f"cache={metadata.get('template_id')}, file={template.template_id}"
            )
            continue

        embedded_template = EmbeddedTemplate(
            template=template,
            embedding=embedding,
            template_id=template_id,
            session_type=metadata.get("session_type", ""),
        )

        embedded_templates.append(embedded_template)

    if not embedded_templates:
        raise RuntimeError(
            "No templates loaded from cache. "
            "Ensure templates are precomputed with embeddings."
        )

    logger.info(f"Loaded {len(embedded_templates)} templates with embeddings")
    return embedded_templates


def initialize_template_library_from_cache() -> None:
    """Initialize template library from cache at startup.

    This should be called once at application startup to load all templates
    with embeddings into memory.

    Raises:
        RuntimeError: If cache not found or template library is empty
    """
    templates = load_templates_with_embeddings()
    initialize_template_library(templates)
    logger.info("Template library initialized from cache")


def load_all_session_templates(domain: str) -> list[tuple[SessionTemplate, list[float]]]:
    """Load all session templates for a domain (no filters).

    Loads every template under /templates/{domain}/ and returns tuples of
    (SessionTemplate, embedding). Skips only invalid YAML or unreadable files.

    Does NOT filter by:
    - phase
    - philosophy
    - race distance
    - audience
    - session type

    Args:
        domain: Training domain (e.g., "running")

    Returns:
        List of tuples (SessionTemplate, embedding) for the domain

    Raises:
        RuntimeError: If no templates found (configuration error)
    """
    # Load all embedded templates from cache
    embedded_templates = load_templates_with_embeddings()

    # Filter by domain and extract (template, embedding) tuples
    # Check if template belongs to the requested domain
    # The template_id format is: {domain}__{philosophy_id}__{phase}__{session_type}__{template_id}
    templates_with_embeddings: list[tuple[SessionTemplate, list[float]]] = [
        (embedded_template.template, embedded_template.embedding)
        for embedded_template in embedded_templates
        if embedded_template.template_id.startswith(f"{domain}__")
    ]

    if not templates_with_embeddings:
        raise RuntimeError(
            f"No templates found for domain '{domain}'. "
            "Ensure templates are precomputed with embeddings."
        )

    logger.info(f"Loaded {len(templates_with_embeddings)} templates for domain '{domain}' (no filters)")
    return templates_with_embeddings
