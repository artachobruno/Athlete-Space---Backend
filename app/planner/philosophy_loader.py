"""Philosophy document loader for hierarchical planner.

This module loads training philosophy documents from RAG markdown files.
It parses frontmatter and provides structured access to philosophy metadata.

No validation logic - pure parsing and loading.
"""

import re
from dataclasses import dataclass
from pathlib import Path

import yaml
from loguru import logger


@dataclass(frozen=True)
class PhilosophyDoc:
    """Immutable philosophy document with parsed metadata.

    Attributes:
        id: Philosophy identifier (e.g., "daniels", "pfitzinger")
        domain: Domain type ("running" | "ultra")
        race_types: List of race types this philosophy supports
        audience: Target audience ("beginner" | "intermediate" | "advanced" | "all")
        priority: Priority value for selection (higher = more preferred)
        version: Version string (for tie-breaking)
        requires: List of required athlete flags
        prohibits: List of prohibited athlete flags
    """

    id: str
    domain: str
    race_types: list[str]
    audience: str
    priority: int
    version: str
    requires: list[str]
    prohibits: list[str]


def _get_philosophies_dir() -> Path:
    """Get path to philosophies directory.

    Returns:
        Path to data/rag/planning/philosophies directory
    """
    project_root = Path(__file__).parent.parent.parent
    return project_root / "data" / "rag" / "planning" / "philosophies"


def _parse_frontmatter(content: str) -> tuple[dict[str, str | int | list[str]], str]:
    """Parse YAML frontmatter from markdown content.

    Args:
        content: Full markdown file content

    Returns:
        Tuple of (frontmatter dict, body content)

    Raises:
        ValueError: If frontmatter is missing or invalid
    """
    frontmatter_pattern = r"^---\s*\n(.*?)\n---\s*\n(.*)$"
    match = re.match(frontmatter_pattern, content, re.DOTALL)

    if not match:
        raise ValueError("Missing or malformed YAML frontmatter")

    frontmatter_text = match.group(1)
    body = match.group(2).strip()

    try:
        frontmatter = yaml.safe_load(frontmatter_text)
        if not isinstance(frontmatter, dict):
            raise TypeError("Frontmatter must be a YAML dictionary")
    except yaml.YAMLError as e:
        raise ValueError(f"Invalid YAML frontmatter: {e}") from e

    return frontmatter, body


def _normalize_race_types(race_types: str | list[str]) -> list[str]:
    """Normalize race types to a list of strings.

    Args:
        race_types: Race types as string or list

    Returns:
        List of normalized race type strings
    """
    if isinstance(race_types, str):
        return [race_types]
    if isinstance(race_types, list):
        return [str(rt) for rt in race_types]
    return []


def _normalize_domain(category: str | None, domain: str | None) -> str:
    """Normalize domain from category or domain field.

    Philosophy files use 'category' field (running/ultra) which maps to domain.

    Args:
        category: Category field from frontmatter
        domain: Domain field from frontmatter (fallback)

    Returns:
        Normalized domain string ("running" | "ultra")
    """
    if category:
        if category == "ultra":
            return "ultra"
        if category == "running":
            return "running"
    if domain:
        if domain == "ultra":
            return "ultra"
        if domain == "running":
            return "running"
    return "running"  # Default to running


def load_philosophies() -> list[PhilosophyDoc]:
    """Load all philosophy files from the philosophies directory.

    Returns:
        List of parsed PhilosophyDoc instances

    Raises:
        RuntimeError: If loading fails
    """
    philosophies_dir = _get_philosophies_dir()

    if not philosophies_dir.exists():
        raise RuntimeError(f"Philosophies directory not found: {philosophies_dir}")

    loaded_philosophies: list[PhilosophyDoc] = []

    for md_file in philosophies_dir.rglob("*.md"):
        try:
            content = md_file.read_text(encoding="utf-8")
            frontmatter, _body = _parse_frontmatter(content)

            # Extract required fields
            philosophy_id = str(frontmatter.get("id", ""))
            if not philosophy_id:
                logger.warning(f"Philosophy file {md_file} missing 'id' field, skipping")
                continue

            # Normalize domain from category or domain field
            category_raw = frontmatter.get("category")
            category: str | None = str(category_raw) if isinstance(category_raw, str) else None
            domain_field_raw = frontmatter.get("domain")
            domain_field: str | None = str(domain_field_raw) if isinstance(domain_field_raw, str) else None
            domain = _normalize_domain(category, domain_field)

            # Extract race types
            race_types_raw = frontmatter.get("race_types", [])
            if isinstance(race_types_raw, (str, list)):
                race_types = _normalize_race_types(race_types_raw)
            else:
                race_types = []

            # Extract audience (default to "all" if not specified)
            audience_raw = frontmatter.get("audience", "all")
            audience = str(audience_raw)

            # Extract priority (default to 50)
            priority_raw = frontmatter.get("priority", 50)
            if isinstance(priority_raw, str):
                try:
                    priority = int(priority_raw)
                except ValueError:
                    priority = 50
            elif isinstance(priority_raw, int):
                priority = priority_raw
            else:
                priority = 50

            # Extract version (default to "1.0")
            version = str(frontmatter.get("version", "1.0"))

            # Extract requires and prohibits (default to empty lists)
            requires_raw = frontmatter.get("requires", [])
            if isinstance(requires_raw, list):
                requires = [str(r) for r in requires_raw]
            elif isinstance(requires_raw, str):
                requires = [requires_raw] if requires_raw else []
            else:
                requires = []

            prohibits_raw = frontmatter.get("prohibits", [])
            if isinstance(prohibits_raw, list):
                prohibits = [str(p) for p in prohibits_raw]
            elif isinstance(prohibits_raw, str):
                prohibits = [prohibits_raw] if prohibits_raw else []
            else:
                prohibits = []

            doc = PhilosophyDoc(
                id=philosophy_id,
                domain=domain,
                race_types=race_types,
                audience=audience,
                priority=priority,
                version=version,
                requires=requires,
                prohibits=prohibits,
            )

            loaded_philosophies.append(doc)

        except ValueError as e:
            logger.warning(f"Failed to parse philosophy file {md_file}: {e}")
            continue
        except Exception as e:
            logger.error(f"Unexpected error loading philosophy file {md_file}: {e}")
            continue

    logger.debug(f"Loaded {len(loaded_philosophies)} philosophy documents")
    return loaded_philosophies
