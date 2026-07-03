"""
run_readonly_query — execute a single LLM-generated read-only SELECT, safely.

Validates the SQL (safety.validate_select_sql) and runs it on the SELECT-only
connection (safety.get_readonly_connection) — two independent layers, so a write
can never reach the data even if one layer were bypassed. Used by: Analytics (the
text-to-SQL agent — ad-hoc analytics the purpose-built tools don't cover).
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))  # .../mcp_server
from safety import validate_select_sql, get_readonly_connection


def run_readonly_query(sql: str) -> dict:
    """
    Run a single READ-ONLY SQL SELECT for an analytical question that the
    purpose-built tools don't already answer (e.g. counts, group-bys, cross-table
    summaries like "how many incidents are still open per machine"). Prefer the
    dedicated tools when one fits; use this only for ad-hoc analytics.

    Hard limits — write the SQL accordingly:
      - SELECT/WITH only; exactly one statement; no comments.
      - Never reference the phone column (PII; it is blocked). Use employee_id.
      - A LIMIT is auto-applied if you omit one.

    Args:
        sql: A single read-only SELECT (or WITH ... SELECT) statement.

    Returns:
        {ok: True, row_count, rows, sql_executed}      # success
        {ok: False, error, category: "validation"}     # rejected before running (fix the SQL and retry)
        {ok: False, error, category: "database"}        # ran but errored (e.g. bad column/syntax)
    """
    try:
        cleaned = validate_select_sql(sql)
    except ValueError as exc:
        return {"ok": False, "error": str(exc), "category": "validation"}

    conn = None
    try:
        conn = get_readonly_connection()
        cursor = conn.cursor(dictionary=True)
        cursor.execute(cleaned)
        rows = cursor.fetchall()
        cursor.close()
        for row in rows:
            # PII backstop: strip any `phone` column even if it arrived via
            # SELECT * (validate_select_sql blocks an explicit `phone` reference,
            # but not a wildcard projection).
            for key in [k for k in row if k.lower() == "phone"]:
                row.pop(key)
            # make dates/decimals JSON-friendly
            for key, value in row.items():
                if not isinstance(value, (str, int, float, bool, type(None))):
                    row[key] = str(value)
        return {
            "ok": True,
            "row_count": len(rows),
            "rows": rows,
            "sql_executed": cleaned,
        }
    except Exception as exc:
        return {"ok": False, "error": str(exc), "category": "database"}
    finally:
        if conn is not None:
            conn.close()


# === SELF-TEST — python mcp_server/mcp_tools/other/run_readonly_query.py ===
if __name__ == "__main__":
    import json

    cases = [
        "SELECT machine_id, status FROM machines WHERE status='Operational'",
        "SELECT COUNT(*) AS open_incidents FROM incidents WHERE incident_closure_date IS NULL",
        "DELETE FROM machines",                       # write -> validation
        "SELECT phone FROM employees",                # PII   -> validation
        "SELECT 1; DROP TABLE machines",              # stacked -> validation
    ]
    for sql in cases:
        print(f"\n>>> {sql}")
        print(json.dumps(run_readonly_query(sql), indent=2, default=str))

    # Defense-in-depth: a write is denied by the DB even if validation is bypassed.
    print("\n>>> [direct] INSERT on the read-only connection (MySQL should DENY):")
    conn = None
    try:
        conn = get_readonly_connection()
        cur = conn.cursor()
        cur.execute(
            "INSERT INTO machines (machine_id, mvc_code, status) "
            "VALUES ('ZZZ', 'MVC01', 'Idle')"
        )
        conn.commit()
        print("  ❌ WRITE SUCCEEDED — read-only user is misconfigured!")
    except Exception as exc:
        print(f"  ✅ denied by DB: {exc}")
    finally:
        if conn is not None:
            conn.close()
