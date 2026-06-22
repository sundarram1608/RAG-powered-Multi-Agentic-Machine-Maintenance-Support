"""
create_incident — open a new incident record for a diagnosed fault.

Scoped write: INSERTs one row into `incidents` (the "create" fields only) via the
least-privilege write user. Leaves technician_id / work_date / work_slot /
technician_comments / incident_closure_date NULL — the incident starts OPEN.
Used by: Action.
"""

import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # .../mcp_tools
from _common import run_query, run_write, REFERENCE_TODAY


def _next_incident_id() -> str:
    """Generate the next inc_N id from the current max suffix."""
    rows = run_query("SELECT incident_id FROM incidents")
    max_n = 0
    for row in rows:
        try:
            max_n = max(max_n, int(row["incident_id"].split("_")[1]))
        except (IndexError, ValueError):
            continue
    return f"inc_{max_n + 1}"


def create_incident(machine_id: str, reported_by: str, user_complaint: str,
                    agent_root_cause: str, agentic_resolution: str) -> dict:
    """
    Open a NEW incident record for a diagnosed fault and return its id. Call this
    once the workflow has a confirmed complaint, root cause, and proposed
    resolution — it logs the case so a technician can be assigned and the fix
    tracked. The incident starts OPEN (no technician, comments, or closure yet).

    Args:
        machine_id: The faulting machine (must exist), e.g. "M01".
        reported_by: employee_id of the operator who reported it, e.g. "E01".
        user_complaint: The user's confirmed problem statement.
        agent_root_cause: The root cause the workflow identified.
        agentic_resolution: The fix the workflow proposes.

    Returns:
        {ok: True, incident_id, machine_id, status: "open"}
        {ok: False, error}   # e.g. unknown machine_id / reported_by
    """
    machine_id = (machine_id or "").strip().upper()
    reported_by = (reported_by or "").strip().upper()

    if not run_query("SELECT 1 FROM machines WHERE machine_id=%s", (machine_id,)):
        return {"ok": False, "error": f"Unknown machine '{machine_id}'."}
    if not run_query("SELECT 1 FROM employees WHERE employee_id=%s", (reported_by,)):
        return {"ok": False, "error": f"Unknown employee '{reported_by}'."}

    incident_id = _next_incident_id()
    run_write((
        """
        INSERT INTO incidents
            (incident_id, machine_id, reported_date, reported_time, reported_by,
             user_complaint, agent_root_cause, agentic_resolution)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
        """,
        (incident_id, machine_id, str(REFERENCE_TODAY),
         datetime.now().strftime("%H:%M:%S"), reported_by,
         user_complaint, agent_root_cause, agentic_resolution),
    ))
    return {"ok": True, "incident_id": incident_id, "machine_id": machine_id,
            "status": "open"}


# === SELF-TEST — python mcp_server/mcp_tools/write/create_incident.py ===
if __name__ == "__main__":
    import json
    from _common import get_connection

    print("unknown machine ->", create_incident("M99", "E01", "x", "y", "z"))

    result = create_incident(
        "M01", "E01", "[SELFTEST] bed not heating",
        "Thermistor fault", "Replace thermistor",
    )
    print("create ->", json.dumps(result, default=str))
    inc_id = result["incident_id"]

    row = run_query(
        "SELECT machine_id, reported_date, technician_id, incident_closure_date "
        "FROM incidents WHERE incident_id=%s", (inc_id,))
    print("row     ->", row[0], "(open)" if row[0]["incident_closure_date"] is None else "")

    # cleanup (admin)
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("DELETE FROM incidents WHERE incident_id=%s", (inc_id,))
    conn.commit(); cur.close(); conn.close()
    print(f"cleanup -> deleted {inc_id}")
