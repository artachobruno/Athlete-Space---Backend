"""Auto-generate DATABASE_SCHEMA.md from SQLAlchemy metadata.

Run:
    python scripts/generate_database_schema.py

DO NOT EDIT THE OUTPUT FILE MANUALLY.
"""

from __future__ import annotations

import pathlib
from datetime import UTC, datetime, timezone

from sqlalchemy import MetaData

import app.workouts.execution_models
import app.workouts.models
from app.db.models import Base

OUTPUT_PATH = "docs/DATABASE_SCHEMA.md"


def render_markdown(metadata: MetaData) -> str:
    lines: list[str] = []

    lines.append("# AthleteSpace — Database Schema\n")
    lines.append("> Auto-generated. Do not edit manually.\n")
    lines.append(
        f"> Last updated: {datetime.now(UTC).strftime('%Y-%m-%d %H:%M:%S UTC')}\n"
    )
    lines.append("---\n")

    for table in sorted(metadata.tables.values(), key=lambda t: t.name):
        lines.append(f"## {table.name}\n")

        if table.comment:
            lines.append(f"{table.comment}\n")

        lines.append("| Column | Type | Nullable | Primary | Notes |")
        lines.append("|--------|------|----------|---------|-------|")

        for col in table.columns:
            col_type = str(col.type)
            nullable = "yes" if col.nullable else "no"
            primary = "yes" if col.primary_key else "no"

            notes = []
            if col.foreign_keys:
                for fk in col.foreign_keys:
                    try:
                        table_name = fk.column.table.name
                        column_name = fk.column.name
                        notes.append(f"FK → {table_name}.{column_name}")
                    except Exception:
                        notes.append(f"FK → {fk.column.name}")
            if col.unique:
                notes.append("unique")

            note_str = ", ".join(notes)

            lines.append(
                f"| {col.name} | {col_type} | {nullable} | {primary} | {note_str} |"
            )

        if table.constraints:
            lines.append("\n**Constraints**")
            lines.extend(f"- {c.name}" for c in table.constraints if c.name)

        if table.indexes:
            lines.append("\n**Indexes**")
            for idx in table.indexes:
                cols = ", ".join(c.name for c in idx.columns)
                lines.append(f"- {idx.name} ({cols})")

        lines.append("\n---\n")

    return "\n".join(lines)


def main() -> None:
    metadata = Base.metadata
    content = render_markdown(metadata)

    with pathlib.Path(OUTPUT_PATH).open("w", encoding="utf-8") as f:
        f.write(content)

    print(f"✅ Database schema written to {OUTPUT_PATH}")


if __name__ == "__main__":
    main()
