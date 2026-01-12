"""Canonical text construction for philosophy embeddings.

This module builds deterministic, stable text representations of training
philosophies for semantic retrieval. The canonical text format is designed
to be embedded and compared via cosine similarity.
"""

from pathlib import Path

from loguru import logger

from app.domains.training_plan.philosophy_loader import (
    PhilosophyDoc,
    get_philosophies_dir,
    load_philosophies,
    parse_frontmatter,
)


def build_philosophy_canonical_text(philosophy: PhilosophyDoc, body_content: str) -> str:
    """Build canonical text representation of a philosophy for embedding.

    The canonical text is deterministic, stable, and includes both metadata
    and content in a single searchable string.

    Args:
        philosophy: Parsed philosophy document
        body_content: Raw markdown body content (without frontmatter)

    Returns:
        Canonical text string ready for embedding
    """
    lines: list[str] = []

    # Header
    lines.append("Training philosophy.")

    # Core metadata (inline)
    lines.append(f"Name: {philosophy.id}")
    lines.append(f"Domain: {philosophy.domain}")
    lines.append(f"Audience: {philosophy.audience}")

    # Race types
    if philosophy.race_types:
        race_types_str = ", ".join(philosophy.race_types)
        lines.append(f"Race types: {race_types_str}")
    else:
        lines.append("Race types: all")

    # Extract intensity bias and risk from body if available
    intensity_bias = _extract_field_from_body(body_content, "intensity_bias", "moderate")
    risk_level = _extract_field_from_body(body_content, "risk_level", "medium")
    lines.append(f"Intensity bias: {intensity_bias}")
    lines.append(f"Risk level: {risk_level}")

    # Requirements and prohibitions
    if philosophy.requires:
        requires_str = ", ".join(philosophy.requires)
        lines.append(f"Requires: {requires_str}")
    if philosophy.prohibits:
        prohibits_str = ", ".join(philosophy.prohibits)
        lines.append(f"Prohibits: {prohibits_str}")

    # Core principles section
    lines.append("")
    lines.append("Core principles:")
    principles = _extract_section(body_content, "Core Principles", "##")
    if principles:
        for raw_line in principles.split("\n"):
            stripped_line = raw_line.strip()
            if stripped_line and stripped_line.startswith("-"):
                lines.append(f"  {stripped_line}")
    else:
        # Fallback: extract any bullet points
        for raw_line in body_content.split("\n"):
            stripped_line = raw_line.strip()
            if stripped_line.startswith("-") and any(
                keyword in stripped_line.lower()
                for keyword in ["principle", "focus", "emphasize", "target", "goal"]
            ):
                lines.append(f"  {stripped_line}")

    # Use cases section
    lines.append("")
    lines.append("Use cases:")
    use_cases = _extract_section(body_content, "Best-Fit Athlete", "##")
    if use_cases:
        for raw_line in use_cases.split("\n"):
            stripped_line = raw_line.strip()
            if stripped_line and stripped_line.startswith("-"):
                lines.append(f"  {stripped_line}")
    else:
        # Fallback: extract athlete archetypes
        for raw_line in body_content.split("\n"):
            stripped_line = raw_line.strip()
            if stripped_line.startswith("-") and any(
                keyword in stripped_line.lower()
                for keyword in ["athlete", "runner", "user", "suitable", "best"]
            ):
                lines.append(f"  {stripped_line}")

    # Adaptation targets
    adaptation = _extract_section(body_content, "Primary Adaptation Targets", "##")
    if adaptation:
        lines.append("")
        lines.append("Adaptation targets:")
        for raw_line in adaptation.split("\n"):
            stripped_line = raw_line.strip()
            if stripped_line and stripped_line.startswith("-"):
                lines.append(f"  {stripped_line}")

    return "\n".join(lines)


def _extract_field_from_body(body: str, field_name: str, default: str) -> str:
    """Extract a field value from frontmatter-like patterns in body.

    Args:
        body: Body content
        field_name: Field name to extract
        default: Default value if not found

    Returns:
        Extracted value or default
    """
    # Look for field: value patterns
    for line in body.split("\n"):
        if f"{field_name}:" in line.lower():
            parts = line.split(":", 1)
            if len(parts) == 2:
                return parts[1].strip()
    return default


def _extract_section(body: str, section_title: str, header_marker: str) -> str:
    """Extract a section from markdown body.

    Args:
        body: Body content
        section_title: Section title to find
        header_marker: Header marker (e.g., "##")

    Returns:
        Section content or empty string
    """
    lines = body.split("\n")
    in_section = False
    section_lines: list[str] = []

    for line in lines:
        if line.strip().startswith(header_marker) and section_title.lower() in line.lower():
            in_section = True
            continue
        if in_section:
            if line.strip().startswith(header_marker):
                break
            section_lines.append(line)

    return "\n".join(section_lines).strip()


def load_philosophy_with_body(philosophy_id: str) -> tuple[PhilosophyDoc, str]:
    """Load a philosophy document with its body content.

    Args:
        philosophy_id: Philosophy identifier

    Returns:
        Tuple of (PhilosophyDoc, body_content)

    Raises:
        ValueError: If philosophy not found
    """
    philosophies_dir = get_philosophies_dir()

    for md_file in philosophies_dir.rglob("*.md"):
        try:
            content = md_file.read_text(encoding="utf-8")
            frontmatter, body = parse_frontmatter(content)
            if str(frontmatter.get("id", "")) == philosophy_id:
                # Reconstruct PhilosophyDoc
                philosophies = load_philosophies()
                for philo in philosophies:
                    if philo.id == philosophy_id:
                        return philo, body
        except Exception as e:
            logger.debug(f"Failed to parse philosophy file {md_file}: {e}")
            continue

    raise ValueError(f"Philosophy not found: {philosophy_id}")
