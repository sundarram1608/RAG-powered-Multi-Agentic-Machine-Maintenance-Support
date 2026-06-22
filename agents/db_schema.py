"""
db_schema.py — compact schema context for the Text-to-SQL agents.

Builds a token-efficient, grounded description of the `maintenance` database from
the generated schema_metadata.json (the single source of truth), shared by the
Analytics coder and the Text-to-SQL Reviewer so both reason over the SAME schema.
"""

import json
import sys
from functools import lru_cache
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))  # agents/ on path
import config

_SCHEMA_PATH = (
    config.PROJECT_ROOT / "synthetic_data" / "tables" / "metadata" / "schema_metadata.json"
)


@lru_cache(maxsize=1)
def get_schema_context() -> str:
    """Return a compact 'TABLE ... (PK/FK) / columns' description + relationships."""
    data = json.loads(_SCHEMA_PATH.read_text())
    lines = []
    for table, meta in data["tables"].items():
        pk = ", ".join(meta.get("primary_key") or [])
        fks = [f'{fk["column"]}->{fk["references"]}'
               for fk in (meta.get("foreign_keys") or []) if fk]
        header = f"TABLE {table}  (PK: {pk}"
        if fks:
            header += "; FK: " + ", ".join(fks)
        header += ")"
        cols = ", ".join(f'{c["name"]} {c["data_type"]}' for c in meta["columns"])
        lines.append(header)
        lines.append(f"  {cols}")
    lines.append("")
    lines.append("RELATIONSHIPS:")
    for rel in data.get("relationships", []):
        lines.append(f"  {rel}")
    return "\n".join(lines)


if __name__ == "__main__":
    print(get_schema_context())
