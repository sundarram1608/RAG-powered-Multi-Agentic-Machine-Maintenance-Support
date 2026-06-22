"""
get_incident_history — past incidents for a machine (prior-case context).

Used by: Diagnosis ("has this happened before, and how was it fixed?").
PII-minimized: deliberately omits reported_by / technician_id (person ids aren't
needed for diagnosis).
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # .../mcp_tools
from _common import run_query


def get_incident_history(machine_id: str, limit: int = 5) -> list:
    """
    Retrieve a machine's past INCIDENTS (reported faults and how they were
    resolved) — the agent's prior-case memory for "has this happened before, and
    what fixed it?". Includes still-open incidents (NULL resolution/closure).
    Distinct from get_maintenance_history, which is routine servicing.

    Privacy: deliberately omits person identifiers (who reported it, which
    technician) — they are not needed to diagnose and must not enter the agent's
    reasoning context.

    Args:
        machine_id: Machine tag like "M01" (case-insensitive).
        limit: Max incidents to return, newest first (default 5).

    Returns (newest first):
        [{incident_id, reported_date, user_complaint, agent_root_cause,
          agentic_resolution, technician_comments, incident_closure_date}, ...]
        []   # no incident history
    """
    machine_id = (machine_id or "").strip().upper()
    rows = run_query(
        """
        SELECT incident_id, reported_date, user_complaint, agent_root_cause,
               agentic_resolution, technician_comments, incident_closure_date
        FROM incidents
        WHERE machine_id = %s
        ORDER BY reported_date DESC
        LIMIT %s
        """,
        (machine_id, int(limit)),
    )
    for row in rows:
        row["reported_date"] = str(row["reported_date"])
        row["incident_closure_date"] = (
            str(row["incident_closure_date"]) if row["incident_closure_date"] else None
        )
    return rows


# === SELF-TEST — python mcp_server/mcp_tools/read/get_incident_history.py ===
if __name__ == "__main__":
    import json

    print(json.dumps(get_incident_history("M01", 3), indent=2, default=str))
