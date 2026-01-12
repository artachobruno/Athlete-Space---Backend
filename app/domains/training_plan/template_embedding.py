"""Canonical text construction for template embeddings.

This module builds deterministic, stable text representations of session templates
for semantic retrieval. The canonical text format is designed to be embedded and
compared via cosine similarity.
"""

from app.domains.training_plan.models import SessionTemplate, SessionTemplateSet


def build_template_canonical_text(template_set: SessionTemplateSet, template: SessionTemplate) -> str:
    """Build canonical text representation of a template for embedding.

    The canonical text is deterministic, stable, and includes both metadata
    and template information in a single searchable string.

    Args:
        template_set: Template set containing the template
        template: Template to build text for

    Returns:
        Canonical text string ready for embedding
    """
    lines: list[str] = []

    # Header
    lines.append("Training session template.")

    # Core metadata
    lines.append(f"Domain: {template_set.domain}")
    lines.append(f"Philosophy: {template_set.philosophy_id}")
    lines.append(f"Phase: {template_set.phase}")
    lines.append(f"Session type: {template_set.session_type}")

    # Race types
    if template_set.race_types:
        race_types_str = ", ".join(template_set.race_types)
        lines.append(f"Race types: {race_types_str}")

    # Audience
    lines.append(f"Audience: {template_set.audience}")

    # Template details
    lines.append(f"Template ID: {template.template_id}")
    lines.append(f"Template kind: {template.kind}")

    if template.description_key:
        lines.append(f"Description: {template.description_key}")

    # Template parameters
    if template.params:
        lines.append("Parameters:")
        for key, value in template.params.items():
            lines.append(f"  {key}: {value}")

    # Template constraints
    if template.constraints:
        lines.append("Constraints:")
        for key, value in template.constraints.items():
            lines.append(f"  {key}: {value}")

    # Tags
    if template.tags:
        tags_str = ", ".join(template.tags)
        lines.append(f"Tags: {tags_str}")

    return "\n".join(lines)
