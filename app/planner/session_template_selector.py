"""Session template selector for B5 stage.

This module loads session templates from RAG documents and selects templates
deterministically for each DistributedDay based on:
- Philosophy (locked from B2.5)
- Domain (running/ultra)
- Race distance
- Audience
- Phase (build/taper; from structure file)
- Day type (threshold/vo2/long/easy/easy_plus_strides)

No LLM. No generation. Pure RAG lookup with deterministic rotation.
"""

import re
from pathlib import Path

import yaml
from loguru import logger

from app.planner.enums import DayType
from app.planner.errors import TemplateSelectionError
from app.planner.models import (
    DistributedDay,
    PlannedSession,
    PlanRuntimeContext,
    SessionTemplate,
    SessionTemplateSet,
)


class TemplateParseError(RuntimeError):
    """Raised when template parsing fails.

    Attributes:
        code: Error code
        message: Error message
    """

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        self.message = message
        super().__init__(f"{code}: {message}")


# Session type to DayType mapping (for template lookup)
# This maps the session_type in template files to DayType enum
_SESSION_TYPE_TO_DAY_TYPE: dict[str, DayType] = {
    "easy": DayType.EASY,
    "easy_plus_strides": DayType.EASY,
    "threshold": DayType.QUALITY,
    "vo2": DayType.QUALITY,
    "long": DayType.LONG,
    "rest": DayType.REST,
    "race": DayType.RACE,
    "cross": DayType.CROSS,
}


def _get_templates_dir() -> Path:
    """Get path to templates directory.

    Returns:
        Path to data/rag/planning/templates directory
    """
    project_root = Path(__file__).parent.parent.parent
    return project_root / "data" / "rag" / "planning" / "templates"


def _parse_frontmatter(content: str) -> tuple[dict[str, str | int | list[str]], str]:
    """Parse YAML frontmatter from markdown content.

    Args:
        content: Full markdown file content

    Returns:
        Tuple of (frontmatter dict, body content)

    Raises:
        TemplateParseError: If frontmatter is missing or invalid
    """
    frontmatter_pattern = r"^---\s*\n(.*?)\n---\s*\n(.*)$"
    match = re.match(frontmatter_pattern, content, re.DOTALL)

    if not match:
        raise TemplateParseError("MISSING_FRONTMATTER", "Missing or malformed YAML frontmatter")

    frontmatter_text = match.group(1)
    body = match.group(2).strip()

    try:
        frontmatter = yaml.safe_load(frontmatter_text)
        if not isinstance(frontmatter, dict):
            raise TemplateParseError("INVALID_FRONTMATTER", "Frontmatter must be a YAML dictionary")
    except yaml.YAMLError as e:
        raise TemplateParseError("INVALID_FRONTMATTER_YAML", f"Invalid YAML frontmatter: {e}") from e

    return frontmatter, body


def _extract_template_spec_block(body: str) -> str:
    """Extract template_spec code block from markdown body.

    Args:
        body: Markdown body content (after frontmatter)

    Returns:
        Content of the template_spec block

    Raises:
        TemplateParseError: If template_spec block is missing or multiple blocks found
    """
    pattern = r"```template_spec\s*\n(.*?)```"
    matches = re.findall(pattern, body, re.DOTALL)

    if len(matches) == 0:
        raise TemplateParseError("MISSING_TEMPLATE_SPEC", "No template_spec block found")

    if len(matches) > 1:
        raise TemplateParseError(
            "MULTIPLE_TEMPLATE_SPEC",
            f"Found {len(matches)} template_spec blocks, expected exactly one",
        )

    return matches[0].strip()


def _parse_template_spec_yaml(spec_text: str, file_path: Path) -> dict[str, str | int | list | dict]:
    """Parse template_spec YAML content.

    Args:
        spec_text: YAML content from template_spec block
        file_path: Path to source file (for error messages)

    Returns:
        Parsed template spec dictionary

    Raises:
        TemplateParseError: If YAML is invalid
    """
    try:
        spec_dict = yaml.safe_load(spec_text)
        if not isinstance(spec_dict, dict):
            raise TemplateParseError(
                "INVALID_TEMPLATE_SPEC_YAML",
                f"template_spec block must contain YAML dictionary in {file_path}",
            )
    except yaml.YAMLError as e:
        raise TemplateParseError(
            "INVALID_TEMPLATE_SPEC_YAML",
            f"Invalid YAML in template_spec block in {file_path}: {e}",
        ) from e
    else:
        return spec_dict


def _validate_template_fields(
    frontmatter: dict[str, str | int | list[str]],
    spec_dict: dict[str, str | int | list | dict],
    file_path: Path,
) -> None:
    """Validate required fields are present.

    Args:
        frontmatter: Parsed frontmatter dictionary
        spec_dict: Parsed template spec dictionary
        file_path: Path to source file (for error messages)

    Raises:
        TemplateParseError: If required fields are missing
    """
    required_frontmatter = [
        "doc_type",
        "domain",
        "philosophy_id",
        "race_types",
        "audience",
        "phase",
        "session_type",
        "priority",
        "version",
    ]

    for field in required_frontmatter:
        if field not in frontmatter:
            raise TemplateParseError(
                "MISSING_FRONTMATTER_FIELD",
                f"Missing required frontmatter field '{field}' in {file_path}",
            )

    if "templates" not in spec_dict:
        raise TemplateParseError(
            "MISSING_TEMPLATES",
            f"Missing required field 'templates' in template_spec in {file_path}",
        )

    templates = spec_dict["templates"]
    if not isinstance(templates, list) or len(templates) == 0:
        raise TemplateParseError(
            "INVALID_TEMPLATES",
            f"templates must be a non-empty list in {file_path}",
        )


def parse_template_file(file_path: Path) -> SessionTemplateSet:
    """Parse a template markdown file into a SessionTemplateSet.

    Args:
        file_path: Path to the template markdown file

    Returns:
        Parsed SessionTemplateSet

    Raises:
        TemplateParseError: If parsing fails
        FileNotFoundError: If file does not exist
    """
    if not file_path.exists():
        raise FileNotFoundError(f"Template file not found: {file_path}")

    content = file_path.read_text(encoding="utf-8")

    # Parse frontmatter
    frontmatter, body = _parse_frontmatter(content)

    # Extract template_spec block
    spec_text = _extract_template_spec_block(body)

    # Parse template_spec YAML
    spec_dict = _parse_template_spec_yaml(spec_text, file_path)

    # Validate required fields
    _validate_template_fields(frontmatter, spec_dict, file_path)

    # Validate doc_type
    if frontmatter.get("doc_type") != "session_template_set":
        raise TemplateParseError(
            "INVALID_DOC_TYPE",
            f"Expected doc_type 'session_template_set', got '{frontmatter.get('doc_type')}' in {file_path}",
        )

    # Build metadata
    race_types = frontmatter["race_types"]
    if not isinstance(race_types, list):
        race_types = [str(race_types)]
    else:
        race_types = [str(rt) for rt in race_types]

    # Validate and convert int fields
    priority_raw = frontmatter["priority"]
    if not isinstance(priority_raw, int):
        if isinstance(priority_raw, str):
            priority = int(priority_raw)
        else:
            raise TemplateParseError(
                "INVALID_PRIORITY",
                f"priority must be an integer in {file_path}",
            )
    else:
        priority = priority_raw

    # Parse templates
    templates_list = spec_dict["templates"]
    templates: list[SessionTemplate] = []

    for template_dict in templates_list:
        if not isinstance(template_dict, dict):
            raise TemplateParseError(
                "INVALID_TEMPLATE_ITEM",
                f"Each template must be a dictionary in {file_path}",
            )

        template_id = template_dict.get("id")
        if not template_id or not isinstance(template_id, str):
            raise TemplateParseError(
                "MISSING_TEMPLATE_ID",
                f"Each template must have an 'id' field in {file_path}",
            )

        description_key = template_dict.get("description_key", "")
        if not isinstance(description_key, str):
            description_key = str(description_key)

        kind = template_dict.get("kind", "")
        if not isinstance(kind, str):
            kind = str(kind)

        params = template_dict.get("params", {})
        if not isinstance(params, dict):
            params = {}

        constraints = template_dict.get("constraints", {})
        if not isinstance(constraints, dict):
            constraints = {}

        tags = template_dict.get("tags", [])
        if not isinstance(tags, list):
            tags = [str(tags)] if tags else []
        else:
            tags = [str(t) for t in tags]

        template = SessionTemplate(
            template_id=str(template_id),
            description_key=description_key,
            kind=kind,
            params=params,
            constraints=constraints,
            tags=tags,
        )
        templates.append(template)

    return SessionTemplateSet(
        domain=str(frontmatter["domain"]),
        philosophy_id=str(frontmatter["philosophy_id"]),
        phase=str(frontmatter["phase"]),
        session_type=str(frontmatter["session_type"]),
        race_types=race_types,
        audience=str(frontmatter["audience"]),
        priority=priority,
        version=str(frontmatter["version"]),
        templates=templates,
    )


def _get_session_type_for_day(
    day: DistributedDay,
    day_index_to_session_type: dict[int, str] | None,
) -> str | None:
    """Get session_type for a day, using structure mapping if available.

    Args:
        day: DistributedDay with day_index and day_type
        day_index_to_session_type: Optional mapping from day_index to session_type

    Returns:
        Session type string (e.g., "easy", "threshold", "vo2") or None if not found
    """
    # If we have the structure mapping, use it (this preserves threshold vs vo2 distinction)
    if day_index_to_session_type is not None:
        return day_index_to_session_type.get(day.day_index)

    # Fallback: map DayType to session_type (loses threshold vs vo2 distinction)
    for session_type, mapped_day_type in _SESSION_TYPE_TO_DAY_TYPE.items():
        if mapped_day_type == day.day_type:
            return session_type
    return None


def _template_set_matches(
    template_set: SessionTemplateSet,
    criteria: dict[str, str | None],
) -> bool:
    """Check if template set matches all criteria.

    Args:
        template_set: Template set to check
        criteria: Dictionary with keys: domain, philosophy_id, phase, session_type, audience, race_distance_str

    Returns:
        True if all criteria match
    """
    if template_set.domain != criteria["domain"]:
        return False
    if template_set.philosophy_id != criteria["philosophy_id"]:
        return False
    if template_set.phase != criteria["phase"]:
        return False
    if template_set.session_type != criteria["session_type"]:
        return False
    if template_set.audience != criteria["audience"]:
        return False
    race_distance_str = criteria["race_distance_str"]
    return race_distance_str is None or race_distance_str in template_set.race_types


def _find_matching_template_set(
    context: PlanRuntimeContext,
    phase: str,
    session_type: str,
) -> SessionTemplateSet | None:
    """Find matching template set from RAG.

    Args:
        context: Plan runtime context with philosophy and plan info
        phase: Training phase (build/taper)
        session_type: Session type (easy/threshold/vo2/long/easy_plus_strides)

    Returns:
        Matching SessionTemplateSet or None if not found
    """
    templates_dir = _get_templates_dir()
    domain = context.philosophy.domain
    philosophy_id = context.philosophy.philosophy_id

    # Build path: data/rag/planning/templates/<domain>/<philosophy_id>/
    philosophy_dir = templates_dir / domain / philosophy_id

    if not philosophy_dir.exists():
        logger.warning(f"Template directory not found: {philosophy_dir}")
        return None

    # Get race distance as string
    race_distance_str = None
    if context.plan.race_distance:
        race_distance_str = context.plan.race_distance.value

    # Search for matching template file
    # Pattern: <philosophy_id>__<race>__<audience>__<phase>__<session_type>__v*.md
    pattern = f"{philosophy_id}__{race_distance_str}__{context.philosophy.audience}__{phase}__{session_type}__*.md"

    matching_files = list(philosophy_dir.glob(pattern))

    if not matching_files:
        logger.warning(
            f"No template file found for pattern: {pattern} in {philosophy_dir}",
        )
        return None

    # Sort by priority (highest first) and version
    # For now, just take the first match (we can enhance with priority later)
    matching_files.sort(reverse=True)  # Sort by filename (v1, v2, etc.)

    criteria = {
        "domain": domain,
        "philosophy_id": philosophy_id,
        "phase": phase,
        "session_type": session_type,
        "audience": context.philosophy.audience,
        "race_distance_str": race_distance_str,
    }

    for file_path in matching_files:
        try:
            template_set = parse_template_file(file_path)
            # Verify it matches our criteria
            if _template_set_matches(template_set=template_set, criteria=criteria):
                return template_set
        except (TemplateParseError, FileNotFoundError) as e:
            logger.warning(f"Failed to parse template file {file_path}: {e}")
            continue

    return None


def select_template_for_day(
    template_set: SessionTemplateSet,
    week_index: int,
    day_index: int,
) -> SessionTemplate:
    """Select a template from a template set deterministically.

    Uses deterministic rotation based on week and day indices to avoid
    staleness while maintaining reproducibility.

    Args:
        template_set: Template set to select from
        week_index: Week number (1-based)
        day_index: Day index (0 = Monday, 6 = Sunday)

    Returns:
        Selected SessionTemplate

    Raises:
        TemplateSelectionError: If template set has no templates
    """
    if not template_set.templates:
        raise TemplateSelectionError("Template set has no templates")

    idx = (week_index * 100 + day_index) % len(template_set.templates)
    return template_set.templates[idx]


def select_templates_for_week(
    context: PlanRuntimeContext,
    week_index: int,
    phase: str,
    days: list[DistributedDay],
    day_index_to_session_type: dict[int, str] | None = None,
) -> list[PlannedSession]:
    """Select templates for all days in a week.

    Args:
        context: Plan runtime context
        week_index: Week number (1-based)
        phase: Training phase (build/taper)
        days: List of distributed days for the week
        day_index_to_session_type: Optional mapping from day_index to session_type
            (from WeekStructure.day_index_to_session_type)

    Returns:
        List of planned sessions with selected templates

    Raises:
        TemplateSelectionError: If template selection fails for any day
    """
    planned_sessions: list[PlannedSession] = []

    for day in days:
        # Get session_type for this day (uses structure mapping if available)
        session_type = _get_session_type_for_day(day, day_index_to_session_type)

        if session_type is None:
            raise TemplateSelectionError(
                f"No session_type mapping for day_type '{day.day_type.value}' at day_index {day.day_index}",
            )

        # Find matching template set
        template_set = _find_matching_template_set(context, phase, session_type)

        if template_set is None:
            raise TemplateSelectionError(
                f"No template set found for domain={context.philosophy.domain}, "
                f"philosophy={context.philosophy.philosophy_id}, phase={phase}, "
                f"session_type={session_type}, audience={context.philosophy.audience}",
            )

        # Select template deterministically
        template = select_template_for_day(template_set, week_index, day.day_index)

        # Create planned session
        planned_session = PlannedSession(
            day_index=day.day_index,
            day_type=day.day_type,
            distance=day.distance,
            template=template,
        )

        planned_sessions.append(planned_session)

    return planned_sessions
