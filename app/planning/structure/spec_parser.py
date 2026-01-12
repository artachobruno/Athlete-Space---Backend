"""Structure specification parser.

This module parses structure markdown files, extracting:
1. YAML frontmatter (metadata)
2. structure_spec code block (specification)

Fails fast on:
- Missing frontmatter
- Missing structure_spec block
- Multiple structure_spec blocks
- Invalid YAML
"""

import re
from pathlib import Path

import yaml

from app.planning.structure.types import StructureMetadata, StructureSpec


class StructureParseError(RuntimeError):
    """Raised when structure parsing fails.

    Attributes:
        code: Error code
        message: Error message
    """

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        self.message = message
        super().__init__(f"{code}: {message}")


def _parse_frontmatter(content: str) -> tuple[dict[str, str | int | list[str]], str]:
    """Parse YAML frontmatter from markdown content.

    Args:
        content: Full markdown file content

    Returns:
        Tuple of (frontmatter dict, body content)

    Raises:
        StructureParseError: If frontmatter is missing or invalid
    """
    frontmatter_pattern = r"^---\s*\n(.*?)\n---\s*\n(.*)$"
    match = re.match(frontmatter_pattern, content, re.DOTALL)

    if not match:
        raise StructureParseError("MISSING_FRONTMATTER", "Missing or malformed YAML frontmatter")

    frontmatter_text = match.group(1)
    body = match.group(2).strip()

    try:
        frontmatter = yaml.safe_load(frontmatter_text)
        if not isinstance(frontmatter, dict):
            raise StructureParseError("INVALID_FRONTMATTER", "Frontmatter must be a YAML dictionary")
    except yaml.YAMLError as e:
        raise StructureParseError("INVALID_FRONTMATTER_YAML", f"Invalid YAML frontmatter: {e}") from e

    return frontmatter, body


def _extract_structure_spec_block(body: str) -> str:
    """Extract structure_spec code block from markdown body.

    Args:
        body: Markdown body content (after frontmatter)

    Returns:
        Content of the structure_spec block

    Raises:
        StructureParseError: If structure_spec block is missing or multiple blocks found
    """
    # Pattern to match ```structure_spec ... ``` blocks
    pattern = r"```structure_spec\s*\n(.*?)```"
    matches = re.findall(pattern, body, re.DOTALL)

    if len(matches) == 0:
        raise StructureParseError("MISSING_STRUCTURE_SPEC", "No structure_spec block found")

    if len(matches) > 1:
        raise StructureParseError(
            "MULTIPLE_STRUCTURE_SPEC",
            f"Found {len(matches)} structure_spec blocks, expected exactly one",
        )

    return matches[0].strip()


def _parse_structure_spec_yaml(spec_text: str, file_path: Path) -> dict[str, str | int | list | dict]:
    """Parse structure_spec YAML content.

    Args:
        spec_text: YAML content from structure_spec block
        file_path: Path to source file (for error messages)

    Returns:
        Parsed structure spec dictionary

    Raises:
        StructureParseError: If YAML is invalid
    """
    try:
        spec_dict = yaml.safe_load(spec_text)
        if not isinstance(spec_dict, dict):
            raise StructureParseError(
                "INVALID_STRUCTURE_SPEC_YAML",
                f"structure_spec block must contain YAML dictionary in {file_path}",
            )
    except yaml.YAMLError as e:
        raise StructureParseError(
            "INVALID_STRUCTURE_SPEC_YAML",
            f"Invalid YAML in structure_spec block in {file_path}: {e}",
        ) from e
    else:
        return spec_dict


def _validate_required_fields(
    frontmatter: dict[str, str | int | list[str]],
    spec_dict: dict[str, str | int | list | dict],
    file_path: Path,
) -> None:
    """Validate required fields are present.

    Args:
        frontmatter: Parsed frontmatter dictionary
        spec_dict: Parsed structure spec dictionary
        file_path: Path to source file (for error messages)

    Raises:
        StructureParseError: If required fields are missing
    """
    # Required frontmatter fields
    required_frontmatter = [
        "doc_type",
        "philosophy_id",
        "id",
        "race_types",
        "audience",
        "phase",
        "days_to_race_min",
        "days_to_race_max",
        "priority",
    ]

    for field in required_frontmatter:
        if field not in frontmatter:
            raise StructureParseError(
                "MISSING_FRONTMATTER_FIELD",
                f"Missing required frontmatter field '{field}' in {file_path}",
            )

    # Required spec fields
    if "week_pattern" not in spec_dict:
        raise StructureParseError(
            "MISSING_WEEK_PATTERN",
            f"Missing required field 'week_pattern' in structure_spec in {file_path}",
        )

    if "rules" not in spec_dict:
        raise StructureParseError(
            "MISSING_RULES",
            f"Missing required field 'rules' in structure_spec in {file_path}",
        )

    if "session_groups" not in spec_dict:
        raise StructureParseError(
            "MISSING_SESSION_GROUPS",
            f"Missing required field 'session_groups' in structure_spec in {file_path}",
        )


def parse_structure_file(file_path: Path) -> StructureSpec:
    """Parse a structure markdown file into a StructureSpec.

    Args:
        file_path: Path to the structure markdown file

    Returns:
        Parsed StructureSpec

    Raises:
        StructureParseError: If parsing fails
        FileNotFoundError: If file does not exist
    """
    if not file_path.exists():
        raise FileNotFoundError(f"Structure file not found: {file_path}")

    content = file_path.read_text(encoding="utf-8")

    # Parse frontmatter
    frontmatter, body = _parse_frontmatter(content)

    # Extract structure_spec block
    spec_text = _extract_structure_spec_block(body)

    # Parse structure_spec YAML
    spec_dict = _parse_structure_spec_yaml(spec_text, file_path)

    # Validate required fields
    _validate_required_fields(frontmatter, spec_dict, file_path)

    # Validate doc_type
    if frontmatter.get("doc_type") != "plan_structure":
        raise StructureParseError(
            "INVALID_DOC_TYPE",
            f"Expected doc_type 'plan_structure', got '{frontmatter.get('doc_type')}' in {file_path}",
        )

    # Build metadata
    race_types = frontmatter["race_types"]
    if not isinstance(race_types, list):
        race_types = [str(race_types)]
    else:
        race_types = [str(rt) for rt in race_types]

    # Validate and convert int fields
    days_to_race_min_raw = frontmatter["days_to_race_min"]
    if not isinstance(days_to_race_min_raw, int):
        if isinstance(days_to_race_min_raw, str):
            days_to_race_min = int(days_to_race_min_raw)
        else:
            raise StructureParseError(
                "INVALID_DAYS_TO_RACE_MIN",
                f"days_to_race_min must be an integer in {file_path}",
            )
    else:
        days_to_race_min = days_to_race_min_raw

    days_to_race_max_raw = frontmatter["days_to_race_max"]
    if not isinstance(days_to_race_max_raw, int):
        if isinstance(days_to_race_max_raw, str):
            days_to_race_max = int(days_to_race_max_raw)
        else:
            raise StructureParseError(
                "INVALID_DAYS_TO_RACE_MAX",
                f"days_to_race_max must be an integer in {file_path}",
            )
    else:
        days_to_race_max = days_to_race_max_raw

    priority_raw = frontmatter["priority"]
    if not isinstance(priority_raw, int):
        if isinstance(priority_raw, str):
            priority = int(priority_raw)
        else:
            raise StructureParseError(
                "INVALID_PRIORITY",
                f"priority must be an integer in {file_path}",
            )
    else:
        priority = priority_raw

    metadata = StructureMetadata(
        id=str(frontmatter["id"]),
        philosophy_id=str(frontmatter["philosophy_id"]),
        race_types=race_types,
        audience=str(frontmatter["audience"]),
        phase=str(frontmatter["phase"]),
        days_to_race_min=days_to_race_min,
        days_to_race_max=days_to_race_max,
        priority=priority,
    )

    # Extract spec fields with defaults
    week_pattern = spec_dict.get("week_pattern", {})
    if not isinstance(week_pattern, dict):
        raise StructureParseError(
            "INVALID_WEEK_PATTERN",
            f"week_pattern must be a dictionary in {file_path}",
        )

    rules = spec_dict.get("rules", {})
    if not isinstance(rules, dict):
        raise StructureParseError("INVALID_RULES", f"rules must be a dictionary in {file_path}")

    session_groups = spec_dict.get("session_groups", {})
    if not isinstance(session_groups, dict):
        raise StructureParseError(
            "INVALID_SESSION_GROUPS",
            f"session_groups must be a dictionary in {file_path}",
        )

    guards = spec_dict.get("guards", {})
    if not isinstance(guards, dict):
        guards = {}

    notes = spec_dict.get("notes", {})
    if not isinstance(notes, dict):
        notes = {}

    # Convert session_groups lists
    session_groups_typed: dict[str, list[str]] = {}
    for group_name, group_sessions in session_groups.items():
        if isinstance(group_sessions, list):
            session_groups_typed[str(group_name)] = [str(s) for s in group_sessions]
        else:
            session_groups_typed[str(group_name)] = [str(group_sessions)]

    # Ensure week_pattern keys are strings
    week_pattern_typed: dict[str, str] = {str(k): str(v) for k, v in week_pattern.items()}

    return StructureSpec(
        metadata=metadata,
        week_pattern=week_pattern_typed,
        rules=rules,
        session_groups=session_groups_typed,
        guards=guards,
        notes=notes,
    )
