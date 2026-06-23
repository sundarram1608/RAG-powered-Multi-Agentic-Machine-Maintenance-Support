"""
update_incident — record an incident's outcome and (by default) close it.

Scoped write: UPDATEs ONLY `technician_comments`, `incident_closure_date`, and
(optionally) `technician_id` on `incidents`, via the least-privilege write user.
The reported facts, root cause, and resolution are immutable. Used by: Action,
Manage Incident, Self Action.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # .../mcp_tools
from _common import run_query, run_write, REFERENCE_TODAY


def update_incident(incident_id: str, technician_comments: str,
                    close: bool = True, assignee_id: str = None) -> dict:
    """
    Update an OPEN incident's outcome — add the technician's comments and, when the
    work is done, close it. It can touch ONLY the outcome fields
    (technician_comments, incident_closure_date, and — when assignee_id is given —
    technician_id); the reported facts, root cause, and resolution are immutable.

    Args:
        incident_id: The incident to update, e.g. "inc_26".
        technician_comments: What was done to resolve it.
        close: If True (default), set the closure date to the system reference date
               and mark the incident closed; if False, just record the comments.
        assignee_id: Optional — also set technician_id to this employee_id WITHOUT
               booking any schedule slot (used by Self Action to record the operator
               as the resolver). If None (default), technician_id is left untouched.

    Returns:
        {ok: True, incident_id, status: "closed" | "open"}
        {ok: False, error}   # unknown incident, or already closed
    """
    incident_id = (incident_id or "").strip()

    incident = run_query(
        "SELECT incident_closure_date FROM incidents WHERE incident_id=%s",
        (incident_id,),
    )
    if not incident:
        return {"ok": False, "error": f"Unknown incident '{incident_id}'."}
    if close and incident[0]["incident_closure_date"] is not None:
        return {"ok": False, "error": f"Incident '{incident_id}' is already closed."}
    if close and not (technician_comments or "").strip():
        return {"ok": False, "error": "technician_comments is required to close."}

    # Build the SET clause from only the fields this call touches.
    sets, params = ["technician_comments=%s"], [technician_comments]
    if close:
        sets.append("incident_closure_date=%s")
        params.append(str(REFERENCE_TODAY))
    if assignee_id:
        sets.append("technician_id=%s")
        params.append(assignee_id.strip().upper())
    params.append(incident_id)

    run_write((f"UPDATE incidents SET {', '.join(sets)} WHERE incident_id=%s", tuple(params)))
    return {"ok": True, "incident_id": incident_id,
            "status": "closed" if close else "open"}


# === SELF-TEST — python mcp_server/mcp_tools/write/update_incident.py ===
if __name__ == "__main__":
    import json
    from _common import get_connection
    from create_incident import create_incident

    inc = create_incident("M01", "E01", "[SELFTEST] close me", "rc", "res")["incident_id"]

    print("unknown      ->", update_incident("inc_does_not_exist", "x"))
    res = update_incident(inc, "Replaced thermistor, verified fix.")
    print("close        ->", json.dumps(res, default=str))
    row = run_query("SELECT technician_comments, incident_closure_date FROM incidents "
                    "WHERE incident_id=%s", (inc,))[0]
    print("  row:", row)
    print("re-close     ->", update_incident(inc, "again"))  # should reject

    # assignee_id (Self Action: operator as resolver, no booking)
    inc2 = create_incident("M01", "E01", "[SELFTEST] self-action", "rc", "res")["incident_id"]
    print("self-close   ->", update_incident(inc2, "Self-Action by the operator",
                                             close=True, assignee_id="E01"))
    row2 = run_query("SELECT technician_id, work_date, technician_comments, "
                     "incident_closure_date FROM incidents WHERE incident_id=%s", (inc2,))[0]
    print("  row:", row2, "(expect technician_id=E01, work_date=None)")

    # cleanup (admin)
    conn = get_connection(); cur = conn.cursor()
    cur.execute("DELETE FROM incidents WHERE incident_id IN (%s, %s)", (inc, inc2))
    conn.commit(); cur.close(); conn.close()
    print(f"cleanup -> deleted {inc}, {inc2}")
