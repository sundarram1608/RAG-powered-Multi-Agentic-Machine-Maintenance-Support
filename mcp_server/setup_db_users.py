"""
setup_db_users.py — one-time creation of the two least-privilege MySQL accounts
the MCP tools use, so neither the read path nor the write path runs as root:

  * maint_readonly — SELECT only (used by run_readonly_query for generated SQL).
  * maint_write    — SELECT + INSERT/UPDATE on ONLY `incidents` and
                     `technician_schedule` (used by the write tools). No DELETE,
                     no DDL, no access to master data (machines, employees, …).

It connects as the admin user (DB_USER/DB_PASSWORD from .env), creates/refreshes
both accounts, and writes their credentials back into .env. Each password is
reused if already present in .env, otherwise a strong random one is generated.
Idempotent — safe to re-run.

    python mcp_server/setup_db_users.py
"""

import secrets
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "synthetic_data" / "tables"))
from db_connection import get_connection  # admin connection (loads .env itself)

ENV_PATH = PROJECT_ROOT / ".env"
READONLY_USER = "maint_readonly"
WRITE_USER = "maint_write"


def _read_env() -> dict:
    """Parse .env into a dict (ignores blanks/comments)."""
    env = {}
    if ENV_PATH.exists():
        for line in ENV_PATH.read_text().splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            env[key.strip()] = value.strip()
    return env


def _write_env(updates: dict) -> None:
    """Update existing keys in place / append new ones, preserving the rest."""
    lines = ENV_PATH.read_text().splitlines() if ENV_PATH.exists() else []
    seen = set()
    out = []
    for line in lines:
        stripped = line.strip()
        if stripped and not stripped.startswith("#") and "=" in stripped:
            key = stripped.split("=", 1)[0].strip()
            if key in updates:
                out.append(f"{key}={updates[key]}")
                seen.add(key)
                continue
        out.append(line)
    for key, value in updates.items():
        if key not in seen:
            out.append(f"{key}={value}")
    ENV_PATH.write_text("\n".join(out) + "\n")


def _provision_user(cursor, user: str, host: str, password: str, grants: list) -> None:
    """Create/refresh a user with a fixed password and apply its grants."""
    # Username/host are identifiers (cannot be bound) — they come from our own
    # config; the password is bound as a parameter so the driver escapes it.
    cursor.execute(
        f"CREATE USER IF NOT EXISTS '{user}'@'{host}' IDENTIFIED BY %s", (password,)
    )
    cursor.execute(f"ALTER USER '{user}'@'{host}' IDENTIFIED BY %s", (password,))
    for grant in grants:
        cursor.execute(grant)


def main() -> None:
    env = _read_env()
    host = env.get("DB_HOST", "localhost")
    db = env.get("DB_NAME", "maintenance")

    ro_pass = env.get("DB_READONLY_PASSWORD") or secrets.token_urlsafe(24)
    wr_pass = env.get("DB_WRITE_PASSWORD") or secrets.token_urlsafe(24)

    conn = get_connection()  # admin
    try:
        cursor = conn.cursor()

        # Read-only: SELECT on the whole DB.
        _provision_user(
            cursor, READONLY_USER, host, ro_pass,
            [f"GRANT SELECT ON `{db}`.* TO '{READONLY_USER}'@'{host}'"],
        )

        # Write: SELECT everywhere (for validation/id reads) but INSERT/UPDATE on
        # ONLY the two mutable tables. No DELETE, no DDL, nothing on master data.
        _provision_user(
            cursor, WRITE_USER, host, wr_pass,
            [
                f"GRANT SELECT ON `{db}`.* TO '{WRITE_USER}'@'{host}'",
                f"GRANT INSERT, UPDATE ON `{db}`.incidents TO '{WRITE_USER}'@'{host}'",
                f"GRANT INSERT, UPDATE ON `{db}`.technician_schedule TO '{WRITE_USER}'@'{host}'",
            ],
        )

        cursor.execute("FLUSH PRIVILEGES")
        conn.commit()
        cursor.close()
    finally:
        conn.close()

    _write_env({
        "DB_READONLY_USER": READONLY_USER,
        "DB_READONLY_PASSWORD": ro_pass,
        "DB_WRITE_USER": WRITE_USER,
        "DB_WRITE_PASSWORD": wr_pass,
    })
    print(f"✅ Read-only user '{READONLY_USER}'@'{host}' — SELECT on `{db}`.")
    print(f"✅ Write user     '{WRITE_USER}'@'{host}' — INSERT/UPDATE on "
          f"`{db}`.incidents + technician_schedule only.")
    print(f"   Credentials written to {ENV_PATH}")


if __name__ == "__main__":
    main()
