#!/usr/bin/env python3
"""Fix indentation in generated template files."""

from pathlib import Path

import yaml

templates_dir = Path("data/rag/planning/templates/running")
fixed = 0
errors = 0

for template_file in templates_dir.rglob("*__v1.md"):
    try:
        content = template_file.read_text()

        # Extract template_spec block
        if "```template_spec" not in content:
            continue

        spec_start = content.find("```template_spec") + len("```template_spec")
        spec_end = content.find("```", spec_start)
        if spec_end == -1:
            continue

        spec_content = content[spec_start:spec_end].strip()

        # Parse YAML
        try:
            parsed = yaml.safe_load(spec_content)
            if not parsed or "templates" not in parsed:
                continue
        except Exception as e:
            # If it doesn't parse, skip this file
            print(f"Warning: Failed to parse YAML in {template_file}: {e}")
            continue

        # Regenerate with proper formatting
        frontmatter = content[:content.find("```template_spec")]
        end_marker = content[spec_end:]

        # Format templates properly
        formatted_yaml = yaml.dump(parsed, default_flow_style=False, sort_keys=False, indent=2, allow_unicode=True)

        new_content = frontmatter + "```template_spec\n" + formatted_yaml + end_marker

        if new_content != content:
            template_file.write_text(new_content)
            fixed += 1
    except Exception as e:
        print(f"Error fixing {template_file}: {e}")
        errors += 1

print(f"Fixed {fixed} files")
if errors > 0:
    print(f"Errors: {errors}")
