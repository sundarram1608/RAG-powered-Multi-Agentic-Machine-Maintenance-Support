"""
get_incident — look up a single incident by its id (current state).

Used by: Manage Incident (confirm the incident exists, show its state for
approval, and find who to notify). Distinct from get_incident_history, which
lists a machine's incidents — this fetches ONE incident by id.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # .../mcp_tools
from _common import run_query


def get_incident(incident_id: str) -> dict:
    """
    Fetch one incident by id with its current state, for acting on a KNOWN incident.

    Use this to confirm an incident exists and see whether it is open or closed,
    which machine it is on, who reported it, and who (if anyone) is currently
    assigned — before closing, reassigning, or updating it.

    Args:
        incident_id: The incident id, e.g. "inc_26" (case-insensitive).

    Returns:
        {exists: True, incident_id, machine_id, status: "open"|"closed",
         reported_date, reported_by, user_complaint, agent_root_cause,
         agentic_resolution, technician_id, work_date, work_slot,
         technician_comments, incident_closure_date}
        {exists: False, incident_id}   # no such incident
    """
    incident_id = (incident_id or "").strip().lower()
    rows = run_query(
        """
        SELECT incident_id, machine_id, reported_date, reported_by, user_complaint,
               agent_root_cause, agentic_resolution, technician_id, work_date,
               work_slot, technician_comments, incident_closure_date
        FROM incidents WHERE incident_id = %s
        """,
        (incident_id,),
    )
    if not rows:
        return {"exists": False, "incident_id": incident_id}
    inc = rows[0]
    for key in ("reported_date", "work_date", "incident_closure_date"):
        inc[key] = str(inc[key]) if inc[key] is not None else None
    inc["status"] = "closed" if inc["incident_closure_date"] else "open"
    inc["exists"] = True
    return inc


# === SELF-TEST — python mcp_server/mcp_tools/read/get_incident.py ===
if __name__ == "__main__":
    import json

    print("inc_1  ->", json.dumps(get_incident("inc_1"), indent=2, default=str))
    print("inc_99 ->", get_incident("inc_99"))
