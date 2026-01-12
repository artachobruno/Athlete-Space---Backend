"""Test that legacy planner paths are completely removed."""

import inspect
import os
import sys
from pathlib import Path

import pytest


def test_legacy_planner_removed():
    """Test that plan_race_build does not exist anywhere in the codebase."""
    project_root = Path(__file__).parent.parent.parent
    code_files = []

    # Find all Python files
    for root, _dirs, files in os.walk(project_root):
        # Skip test directories and virtual environments
        if any(skip in root for skip in [".git", "__pycache__", ".venv", "venv", "node_modules"]):
            continue
        code_files.extend(Path(root) / file for file in files if file.endswith(".py"))

    # Check for legacy planner references
    legacy_patterns = [
        "plan_race_build",
        "plan_week_llm",
        "plan_race_build_new",
        "volume_repair",
        "compile_plan",
    ]

    violations = []
    for file_path in code_files:
        try:
            content = file_path.read_text(encoding="utf-8")
            # Skip test files that are testing the removal itself
            if "test_legacy_planner_removed" in str(file_path):
                continue
            # Skip files that are explicitly stubbing legacy code
            if "DEPRECATED" in content or "Legacy planner" in content or "Removed in v2" in content:
                continue
            for pattern in legacy_patterns:
                # Only flag if it's not in a comment or string explaining removal
                if pattern in content:
                    # Check if it's in a comment/docstring about removal
                    lines = content.split("\n")
                    for i, line in enumerate(lines):
                        if pattern in line:
                            # Check surrounding context
                            context = "\n".join(lines[max(0, i - 2):min(len(lines), i + 3)])
                            if "DEPRECATED" not in context and "removed" not in context.lower() and "legacy" not in context.lower():
                                violations.append(f"{file_path}:{i + 1}: {pattern}")
        except Exception:
            # Skip files that can't be read
            continue

    if violations:
        pytest.fail(
            f"Found {len(violations)} legacy planner references:\n" + "\n".join(violations[:20])
        )
