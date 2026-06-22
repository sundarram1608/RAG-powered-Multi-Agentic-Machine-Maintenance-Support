"""
safety.py — guardrails for the one tool that runs LLM-generated SQL
(run_readonly_query). Two independent layers of defense:

  1. get_readonly_connection() — a SELECT-only MySQL account (created by
     setup_db_users.py). Writes/DDL are rejected by the database itself,
     even if validation has a gap. This is the hard guarantee.
  2. validate_select_sql() — static checks that reject anything other than a
     single, comment-free, read-only SELECT/WITH, and block PII columns. Fast,
     friendly rejection before the query ever reaches the database.

Neither layer is trusted alone; together they're robust.
"""

import os
import re
from pathlib import Path

import mysql.connector
from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parents[1]
ENV_PATH = PROJECT_ROOT / ".env"

# Write / DDL / dangerous keywords — any whole-word match (case-insensitive)
# rejects the query. INTO blocks `SELECT ... INTO OUTFILE/@var`; OUTFILE/DUMPFILE/
# LOAD_FILE block file I/O (also denied by the read-only user).
_FORBIDDEN_KEYWORDS = [
    "INSERT", "UPDATE", "DELETE", "DROP", "ALTER", "CREATE", "TRUNCATE",
    "REPLACE", "RENAME", "GRANT", "REVOKE", "MERGE", "CALL", "EXEC", "EXECUTE",
    "SET", "LOCK", "UNLOCK", "HANDLER", "LOAD", "LOAD_FILE", "OUTFILE",
    "DUMPFILE", "INTO",
]
_FORBIDDEN_RE = re.compile(r"\b(" + "|".join(_FORBIDDEN_KEYWORDS) + r")\b", re.IGNORECASE)

# PII columns the generated SQL may never reference (in-office policy: email and
# full_name are allowed; phone is not).
_PII_COLUMNS = ["phone"]
_PII_RE = re.compile(r"\b(" + "|".join(_PII_COLUMNS) + r")\b", re.IGNORECASE)

_COMMENT_RE = re.compile(r"--|#|/\*|\*/")

DEFAULT_MAX_ROWS = 200


def get_readonly_connection():
    """Open a MySQL connection using the SELECT-only account from .env."""
    load_dotenv(dotenv_path=ENV_PATH, override=True)
    return mysql.connector.connect(
        host=os.getenv("DB_HOST", "localhost"),
        port=int(os.getenv("DB_PORT", "3306")),
        database=os.getenv("DB_NAME", "maintenance"),
        user=os.getenv("DB_READONLY_USER", "maint_readonly"),
        password=os.getenv("DB_READONLY_PASSWORD", ""),
    )


def validate_select_sql(sql: str, max_rows: int = DEFAULT_MAX_ROWS) -> str:
    """
    Validate that `sql` is a single read-only SELECT/WITH and return cleaned,
    row-capped SQL. Raise ValueError(reason) on any violation.
    """
    if not (sql or "").strip():
        raise ValueError("Empty SQL.")
    cleaned = sql.strip()

    if _COMMENT_RE.search(cleaned):
        raise ValueError("SQL comments are not allowed.")

    # Single statement only (allow one optional trailing ';').
    cleaned = cleaned.rstrip(";").strip()
    if ";" in cleaned:
        raise ValueError("Only a single statement is allowed.")

    if not re.match(r"(?is)^(SELECT|WITH)\b", cleaned):
        raise ValueError("Only SELECT / WITH (read-only) queries are allowed.")

    forbidden = _FORBIDDEN_RE.search(cleaned)
    if forbidden:
        raise ValueError(f"Forbidden keyword: {forbidden.group(1).upper()}.")

    pii = _PII_RE.search(cleaned)
    if pii:
        raise ValueError(f"Querying PII column '{pii.group(1)}' is not allowed.")

    # Row cap: append a LIMIT if the query has none.
    if not re.search(r"(?is)\blimit\b", cleaned):
        cleaned = f"{cleaned} LIMIT {max_rows}"

    return cleaned


# === SELF-TEST — python mcp_server/safety.py ===
if __name__ == "__main__":
    ok_cases = [
        "SELECT machine_id FROM machines",
        "  select * from incidents where machine_id='M01'  ;",
        "WITH x AS (SELECT 1 AS n) SELECT n FROM x",
    ]
    bad_cases = [
        "",
        "DELETE FROM machines",
        "SELECT phone FROM employees",
        "SELECT 1; DROP TABLE machines",
        "SELECT * FROM employees -- comment",
        "SELECT * INTO OUTFILE '/tmp/x' FROM employees",
    ]
    print("OK cases (cleaned):")
    for s in ok_cases:
        print(f"  {s!r:60} -> {validate_select_sql(s)!r}")
    print("\nBad cases (rejected):")
    for s in bad_cases:
        try:
            validate_select_sql(s)
            print(f"  {s!r:60} -> ❌ NOT rejected")
        except ValueError as exc:
            print(f"  {s!r:60} -> ✅ {exc}")
