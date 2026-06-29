"""
list_incidents — browse incidents (open by default), optionally only the operator's.

Used by: Manage Incident, when the user wants to act on an incident but hasn't given
an id — list the open incidents so they can pick one. Supports a "my incidents" filter
(reported by OR assigned to a given employee). Distinct from get_incident (one id) and
get_incident_history (one machine's incidents).
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # .../mcp_tools
from _common import run_query


def list_incidents(status: str = "open", employee_id: str | None = None) -> list:
    """
    List incidents so a user can pick one to act on, newest-id last.

    Use this when the user wants to manage an incident but did not name an id — show
    them the open incidents to choose from. Pass employee_id to show only "their"
    incidents (ones they reported OR are assigned to as the technician).

    Args:
        status: "open" (default) -> not-yet-closed incidents; "closed" -> resolved
            ones; "all" -> both.
        employee_id: optional, e.g. "E01". When given, returns only incidents where
            this employee is the reporter OR the assigned technician ("my incidents").

    Returns:
        A list of incidents (no PII — never phone/email; employee_ids are not PII).
        Each item: {incident_id, machine_id, status: "open"|"closed", reported_date,
        summary, reported_by, technician_id} where summary is the user's complaint and
        reported_by / technician_id are the reporter / assigned-technician employee ids
        (technician_id may be None). CLOSED incidents additionally include
        {agent_root_cause, agent_suggested_action, technician_action} so the user sees
        what the agent diagnosed/suggested and what the technician actually did.
    """
    status = (status or "open").strip().lower()
    where, params = [], []
    if status == "open":
        where.append("incident_closure_date IS NULL")
    elif status == "closed":
        where.append("incident_closure_date IS NOT NULL")
    # "all" -> no status filter
    if employee_id:
        eid = employee_id.strip().upper()
        where.append("(UPPER(reported_by) = %s OR UPPER(technician_id) = %s)")
        params += [eid, eid]
    clause = (" WHERE " + " AND ".join(where)) if where else ""
    rows = run_query(
        f"""
        SELECT incident_id, machine_id, reported_date, user_complaint,
               reported_by, technician_id, agent_root_cause, agentic_resolution,
               technician_comments, incident_closure_date
        FROM incidents{clause}
        ORDER BY incident_id
        """,
        tuple(params),
    )
    out = []
    for r in rows:
        closed = r["incident_closure_date"] is not None
        item = {
            "incident_id": r["incident_id"],
            "machine_id": r["machine_id"],
            "status": "closed" if closed else "open",
            "reported_date": str(r["reported_date"]) if r["reported_date"] else None,
            "summary": r["user_complaint"],
            "reported_by": r["reported_by"],      # employee id (not PII) — for ownership
            "technician_id": r["technician_id"],  # assigned employee id, or None
        }
        if closed:
            item["agent_root_cause"] = r["agent_root_cause"]
            item["agent_suggested_action"] = r["agentic_resolution"]
            item["technician_action"] = r["technician_comments"]
        out.append(item)
    return out


# === SELF-TEST — python mcp_server/mcp_tools/read/list_incidents.py ===
if __name__ == "__main__":
    import json

    print("open ->", json.dumps(list_incidents(), indent=2, default=str)[:600])
    print("\nmine (E01) ->", json.dumps(list_incidents(employee_id="E01"), default=str)[:400])
    print("\nclosed[0] ->", json.dumps(list_incidents(status="closed")[:1], indent=2, default=str))
