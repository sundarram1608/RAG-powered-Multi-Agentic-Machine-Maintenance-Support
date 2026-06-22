"""
generate_metadata.py
---------------------
Builds the `maintenance` database's data dictionary by INTROSPECTING the live
schema (information_schema) and ENRICHING it with hand-authored prose from
table_descriptions.py. Writes two artifacts:

    schema_metadata.json   -> for agents (machine-readable)
    schema_metadata.md     -> for humans (readable data dictionary)

Why introspect: column names/types/nullability/keys + sample values come from
the real DB every run, so the structure can never drift. Only the prose in
table_descriptions.py is hand-maintained — and missing/orphaned descriptions are
flagged as warnings.

Run standalone:
    python synthetic_data/tables/metadata/generate_metadata.py
(generate_data.py also calls generate_metadata(conn) as its final phase.)
"""

import json
import sys
from pathlib import Path

# Make both this folder and the parent tables/ folder importable, whether this
# module is run standalone or imported by generate_data.py.
THIS_DIR = Path(__file__).resolve().parent          # .../tables/metadata
TABLES_DIR = THIS_DIR.parent                         # .../tables
for _p in (str(THIS_DIR), str(TABLES_DIR)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from db_connection import get_connection
from table_descriptions import DATABASE_OVERVIEW, TABLE_DESCRIPTIONS

OUTPUT_JSON = THIS_DIR / "schema_metadata.json"
OUTPUT_MD = THIS_DIR / "schema_metadata.md"

_KEY_LABEL = {"PRI": "PK", "MUL": "FK", "UNI": "UNIQUE"}


def _sample_value(conn, table: str, column: str):
    """Return one non-null sample value for a column, as a string (or None)."""
    cur = conn.cursor(buffered=True)
    try:
        cur.execute(
            f"SELECT `{column}` FROM `{table}` WHERE `{column}` IS NOT NULL LIMIT 1;"
        )
        row = cur.fetchone()
        return None if row is None else str(row[0])
    except Exception:
        return None
    finally:
        cur.close()


def _introspect(conn):
    """Read tables, columns, keys and FKs straight from information_schema."""
    cur = conn.cursor(buffered=True)
    cur.execute("SELECT DATABASE();")
    db_name = cur.fetchone()[0]

    cur.execute(
        "SELECT table_name FROM information_schema.tables "
        "WHERE table_schema = %s AND table_type = 'BASE TABLE' "
        "ORDER BY table_name;",
        (db_name,),
    )
    tables = [r[0] for r in cur.fetchall()]

    schema = {}
    relationships = []
    for table in tables:
        cur.execute(
            "SELECT column_name, column_type, is_nullable, column_key "
            "FROM information_schema.columns "
            "WHERE table_schema = %s AND table_name = %s "
            "ORDER BY ordinal_position;",
            (db_name, table),
        )
        col_rows = cur.fetchall()

        cur.execute(
            "SELECT column_name, referenced_table_name, referenced_column_name "
            "FROM information_schema.key_column_usage "
            "WHERE table_schema = %s AND table_name = %s "
            "AND referenced_table_name IS NOT NULL;",
            (db_name, table),
        )
        fk_map = {c[0]: (c[1], c[2]) for c in cur.fetchall()}

        primary_key = [name for name, _t, _n, key in col_rows if key == "PRI"]
        columns = []
        for name, ctype, is_nullable, key in col_rows:
            columns.append({
                "name": name,
                "data_type": ctype,
                "nullable": is_nullable == "YES",
                "key": _KEY_LABEL.get(key, ""),
                "example": _sample_value(conn, table, name),
            })

        foreign_keys = [
            {"column": col, "references": f"{ref[0]}.{ref[1]}"}
            for col, ref in fk_map.items()
        ]
        relationships += [
            f"{table}.{col} -> {ref[0]}.{ref[1]}" for col, ref in fk_map.items()
        ]

        schema[table] = {
            "primary_key": primary_key,
            "foreign_keys": foreign_keys,
            "columns": columns,
        }

    cur.close()
    return db_name, tables, schema, relationships


def _merge(db_name, tables, schema, relationships):
    """Merge introspected structure with hand-authored prose; collect warnings."""
    warnings = []
    tables_meta = {}

    for table in tables:
        desc = TABLE_DESCRIPTIONS.get(table)
        if not desc:
            warnings.append(f"no description entry for table '{table}'")
            desc = {}
        col_desc = desc.get("columns", {})

        db_cols = {c["name"] for c in schema[table]["columns"]}
        for cname in col_desc:
            if cname not in db_cols:
                warnings.append(
                    f"description for '{table}.{cname}' but that column is not in the DB"
                )

        columns = []
        for col in schema[table]["columns"]:
            description = col_desc.get(col["name"])
            if description is None:
                warnings.append(f"no description for column '{table}.{col['name']}'")
            columns.append({
                "name": col["name"],
                "data_type": col["data_type"],
                "nullable": col["nullable"],
                "key": col["key"],
                "description": description or "",
                "example": col["example"],
            })

        tables_meta[table] = {
            "description": desc.get("description", ""),
            "when_to_use": desc.get("when_to_use", ""),
            "notes": desc.get("notes", ""),
            "primary_key": schema[table]["primary_key"],
            "foreign_keys": schema[table]["foreign_keys"],
            "columns": columns,
        }

    for t in TABLE_DESCRIPTIONS:
        if t not in tables:
            warnings.append(f"description for table '{t}' but that table is not in the DB")

    metadata = {
        "database": db_name,
        "overview": DATABASE_OVERVIEW,
        "relationships": relationships,
        "tables": tables_meta,
    }
    return metadata, warnings


def _to_markdown(meta) -> str:
    """Render the metadata dict as a human-readable Markdown data dictionary."""
    out = [f"# Data dictionary — `{meta['database']}` database", "", meta["overview"], ""]

    if meta["relationships"]:
        out += ["## Relationships", ""]
        out += [f"- `{r}`" for r in meta["relationships"]]
        out.append("")

    out += ["## Tables", ""]
    for table, t in meta["tables"].items():
        out += [f"### `{table}`", ""]
        if t["description"]:
            out += [t["description"], ""]
        if t["when_to_use"]:
            out += [f"**When to use:** {t['when_to_use']}", ""]
        if t.get("notes"):
            out += [f"**Notes:** {t['notes']}", ""]
        if t["primary_key"]:
            out.append(f"**Primary key:** {', '.join(t['primary_key'])}")
        if t["foreign_keys"]:
            fks = "; ".join(f"{fk['column']} → {fk['references']}" for fk in t["foreign_keys"])
            out.append(f"**Foreign keys:** {fks}")
        out += [
            "",
            "| Column | Type | Nullable | Key | Description | Example |",
            "|---|---|---|---|---|---|",
        ]
        for c in t["columns"]:
            example = "" if c["example"] is None else c["example"].replace("|", "\\|")
            description = (c["description"] or "").replace("|", "\\|")
            out.append(
                f"| `{c['name']}` | {c['data_type']} | "
                f"{'Yes' if c['nullable'] else 'No'} | {c['key']} | "
                f"{description} | {example} |"
            )
        out.append("")
    return "\n".join(out)


def generate_metadata(conn=None) -> None:
    """Introspect + enrich, then write schema_metadata.json and schema_metadata.md."""
    own_conn = conn is None
    if own_conn:
        conn = get_connection()
    try:
        db_name, tables, schema, relationships = _introspect(conn)
        metadata, warnings = _merge(db_name, tables, schema, relationships)

        OUTPUT_JSON.write_text(
            json.dumps(metadata, indent=2, ensure_ascii=False), encoding="utf-8"
        )
        OUTPUT_MD.write_text(_to_markdown(metadata), encoding="utf-8")

        print(
            f"  ✓ metadata: wrote {OUTPUT_JSON.name} and {OUTPUT_MD.name} "
            f"({len(tables)} tables)"
        )
        for w in warnings:
            print(f"    ⚠️  {w}")
    finally:
        if own_conn and conn.is_connected():
            conn.close()


if __name__ == "__main__":
    generate_metadata()
