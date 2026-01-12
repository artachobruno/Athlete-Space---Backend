"""Template validation for training plan philosophies.

This module validates that all enabled philosophies have:
- Template root directories exist
- Minimum phase/session coverage exists

Fail fast before runtime.
"""

import re
from pathlib import Path

import yaml
from loguru import logger

from app.domains.training_plan.errors import TemplateValidationError
from app.domains.training_plan.philosophy_loader import PhilosophyDoc, load_philosophies
from app.domains.training_plan.session_template_selector import get_templates_dir

# Required phases for all philosophies
REQUIRED_PHASES = {"build", "taper"}

# Required core session types (all philosophies must have these)
REQUIRED_CORE_SESSION_TYPES = {"easy", "long"}

# Required quality session types (at least one must exist)
REQUIRED_QUALITY_SESSION_TYPES = {"threshold", "vo2"}


def _get_template_dir_for_philosophy(philosophy: PhilosophyDoc) -> Path:
    """Get template directory path for a philosophy.

    Args:
        philosophy: Philosophy document

    Returns:
        Path to philosophy's template directory
    """
    templates_dir = get_templates_dir()
    return templates_dir / philosophy.domain / philosophy.id


def _extract_template_metadata(file_path: Path) -> tuple[str | None, str | None]:
    """Extract phase and session_type from template filename.

    Template files follow pattern: <philosophy_id>__<race>__<audience>__<phase>__<session_type>__v*.md

    Args:
        file_path: Path to template file

    Returns:
        Tuple of (phase, session_type) or (None, None) if pattern doesn't match
    """
    parts = file_path.stem.split("__")
    if len(parts) >= 5:
        phase = parts[3]
        session_type = parts[4]
        return phase, session_type
    return None, None


def _extract_session_types_from_template_pack(file_path: Path) -> set[str]:
    """Extract session types from a template pack file.

    Template packs have doc_type "session_template_pack" and contain
    multiple session types in a template_sets block.

    Args:
        file_path: Path to template pack file

    Returns:
        Set of session types found in the file
    """
    try:
        content = file_path.read_text(encoding="utf-8")
        parts = content.split("---")
        if len(parts) < 3:
            return set()

        body = parts[2]
        pattern = r"```template_sets\s*\n(.*?)```"
        match = re.search(pattern, body, re.DOTALL)
        if not match:
            return set()

        spec_dict = yaml.safe_load(match.group(1))
        if not isinstance(spec_dict, dict):
            return set()

        sets_list = spec_dict.get("sets", [])
        if not isinstance(sets_list, list):
            return set()

        session_types: set[str] = set()
        for item in sets_list:
            if isinstance(item, dict):
                session_type = item.get("session_type")
                if isinstance(session_type, str):
                    session_types.add(session_type)
    except Exception:
        return set()
    else:
        return session_types


def _get_template_coverage(template_dir: Path) -> dict[str, set[str]]:
    """Get phase/session_type coverage for a philosophy's templates.

    Handles both formats:
    - Files with session_type in filename: <philosophy>__<race>__<audience>__<phase>__<session_type>__v*.md (6+ parts)
    - Template packs: <philosophy>__<race>__<audience>__<phase>__v*.md (5 parts, last part is version)

    Args:
        template_dir: Path to philosophy's template directory

    Returns:
        Dictionary mapping phases to sets of session types
    """
    coverage: dict[str, set[str]] = {}

    for template_file in template_dir.glob("*.md"):
        parts = template_file.stem.split("__")

        # Need at least 4 parts: <philosophy>__<race>__<audience>__<phase>
        if len(parts) < 4:
            continue

        phase = parts[3]

        # Check if file has session_type in filename (6+ parts: includes session_type before version)
        if len(parts) >= 6:
            # Format: <philosophy>__<race>__<audience>__<phase>__<session_type>__v*
            session_type = parts[4]
            if phase not in coverage:
                coverage[phase] = set()
            coverage[phase].add(session_type)
        elif len(parts) == 5:
            # Format: <philosophy>__<race>__<audience>__<phase>__v* (template pack)
            # Extract session types from file content
            pack_session_types = _extract_session_types_from_template_pack(template_file)
            if phase not in coverage:
                coverage[phase] = set()
            coverage[phase].update(pack_session_types)

    return coverage


def validate_philosophy_templates(philosophy: PhilosophyDoc) -> None:
    """Validate template coverage for a single philosophy.

    Args:
        philosophy: Philosophy document to validate

    Raises:
        TemplateValidationError: If validation fails
    """
    template_dir = _get_template_dir_for_philosophy(philosophy)

    # Check template root exists
    if not template_dir.exists():
        raise TemplateValidationError(
            f"Template directory not found for philosophy {philosophy.id} "
            f"(domain={philosophy.domain}): {template_dir}"
        )

    # Get coverage
    coverage = _get_template_coverage(template_dir)

    # Check required phases exist
    found_phases = set(coverage.keys())
    missing_phases = REQUIRED_PHASES - found_phases
    if missing_phases:
        raise TemplateValidationError(
            f"Missing required phases for philosophy {philosophy.id} "
            f"(domain={philosophy.domain}): {sorted(missing_phases)}. "
            f"Found phases: {sorted(found_phases)}"
        )

    # Check minimum session type coverage for each phase
    for phase in REQUIRED_PHASES:
        phase_session_types = coverage.get(phase, set())

        # Check core session types
        missing_core = REQUIRED_CORE_SESSION_TYPES - phase_session_types
        if missing_core:
            raise TemplateValidationError(
                f"Missing required core session types for philosophy {philosophy.id} "
                f"(domain={philosophy.domain}, phase={phase}): {sorted(missing_core)}. "
                f"Found session types: {sorted(phase_session_types)}"
            )

        # Check at least one quality session type exists
        found_quality = REQUIRED_QUALITY_SESSION_TYPES & phase_session_types
        if not found_quality:
            raise TemplateValidationError(
                f"Missing required quality session types for philosophy {philosophy.id} "
                f"(domain={philosophy.domain}, phase={phase}): "
                f"must have at least one of {sorted(REQUIRED_QUALITY_SESSION_TYPES)}. "
                f"Found session types: {sorted(phase_session_types)}"
            )


def validate_all_philosophy_templates() -> None:
    """Validate template coverage for all loaded philosophies.

    Fails fast if any philosophy has missing templates or insufficient coverage.

    Raises:
        TemplateValidationError: If any validation fails
    """
    logger.info("Validating template coverage for all philosophies...")
    philosophies = load_philosophies()

    if not philosophies:
        raise TemplateValidationError("No philosophies loaded - cannot validate templates")

    errors: list[str] = []

    for philosophy in philosophies:
        try:
            validate_philosophy_templates(philosophy)
            logger.debug(
                f"Template validation passed for philosophy {philosophy.id} (domain={philosophy.domain})"
            )
        except TemplateValidationError as e:
            errors.append(str(e))

    if errors:
        error_message = f"Template validation failed for {len(errors)} philosophy/philosophies:\n"
        error_message += "\n".join(f"  - {error}" for error in errors)
        raise TemplateValidationError(error_message)

    logger.info(f"Template validation passed for {len(philosophies)} philosophy/philosophies")
