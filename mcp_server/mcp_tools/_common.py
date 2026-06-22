"""
_common.py
----------
Shared helper for the MCP tools.

It (a) puts the project's existing modules on the import path — the DB connection
(synthetic_data/tables/db_connection.py) and the RAG retriever (rag/) — so tools
can reuse them, and (b) provides a small parameterized read-query helper so each
tool file shows just its own SQL, not connection plumbing.

REFERENCE_TODAY is the dataset's "current date": the seeded data is anchored to
June 2026, so overdue/availability logic uses this instead of the real
date.today() (which is intentionally not used, to match the synthetic data).
"""

import os
import sys
from datetime import date
from pathlib import Path

import mysql.connector
from dotenv import load_dotenv

# This file: .../mcp_server/mcp_tools/_common.py  ->  parents[2] = project root
PROJECT_ROOT = Path(__file__).resolve().parents[2]
ENV_PATH = PROJECT_ROOT / ".env"
for _path in (PROJECT_ROOT / "synthetic_data" / "tables", PROJECT_ROOT / "rag"):
    _path_str = str(_path)
    if _path_str not in sys.path:
        sys.path.insert(0, _path_str)

from db_connection import get_connection  # noqa: E402  (import after sys.path setup)

# Dataset reference "today" — seeded data is anchored here (the real date.today()
# is deliberately not used so the demo lines up with the synthetic data).
REFERENCE_TODAY = date(2026, 6, 16)


def run_query(sql: str, params: tuple = ()) -> list:
    """Run a parameterized read query; return rows as a list of dicts."""
    conn = get_connection()
    try:
        cursor = conn.cursor(dictionary=True)
        cursor.execute(sql, params)
        rows = cursor.fetchall()
        cursor.close()
        return rows
    finally:
        conn.close()


def get_write_connection():
    """
    Open a MySQL connection using the least-privilege WRITE account
    (DB_WRITE_USER from .env) — it can only INSERT/UPDATE `incidents` and
    `technician_schedule`, nothing else. Created by setup_db_users.py. The write
    tools use this so a bug can never reach master data or DELETE/DROP anything.
    """
    load_dotenv(dotenv_path=ENV_PATH, override=True)
    return mysql.connector.connect(
        host=os.getenv("DB_HOST", "localhost"),
        port=int(os.getenv("DB_PORT", "3306")),
        database=os.getenv("DB_NAME", "maintenance"),
        user=os.getenv("DB_WRITE_USER", "maint_write"),
        password=os.getenv("DB_WRITE_PASSWORD", ""),
    )


def run_write(statements) -> int:
    """
    Execute one or more writes in a single committed transaction on the write
    connection. `statements` is a single (sql, params) tuple or a list of them;
    if any statement fails the whole transaction is rolled back. Returns the last
    statement's rowcount.
    """
    if isinstance(statements, tuple):
        statements = [statements]
    conn = get_write_connection()
    try:
        cursor = conn.cursor()
        rowcount = 0
        for sql, params in statements:
            cursor.execute(sql, params)
            rowcount = cursor.rowcount
        conn.commit()
        cursor.close()
        return rowcount
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
