"""
update_incident — record an incident's outcome and (by default) close it.

Scoped write: UPDATEs ONLY `technician_comments` and `incident_closure_date` on
`incidents`, via the least-privilege write user. The reported facts, root cause,
and resolution are immutable — this tool cannot touch them. Used by: Action.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # .../mcp_tools
from _common import run_query, run_write, REFERENCE_TODAY


def update_incident(incident_id: str, technician_comments: str,
                    close: bool = True) -> dict:
    """
    Update an OPEN incident's outcome — add the technician's comments and, when the
    work is done, close it. This is the ONLY way the workflow edits an incident, and
    it can touch ONLY the outcome fields (technician_comments, incident_closure_date)
    — the reported facts, root cause, and resolution are immutable.

    Args:
        incident_id: The incident to update, e.g. "inc_26".
        technician_comments: What was done to resolve it.
        close: If True (default), set the closure date to the system reference date
               and mark the incident closed; if False, just record the comments.

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

    if close:
        if incident[0]["incident_closure_date"] is not None:
            return {"ok": False, "error": f"Incident '{incident_id}' is already closed."}
        if not (technician_comments or "").strip():
            return {"ok": False, "error": "technician_comments is required to close."}
        run_write((
            "UPDATE incidents SET technician_comments=%s, incident_closure_date=%s "
            "WHERE incident_id=%s",
            (technician_comments, str(REFERENCE_TODAY), incident_id),
        ))
        return {"ok": True, "incident_id": incident_id, "status": "closed"}

    run_write((
        "UPDATE incidents SET technician_comments=%s WHERE incident_id=%s",
        (technician_comments, incident_id),
    ))
    return {"ok": True, "incident_id": incident_id, "status": "open"}


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

    # cleanup (admin)
    conn = get_connection(); cur = conn.cursor()
    cur.execute("DELETE FROM incidents WHERE incident_id=%s", (inc,))
    conn.commit(); cur.close(); conn.close()
    print(f"cleanup -> deleted {inc}")
