"""
db_connection.py
----------------
Shared MySQL connection helper for the synthetic-data scripts.

`get_connection()` reads credentials from the project-root `.env` file and
returns a live `mysql-connector` connection to the `maintenance` database.

Run this file directly to verify connectivity:

    python synthetic_data/tables/db_connection.py
"""

import os
from pathlib import Path

import mysql.connector
from mysql.connector import Error
from dotenv import load_dotenv

# Locate the project root (.../agenticragmcp) regardless of where this is run
# from: this file is at <root>/synthetic_data/tables/db_connection.py
PROJECT_ROOT = Path(__file__).resolve().parents[2]
ENV_PATH = PROJECT_ROOT / ".env"


def get_connection():
    """Open and return a MySQL connection to the `maintenance` database."""
    # Load .env from the project root (override so the file wins over any
    # stale shell vars).
    load_dotenv(dotenv_path=ENV_PATH, override=True)

    return mysql.connector.connect(
                                        host=os.getenv("DB_HOST", "localhost"),
                                        port=int(os.getenv("DB_PORT", "3306")),
                                        database=os.getenv("DB_NAME", "maintenance"),
                                        user=os.getenv("DB_USER", "root"),
                                        password=os.getenv("DB_PASSWORD", ""),
                                    )


def _check_connection() -> None:
    """Connect, print server version + current database, then close."""
    if not ENV_PATH.exists():
        print(f"⚠️  No .env found at {ENV_PATH}. Copy .env.example to .env first.")
        return

    conn = None
    try:
        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT VERSION();")
        version = cursor.fetchone()[0]
        cursor.execute("SELECT DATABASE();")
        database = cursor.fetchone()[0]
        cursor.close()
        print(f"✅ Connected — MySQL {version}, database: {database}")
    except Error as exc:
        print(f"❌ Connection failed: {exc}")
    finally:
        if conn is not None and conn.is_connected():
            conn.close()


if __name__ == "__main__":
    _check_connection()
