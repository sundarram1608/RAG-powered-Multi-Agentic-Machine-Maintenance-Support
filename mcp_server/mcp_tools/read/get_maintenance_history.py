"""
get_maintenance_history — recent preventive services for a machine.

Used by: Diagnosis (recent preventive maintenance service context).
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # .../mcp_tools
from _common import run_query


def get_maintenance_history(machine_id: str, limit: int = 5) -> list:
    """
    Retrieve a machine's recent PREVENTIVE-maintenance service records (routine
    SOP servicing — not fault repairs; for past faults use get_incident_history).
    Use it to understand how recently and how regularly the machine was serviced
    when assessing a current problem.

    Args:
        machine_id: Machine tag like "M01" (case-insensitive).
        limit: Max records to return, newest first (default 5).

    Returns (newest first):
        [{service_id, service_date, performed_by, technician_comments}, ...]
        []   # machine has no service history
    """
    machine_id = (machine_id or "").strip().upper()
    rows = run_query(
        """
        SELECT service_id, service_date, performed_by, technician_comments
        FROM maintenance_history
        WHERE machine_id = %s
        ORDER BY service_date DESC
        LIMIT %s
        """,
        (machine_id, int(limit)),
    )
    for row in rows:
        row["service_date"] = str(row["service_date"])
    return rows


# === SELF-TEST — python mcp_server/mcp_tools/read/get_maintenance_history.py ===
if __name__ == "__main__":
    import json

    print(json.dumps(get_maintenance_history("M01", 3), indent=2, default=str))
